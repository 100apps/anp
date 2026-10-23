"""包内 Python 加载冻结依赖；业务源码仍在可复制的 business/。"""
from pathlib import Path
import site
import sys

root = Path(__file__).resolve().parents[1]
site.addsitedir(str(root / "platform/business/site-packages"))
sys.path.insert(0, str(root / "business/src"))
from anp_business.__main__ import main

main()
