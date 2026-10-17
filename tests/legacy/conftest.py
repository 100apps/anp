"""旧 V2 验收只在显式选择 legacy 栈时执行，避免误操作旧 anp2。"""
import os
from pathlib import Path
import pytest


def pytest_collection_modifyitems(items):
    if os.environ.get('ANP_STACK')=='legacy':return
    directory=Path(__file__).resolve().parent
    for item in items:
        if directory in item.path.resolve().parents:
            item.add_marker(pytest.mark.skip(reason='V2 历史验收；只有 ANP_STACK=legacy 才运行'))
