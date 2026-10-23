"""不用不可搬动的 venv shebang；由包内 Python 加载冻结的应用依赖。"""
from pathlib import Path
import site
import sys

root = Path(__file__).resolve().parents[1]
site.addsitedir(str(root / "platform/litellm/site-packages"))
from litellm.proxy.proxy_cli import run_server

sys.exit(run_server())
