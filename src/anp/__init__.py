"""ANP 公共应用宿主。"""
from pathlib import Path
import os
import sys
import asyncio

if sys.platform == 'win32':
    # psycopg 异步连接需要 SelectorEventLoop；项目固定使用 Python 3.12。
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ROOT = Path(os.environ.get('ANP_ROOT', Path(__file__).resolve().parents[2])).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
