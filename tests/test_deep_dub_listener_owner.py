"""真实内核监听表检查及故障拒绝；不以HTTP健康响应推断PID。"""
import ctypes
import os
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from apps.deep_dub.services.listener_owner import DWORD, _owners, _Row4, listener_owners
from apps.deep_dub.services.model_pool import OwnedModelServer


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows IP Helper真实检查')
@pytest.mark.parametrize('family,address', [(socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')])
def test_real_listener_and_release(family, address):
    with socket.socket(family) as server:
        server.bind((address, 0)); server.listen()
        port = server.getsockname()[1]
        assert listener_owners(port) == {os.getpid()}
        owned = OwnedModelServer(SimpleNamespace(port=port), None, '.')
        owned.process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        assert owned.owns_listener()
        owned.process = SimpleNamespace(pid=os.getpid() + 999999, poll=lambda: None)
        assert not owned.owns_listener()
        owned.process = SimpleNamespace(pid=os.getpid(), poll=lambda: 0)
        assert not owned.owns_listener()
    assert listener_owners(port) == set()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows IP Helper真实子进程检查')
def test_child_listener_is_not_parent_or_health_claim():
    child = subprocess.Popen([sys._base_executable, '-u', '-c',
        "import socket,time;s=socket.socket();s.bind(('127.0.0.1',0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(60)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        port = int(child.stdout.readline())
        assert listener_owners(port) == {child.pid}
        owned = OwnedModelServer(SimpleNamespace(port=port), None, '.')
        owned.process = child
        assert owned.owns_listener()
    finally:
        child.terminate(); child.wait(timeout=10)
        child.stdout.close(); child.stderr.close()
    assert not owned.owns_listener()
    assert listener_owners(port) == set()


def test_native_failures_and_truncated_table_fail_closed():
    def denied(*args): return 5
    with pytest.raises(OSError): _owners(denied, socket.AF_INET, _Row4, 18112)
    calls = []
    def truncated(buffer, size, *args):
        calls.append(1)
        ctypes.cast(size, ctypes.POINTER(DWORD)).contents.value = 4
        if buffer is None: return 122
        DWORD.from_buffer(buffer).value = 1
        return 0
    with pytest.raises(OSError): _owners(truncated, socket.AF_INET, _Row4, 18112)
    assert len(calls) == 2
    def growing(buffer, size, *args):
        ctypes.cast(size, ctypes.POINTER(DWORD)).contents.value = 100
        return 122
    with pytest.raises(OSError): _owners(growing, socket.AF_INET, _Row4, 18112)
