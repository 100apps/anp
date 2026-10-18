"""模拟 WSL 已就绪但 Windows 端口稍后才出现的真实 TCP 时序。"""
import socket
import sys

import pytest

from anp.dev.native_debug import HOST_READY, relay


def test_host_ready_waits_for_delayed_loopback_listener(capsys):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    child = f"""
import socket,time
print('ANP_NATIVE_DEBUG_READY',flush=True)
time.sleep(.2)
with socket.socket() as server:
    server.bind(('127.0.0.1',{port}))
    server.listen()
    server.settimeout(3)
    try:
        connection,_=server.accept()
    except TimeoutError:
        pass
    else:
        connection.close()
        raise SystemExit('就绪检查不应消耗调试客户端连接')
print('原生进程正常退出',flush=True)
"""
    assert relay([sys.executable, '-u', '-c', child], port) == 0
    output = capsys.readouterr().out
    assert output.splitlines() == ['ANP_NATIVE_DEBUG_STARTING', HOST_READY, '原生进程正常退出']


def test_dead_native_process_cannot_report_host_ready(capsys):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
        child = "print('ANP_NATIVE_DEBUG_READY',flush=True);raise SystemExit(3)"
        with pytest.raises(RuntimeError, match='已退出'):
            relay([sys.executable, '-u', '-c', child], port)
    assert HOST_READY not in capsys.readouterr().out
