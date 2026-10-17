"""真实启动 Vite 并验证两次 HMR；不伪造 API，也不代替 IDE 断点或平台网关验收。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit,quote
from uuid import uuid4

import httpx
from websockets.sync.client import connect

from anp import ROOT
from anp.dev import frontend


def wait_update(stream):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        message=json.loads(stream.recv(timeout=max(.1,deadline-time.monotonic())))
        if message.get('type')=='error':raise RuntimeError('Vite 报告源码处理错误')
        if message.get('type')!='update':continue
        for update in message['updates']:
            path=urlsplit(update['path']).path
            if path.removeprefix('/workbench')=='/src/app.tsx':
                assert update['type']=='js-update'
                return update
    raise RuntimeError('未收到目标源码的 HMR 更新')


def verify():
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
    os.environ.update(ANP_UI_PORT=str(port),VITE_BASE_PATH='/workbench/',VITE_PUBLIC_PORT=str(port))
    os.environ['ANP_VITE_CACHE_DIR']=str(ROOT/'.local/vite-runtime-cache'/uuid4().hex)
    os.environ.setdefault('ANP_DEV_HOST','127.0.0.1')
    original_popen=subprocess.Popen;created=[]
    def launch(*args,**kwargs):
        process=original_popen(*args,**kwargs);created.append(process);return process
    source=ROOT/'console/src/app.tsx'
    original=source.read_bytes()
    temporary=original+('\n// ANP HMR 验收 '+uuid4().hex+'\n').encode()
    modified=False;report={}
    try:
        started=time.monotonic()
        with patch.object(frontend.subprocess,'Popen',launch):
            frontend.ensure();frontend.ensure()
        assert len(created)==1 and created[0].poll() is None,'重复前置调用未保持单一进程'
        startup=time.monotonic()-started
        base=f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=base,trust_env=False,timeout=15) as client:
            page=client.get('/workbench/');page.raise_for_status()
            assert 'ANP 工作台' in page.text
            code=client.get('/workbench/src/app.tsx');code.raise_for_status()
            assert 'javascript' in code.headers['content-type']
            runtime=client.get('/workbench/@vite/client');runtime.raise_for_status()
            token=re.search(r'const wsToken\s*=\s*"([^"]+)"',runtime.text)
            assert token
        with connect(f'ws://127.0.0.1:{port}/workbench/?token='+quote(token.group(1),safe=''),
            origin=base,subprotocols=['vite-hmr'],proxy=None,open_timeout=10) as stream, \
            httpx.Client(base_url=base,trust_env=False,timeout=15) as reload_client:
            assert json.loads(stream.recv(timeout=10))['type']=='connected'
            assert source.read_bytes()==original,'源码被其他操作更新，拒绝覆盖'
            source.write_bytes(temporary);modified=True
            changed=wait_update(stream)
            notified_at=time.monotonic()
            # 浏览器接到 HMR 后会重新请求模块，清除旧模块的失效状态。
            refreshed=reload_client.get('/workbench/src/app.tsx',params={'t':changed['timestamp']})
            refreshed.raise_for_status()
            # Vite 内置 chokidar 对 change 事件有 50ms 节流；模拟两次独立保存。
            time.sleep(max(0,.15-(time.monotonic()-notified_at)))
            assert source.read_bytes()==temporary,'源码被其他操作更新，拒绝覆盖'
            source.write_bytes(original);modified=False
            restored=wait_update(stream)
            refreshed=reload_client.get('/workbench/src/app.tsx',params={'t':restored['timestamp']})
            refreshed.raise_for_status()
        assert source.read_bytes()==original
        all_interfaces=None
        if sys.platform=='linux' and os.environ['ANP_DEV_HOST']=='0.0.0.0':
            all_interfaces=any(line.split()[1]==f'00000000:{port:04X}' and line.split()[3]=='0A'
                for line in Path('/proc/net/tcp').read_text().splitlines()[1:])
            assert all_interfaces,'CI 的 Vite 未绑定 Docker 可访问的主机接口'
        report={'scope':'独立前端真实启动与 HMR，未启动平台或模拟业务 API；不是 IDE 调试验收',
            'platform':sys.platform,'startup_seconds':round(startup,3),'two_ensure_calls_one_process':True,
            'change_update_path':changed['path'],'restore_update_path':restored['path'],'source_restored':True,
            'source_sha256':hashlib.sha256(original).hexdigest(),'all_interfaces_binding':all_interfaces}
    finally:
        try:
            if modified:
                if source.read_bytes()==temporary:source.write_bytes(original)
                elif source.read_bytes()!=original:raise RuntimeError('外部修改保留，请检查源码')
        finally:
            for process in created:
                if process.poll() is None:
                    if os.name!='nt':
                        assert os.getpgid(process.pid)==process.pid,'验收进程组身份不匹配'
                        os.killpg(process.pid,signal.SIGTERM)
                    else:process.terminate()
                process.wait(timeout=15)
    assert not frontend.ready(),'验收进程停止后端口仍可用'
    report['owned_process_stopped']=True
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=verify()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(result,ensure_ascii=False))
