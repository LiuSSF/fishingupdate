# -*- coding: utf-8 -*-
"""
gh_autoupdater.py — GitHub 自动更新器（最新版）
兼容 Windows + PyInstaller 打包
用法示例：
    gh_autoupdater.exe ^
      --current 1.0.0 ^
      --latest-url https://raw.githubusercontent.com/LiuSSF/fishingupdate/main/latest.json ^
      --target-dir "D:\python\test_app" ^
      --restart "app.exe" ^
      --interval 60
"""

import argparse, os, sys, time, tempfile, json, hashlib
from urllib.parse import urlparse
from typing import Optional, Tuple

# =============== 工具函数 ===============

def _need_requests() -> bool:
    try:
        import requests  # noqa
        return True
    except Exception:
        print("[UPDATE] 缺少 requests，请先：pip install requests")
        return False

def _http_get(url: str, timeout: int = 10):
    import requests
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r

def _download_stream(url: str, dst_path: str, timeout: int = 30):
    import requests
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dst_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def _parse_latest(body: str, content_type: str, latest_url: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    支持两种格式：
      - JSON: {"version":"1.0.1","url":"https://...zip","sha256":"..."}
      - 文本: "1.0.1"
    """
    ct = (content_type or "").lower()
    text = body.strip()
    if "application/json" in ct or (text.startswith("{") and text.endswith("}")):
        data = json.loads(text)
        ver = str(data.get("version", "")).strip()
        url = str(data.get("url", "")).strip() or None
        sha = str(data.get("sha256", "")).strip() or None
        return ver, url, sha
    return text, None, None

def _default_zip_url(latest_url: str, ver: str) -> str:
    parsed = urlparse(latest_url)
    base = "/".join(parsed.path.split("/")[:-1])
    return f"https://{parsed.netloc}{base}/releases/fisher_v{ver}.zip"

def _make_update_bat(tmpdir: str, unpack_dir: str, target_dir: str, restart_cmd: Optional[str]) -> str:
    bat = os.path.join(tmpdir, "update.bat")
    restart_line = ""
    if restart_cmd:
        restart_line = f"start \"\" {restart_cmd}\n"
    with open(bat, "w", encoding="utf-8") as f:
        f.write(f"""@echo off
echo [UPDATE] 等待主进程退出...
timeout /t 2 /nobreak >nul
echo [UPDATE] 覆盖新文件到：{target_dir}
xcopy /E /Y "{unpack_dir}\\*" "{target_dir}" >nul
if %errorlevel% neq 0 echo [UPDATE] 复制时出错，请检查权限。
echo [UPDATE] 启动程序...
{restart_line}echo [UPDATE] 清理临时目录...
rd /s /q "{tmpdir}" 2>nul
exit
""")
    return bat

def _apply_update(zip_url: str, target_dir: str, sha256_expect: Optional[str], restart_cmd: Optional[str]):
    print(f"[UPDATE] 下载更新包：{zip_url}")
    tmpdir = tempfile.mkdtemp(prefix="ghupd_")
    zip_path = os.path.join(tmpdir, "update.zip")
    _download_stream(zip_url, zip_path)

    if sha256_expect:
        got = _sha256_file(zip_path)
        if got.lower() != sha256_expect.lower():
            print(f"[UPDATE] 校验失败：期望 {sha256_expect}，实际 {got}")
            print("[UPDATE] 取消更新。")
            return

    import zipfile
    unpack = os.path.join(tmpdir, "extracted")
    os.makedirs(unpack, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(unpack)

    print("[UPDATE] 生成更新脚本并执行...")
    bat = _make_update_bat(tmpdir, unpack, target_dir, restart_cmd)
    os.startfile(bat)  # 启动更新脚本
    os._exit(0)        # 当前进程退出以释放文件锁

# =============== 核心逻辑 ===============

def check_once(current: str, latest_url: str, target_dir: str, restart_cmd: Optional[str], insecure: bool):
    if not _need_requests():
        return
    try:
        r = _http_get(latest_url, timeout=8)
        version, dl_url, sha256v = _parse_latest(r.text, r.headers.get("content-type", ""), latest_url)
        if not version:
            print("[UPDATE] latest.json 无版本号，跳过。")
            return
        if version == current:
            print(f"[UPDATE] 已是最新版本：{current}")
            return

        if not dl_url:
            dl_url = _default_zip_url(latest_url, version)
        if insecure:
            sha256v = None

        print(f"[UPDATE] 发现新版本 {version}（当前 {current}）")
        _apply_update(dl_url, target_dir, sha256v, restart_cmd)
    except Exception as e:
        print("[UPDATE] 检测失败：", e)

def loop(current: str, latest_url: str, target_dir: str, restart_cmd: Optional[str], interval: int, insecure: bool):
    interval = max(5, int(interval))
    while True:
        check_once(current, latest_url, target_dir, restart_cmd, insecure)
        time.sleep(interval)

def main():
    ap = argparse.ArgumentParser(description="GitHub 自动更新器（独立版）")
    ap.add_argument("--current", required=True, help="当前版本号，如 1.0.0")
    ap.add_argument("--latest-url", required=True, help="latest.json 的 raw 直链")
    ap.add_argument("--target-dir", required=True, help="覆盖更新的目标目录")
    ap.add_argument("--restart", default=None, help='更新后重启命令，例如 "app.exe" 或 "python app.py"')
    ap.add_argument("--interval", type=int, default=60, help="检测间隔秒（默认60）")
    ap.add_argument("--once", action="store_true", help="仅检测一次后退出")
    ap.add_argument("--insecure", action="store_true", help="跳过 SHA256 校验（不建议）")
    args = ap.parse_args()

    args.target_dir = os.path.abspath(args.target_dir)
    if not os.path.isdir(args.target_dir):
        print(f"[UPDATE] 目标目录不存在：{args.target_dir}")
        sys.exit(1)

    if args.once:
        check_once(args.current, args.latest_url, args.target_dir, args.restart, args.insecure)
    else:
        loop(args.current, args.latest_url, args.target_dir, args.restart, args.interval, args.insecure)

if __name__ == "__main__":
    main()
