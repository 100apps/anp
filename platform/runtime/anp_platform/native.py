"""原生平台入口，记录可验证的进程身份并保证常规退出执行状态交接。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from .supervisor import main as serve


def reloading():
    from watchfiles import watch,PythonFilter
    root=Path(os.environ['ANP_ROOT']);stopping=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stopping.set())
    def start():
        return subprocess.Popen([sys.executable,'-m','anp_platform.native','--child'])
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=35)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    process=start()
    try:
        for changes in watch(root/'platform/runtime',root/'src/anp',root/'apps',
            watch_filter=PythonFilter(),stop_event=stopping,force_polling=True,poll_delay_ms=750,debounce=500):
            print('ANP_NATIVE_RELOAD',len(changes),flush=True)
            stop(process)
            if not stopping.is_set():process=start()
    finally:stop(process)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--debug-port',type=int);parser.add_argument('--wait-for-client',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--reload',action='store_true');parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.child:serve();return
    if args.check:
        from .bridges import Bridges
        settings=json.loads(Path(os.environ['ANP_PLATFORM_CONFIG']).read_text())
        async def check():
            bridges=Bridges();await bridges.start(settings['host_bridges'])
            try:
                for port,path in [(19081,'/health'),(19082,'/health'),(19574,'/workbench/')]:
                    reader,writer=await asyncio.open_connection('127.0.0.1',port)
                    writer.write(f'GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'.encode());await writer.drain()
                    data=await asyncio.wait_for(reader.read(),20)
                    writer.close()
                    if not data.startswith(b'HTTP/1.1 200'):raise RuntimeError('本机业务回调检查失败')
            finally:await bridges.close()
        asyncio.run(check());print('ANP_NATIVE_CALLBACKS_READY');return
    runtime=Path('/run/anp');runtime.mkdir(parents=True,exist_ok=True)
    marker=runtime/'native.pid.json'
    if marker.exists():
        previous=json.loads(marker.read_text());pid=previous['pid']
        command=Path(f'/proc/{pid}/cmdline')
        if command.exists() and b'anp_platform.native' in command.read_bytes():raise RuntimeError('原生平台已经运行')
    marker.write_text(json.dumps({'pid':os.getpid(),'workspace':os.environ['ANP_ROOT']}))
    def stop_waiting(*_):raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop_waiting)
    try:
        if args.debug_port:
            import debugpy
            debugpy.listen(('127.0.0.1',args.debug_port))
            print('ANP_NATIVE_DEBUG_READY',flush=True)
            if args.wait_for_client:debugpy.wait_for_client()
        if args.reload:reloading()
        else:serve()
    finally:
        if marker.exists() and json.loads(marker.read_text())['pid']==os.getpid():marker.unlink()


if __name__=='__main__':main()
