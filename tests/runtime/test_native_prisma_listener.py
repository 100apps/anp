"""内部动态端口只允许确切的包内 Prisma 引擎与受管进程组。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_query_engine_listener_requires_both_exact_binary_and_litellm_group(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('prisma_port_check', ROOT/'native/platform_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    expected = tmp_path/'var/cache/elf/platform/litellm/engines/query-engine.elf'
    monkeypatch.setattr(module.os, 'readlink', lambda _: str(expected))
    assert module.owned_prisma_engine(tmp_path, 12, 10, 10)
    assert not module.owned_prisma_engine(tmp_path, 12, 11, 10)
    monkeypatch.setattr(module.os, 'readlink', lambda _: '/foreign/query-engine.elf')
    assert not module.owned_prisma_engine(tmp_path, 12, 10, 10)
