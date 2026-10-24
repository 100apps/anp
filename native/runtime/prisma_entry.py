"""由包内 Python 执行固定 Prisma Python 生成器或 CLI。"""
from pathlib import Path
import site
import sys

root = Path(__file__).resolve().parents[1]
site.addsitedir(str(root / "platform/litellm/site-packages"))
from prisma.cli import main

main()
