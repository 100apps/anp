"""供 IDE 调试的原生命令入口，不启动第二套调试器。"""
import os
from pathlib import Path
import signal
import sys

from langgraph_cli.cli import cli

def configure_windows_reload():
    if os.name != 'nt':
        return
    from uvicorn._subprocess import get_subprocess
    from uvicorn.supervisors.basereload import BaseReload

    def restart(self):
        # Windows+debugpy后台控制台可能无限等待CTRL_C；仅给本开发子进程增加退出上限。
        self.is_restarting = True
        try:
            os.kill(self.process.pid, signal.CTRL_C_EVENT)
        except OSError:
            pass
        sys.stdout.write(' '); sys.stdout.flush()
        self.process.join(timeout=8)
        if self.process.is_alive():
            self.process.terminate(); self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill(); self.process.join(timeout=5)
        self.is_restarting = False
        self.process = get_subprocess(config=self.config, target=self.target, sockets=self.sockets)
        self.process.start()

    BaseReload.restart = restart


if __name__ == "__main__":
    configure_windows_reload()
    os.chdir(Path(__file__).resolve().parents[1] / "business")
    args = ["dev", "--no-browser", "--host", "127.0.0.1", "--port", os.getenv("BUSINESS_PORT", "20260")]
    if port := os.getenv("ANP_DEBUG_PORT"):
        # Windows debugpy 会在事件循环中规范化源码路径，触发 blocker 的文件系统检测。
        # 只在显式调试模式使用官方开发开关，普通 dev 仍保留阻塞检查。
        args.extend(["--debug-port", port, "--allow-blocking"])
    cli(args)
