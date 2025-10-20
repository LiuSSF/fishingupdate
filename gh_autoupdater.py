# -*- coding: utf-8 -*-
"""
gh_autoupdater.py — 独立自动更新器（Windows）
用法示例：
  python gh_autoupdater.py ^
      --current 1.0.0 ^
      --latest-url https://raw.githubusercontent.com/<user>/<repo>/main/latest.json ^
      --target-dir "D:\python\albion\albion_fisher1024" ^
      --restart "python app.py" ^
      --interval 60

参数说明：
  --current     当前版本号（字符串比较，不做语义版本解析）
  --latest-url  latest.json 或 version.txt 的 raw 直链
  --target-dir  要被覆盖更新的目标目录（你的程序所在目录）
  --restart     （可选）更新后如何重启程序，如 "python app.py" 或 "MyApp.exe"
  --interval    （可选）检测间隔秒数，默认 60
  --once        （可选）只检测一次就退出（用于手动测试）
  --insecure    （可选）跳过 SHA256 校验（不建议，默认会校验若 latest.json 提供 sha256）
"""

import argparse, os, sys, time, tempfile, json, hashlib, shlex, subprocess
from urllib.parse import urlparse
from typing import Optional

def _need_requests():
    try:
        import requests  # noqa
        return True
    except Exception:
        print("[UPDATE] 缺少依赖：requests。请先：pip install requests")
        return False

def _http_get(url, timeout=10):
    import requests
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def _download_file(url: str, dst_path: str, timeout=30):
    import requests
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dst_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

def _parse_latest_payload(body: str, content_type: str, latest_url: str) -> tuple[str, Optional[str], Optional[str]]:
    """
    返回：(remote_version, download_url, sha256)
    兼容两种格式：
      1) JSON: {"version":"1.2.3","url":"https://.../zip","sha256":"..."}
      2) 纯文本: "1.2.3"  —— 此时 download_url 为空，调用方按规则拼
    """
    ct = (content_type or "").lower()
    if "application/json" in ct or body.strip().startswith("{"):
        data = json.loads(body)
        ver = str(data.get("version", "")).strip()
        url = str(data.get("url", "")).strip() or None
        sha = str(data.get("sha256", "")).strip() or None
        return ver, url, sha
    else:
        return body.strip(), None, None

def _default_zip_url_from_latest(latest_url: str, remote_version: str) -> str:
    parsed = urlparse(latest_url)
    base = "/".join(parsed.path.split("/")[:-1])
    return f"https://{parsed.netloc}{base}/releases/fisher_v{remote_version}.zip"

def _make_update_bat(tmpdir: str, extract_dir: str, target_dir: str, restart_cmd: Optional[str]) -> str:
    bat_path = os.path.join(tmpdir, "update.bat")
    # 构造重启命令
    restart_line = ""
    if restart_cmd:
        # 通过 start "" <cmd> 形式启动；如果包含空格，建议传完整的一行命令字符串
        restart_line = f'start "" {restart_cmd}\n'

    with open(bat_path, "w", encoding="utf-8") as bf:
        bf.write(f"""@echo off
echo [UPDATE] 等待主进程退出...
timeout /t 2 /nobreak >nul
echo [UPDATE] 覆盖新文件到：{target_dir}
xcopy /E /Y "{extract_dir}\\*" "{target_dir}" >nul
if %errorlevel% neq 0 echo [UPDATE] xcopy 可能报错，请手动检查。
echo [UPDATE] 重启程序...
{restart_line}echo [UPDATE] 清理临时目录...
rd /s /q "{tmpdir}" 2>nul
exit
""")
    return bat_path

def _apply_update(download_url: str, target_dir: str, expect_sha256: Optional[str], restart_cmd: Optional[str]):
    print(f"[UPDATE] 下载更新包：{download_url}")
    tmpdir = tempfile.mkdtemp(prefix="gh_update_")
    zip_path = os.path.join(tmpdir, "update.zip")
    _download_file(download_url, zip_path)

    if expect_sha256:
        got = _sha256_file(zip_path)
        if got.lower() != expect_sha256.lower():
            print(f"[UPDATE] 校验失败：期望 {expect_sha256}，实际 {got}")
            print("[UPDATE] 已取消更新。")
            return

    print("[UPDATE] 解压中...")
    import zipfile
    extract_dir = os.path.join(tmpdir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    print("[UPDATE] 生成更新脚本并执行...")
    bat = _make_update_bat(tmpdir, extract_dir, target_dir, restart_cmd)
    # 以 Explorer 启动（非阻塞），然后当前进程退出释放文件锁
    os.startfile(bat)
    os._exit(0)

def check_once(current: str, latest_url: str, target_dir: str, restart_cmd: Optional[str], insecure: bool):
    if not _need_requests():
        return
    try:
        r = _http_get(latest_url, timeout=8)
        remote_version, download_url, sha256 = _parse_latest_payload(r.text, r.headers.get("content-type", ""), latest_url)
        if not remote_version:
            print("[UPDATE] latest.json/version.txt 无版本号，忽略。")
            return
        if remote_version == current:
            print(f"[UPDATE] 已是最新：{current}")
            return

        if not download_url:
            download_url = _default_zip_url_from_latest(latest_url, remote_version)

        if insecure:
            sha256 = None  # 显式跳过校验

        print(f"[UPDATE] 发现新版本：{remote_version}（当前 {current}）")
        _apply_update(download_url, target_dir, sha256, restart_cmd)
    except Exception as e:
        print("[UPDATE] 检测失败：", e)

def loop(current: str, latest_url: str, target_dir: str, restart_cmd: Optional[str], interval: int, insecure: bool):
    while True:
        check_once(current, latest_url, target_dir, restart_cmd, insecure)
        time.sleep(max(5, int(interval)))

def main():
    ap = argparse.ArgumentParser(description="GitHub 自动更新器（独立测试版）")
    ap.add_argument("--current", required=True, help="当前版本号，如 1.3.4")
    ap.add_argument("--latest-url", required=True, help="latest.json 或 version.txt 的 GitHub raw 直链")
    ap.add_argument("--target-dir", required=True, help="要覆盖更新的目标目录（你的程序根目录）")
    ap.add_argument("--restart", default=None, help='更新后重启命令，如 "python app.py" 或 "MyApp.exe"')
    ap.add_argument("--interval", type=int, default=60, help="检测间隔秒数（默认 60）")
    ap.add_argument("--once", action="store_true", help="仅检测一次就退出（用于测试）")
    ap.add_argument("--insecure", action="store_true", help="跳过 SHA256 校验（不建议）")
    args = ap.parse_args()

    # 规范路径
    args.target_dir = os.path.abspath(args.target_dir)
    if not os.path.isdir(args.target_dir):
        print(f"[UPDATE] 目标目录不存在：{args.target_dir}")
        sys.exit(1)

    # restart 命令写入 .bat 时不再额外转义，让你自行传入合适的字符串（含引号）
    if args.once:
        check_once(args.current, args.latest_url, args.target_dir, args.restart, args.insecure)
    else:
        loop(args.current, args.latest_url, args.target_dir, args.restart, args.interval, args.insecure)

if __name__ == "__main__":
    main()
