"""先确认 Windows 回环端口可达，再通知 IDE 附加 WSL 调试器。"""
from pathlib import Path
import subprocess
import sys
import time


HOST_READY = 'ANP_NATIVE_HOST_DEBUG_READY'


def endpoint_listening(port):
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('调试端口范围不正确')
    # 只读监听表，避免探测连接占用调试器的第一个客户端会话。
    if sys.platform == 'win32':
        expression = (f"if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort {port} "
                      "-State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', expression],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    if sys.platform == 'linux':
        return any(row.split()[1] == f'0100007F:{port:04X}' and row.split()[3] == '0A'
                   for row in Path('/proc/net/tcp').read_text().splitlines()[1:])
    raise RuntimeError('原生调试就绪检查仅支持 Windows 与 Linux')


def wait_endpoint(process, port, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError('原生调试进程已退出，不能附加')
        if endpoint_listening(port):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('宿主机调试端口等待超时；原生进程保留，请检查 WSL 转发或执行 stop-native')
        time.sleep(.1)


def relay(command, port):
    print('ANP_NATIVE_DEBUG_STARTING', flush=True)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True,
                               encoding='utf8', errors='replace', bufsize=1)
    with process.stdout:
        for line in process.stdout:
            if line.strip() == 'ANP_NATIVE_DEBUG_READY':
                wait_endpoint(process, port)
                print(HOST_READY, flush=True)
            else:
                print(line, end='', flush=True)
    return process.wait()
