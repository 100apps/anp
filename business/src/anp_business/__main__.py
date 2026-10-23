"""通过 LangGraph 内置 Agent Server 启动可复制的业务目录。"""
import os
import json
import argparse

from anp_business.shared.config import BUSINESS_ROOT, configuration
from pathlib import Path


def main():
    from dotenv import load_dotenv
    load_dotenv(BUSINESS_ROOT / ".env")
    config = configuration()
    parser = argparse.ArgumentParser(description="ANP Business：LangGraph Agent Server")
    parser.add_argument("--host", default=config["server"]["host"])
    parser.add_argument("--port", type=int, default=config["server"]["port"])
    args = parser.parse_args()
    runtime = Path(os.getenv("ANP_BUSINESS_DATA_ROOT", str(BUSINESS_ROOT / "var"))).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    # 框架将 .langgraph_api 写在 cwd；固定到实例 var/，不修改不可变源码。
    os.chdir(runtime)
    spec = json.loads((BUSINESS_ROOT / "langgraph.json").read_text())
    def absolute(value):
        path, attribute = value.rsplit(":", 1)
        return str((BUSINESS_ROOT / path).resolve()) + ":" + attribute
    from langgraph_api.cli import run_server
    run_server(args.host, args.port, False,
               {name: absolute(path) for name, path in spec["graphs"].items()},
               open_browser=False, env=None,
               auth={**spec["auth"], "path": absolute(spec["auth"]["path"])},
               http={**spec["http"], "app": absolute(spec["http"]["app"])})


if __name__ == "__main__":
    main()
