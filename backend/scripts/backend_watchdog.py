"""后端看护进程：静默运行（pythonw），循环拉起 uvicorn。

由 Windows 计划任务「WeiwolianghuaBackend」在登录时启动：
- 无控制台窗口（不可被误关）
- uvicorn 意外退出 3 秒后自动重启
- 重启电脑后随登录自动恢复

注意：pythonw 环境下 sys.stdout 为 None，uvicorn 写控制台会崩溃，
因此子进程的 stdout/stderr 必须重定向到日志文件（data/logs/uvicorn.out）。
停止方式：任务计划程序删除该任务，或任务管理器结束 pythonw.exe。
"""
import subprocess
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BACKEND_DIR.parent / "data" / "logs" / "uvicorn.out"


def main() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    while True:
        with open(LOG_FILE, "ab", buffering=0) as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", "8000"],
                cwd=BACKEND_DIR,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            proc.wait()
        time.sleep(3)  # 意外退出 → 重启


if __name__ == "__main__":
    main()
