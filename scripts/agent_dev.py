"""供 IDE 调试的原生命令入口，不启动第二套调试器。"""
import os
from pathlib import Path

from langgraph_cli.cli import cli

if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parents[1] / "business")
    args = ["dev", "--no-browser", "--host", "127.0.0.1", "--port", os.getenv("BUSINESS_PORT", "20240")]
    if port := os.getenv("ANP_DEBUG_PORT"):
        # Windows debugpy 会在事件循环中规范化源码路径，触发 blocker 的文件系统检测。
        # 只在显式调试模式使用官方开发开关，普通 dev 仍保留阻塞检查。
        args.extend(["--debug-port", port, "--allow-blocking"])
    cli(args)
