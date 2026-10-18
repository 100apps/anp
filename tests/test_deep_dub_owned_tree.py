"""真实Windows子孙进程停止证明；仅夹具，不是视频下载验收。"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from anp.errors import BusinessError
from apps.deep_dub.services.owned_tree import run_tree
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.listener_owner import listener_owners

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='Windows Job Object真实验收')


def test_normal_output_and_failed_tool_are_reaped(tmp_path):
    async def run():
        for code in (0,7):
            folder=tmp_path/str(code);folder.mkdir()
            with LocalCpuPool('tree-test',directory=folder/'pool') as pool:
                task=run_tree([sys._base_executable,'-I','-S','-c',f'print("fixture");raise SystemExit({code})'],folder,10)
                if code:
                    with pytest.raises(BusinessError,match='来源获取失败'):await task
                else:
                    proof=await task
                    assert proof['job']['total_processes']==2 and proof['job']['active_processes']==0
                assert pool.stopped() and len(pool.data['jobs'])==1
            assert (folder/'tool.stdout').read_text().strip()=='fixture'
    asyncio.run(run())


@pytest.mark.parametrize('mode',['cancel','timeout'])
def test_descendants_stopped_before_pool_release_and_unrelated_kept(tmp_path,mode):
    external=subprocess.Popen([sys._base_executable,'-I','-S','-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
    marker=tmp_path/'child.json'
    child="import os,socket,time,json,pathlib;s=socket.socket();s.bind(('127.0.0.1',0));s.listen();pathlib.Path("+repr(str(marker))+").write_text(json.dumps([os.getpid(),s.getsockname()[1]]));time.sleep(60)"
    parent='import subprocess,sys,time;subprocess.Popen([sys.executable,"-I","-S","-c",'+repr(child)+']);time.sleep(60)'
    async def run():
        with LocalCpuPool('tree-'+mode,directory=tmp_path/'pool') as pool:
            task=asyncio.create_task(run_tree([sys._base_executable,'-I','-S','-c',parent],tmp_path,2 if mode=='timeout' else 20))
            for _ in range(100):
                if marker.exists():break
                if task.done():await task
                await asyncio.sleep(.02)
            assert marker.exists()
            pid,port=json.loads(marker.read_bytes());assert listener_owners(port)=={pid}
            if mode=='cancel':task.cancel()
            with pytest.raises(asyncio.CancelledError if mode=='cancel' else BusinessError):await task
            assert pool.stopped() and listener_owners(port)==set() and external.poll() is None
    try:asyncio.run(run())
    finally:external.terminate();external.wait(timeout=10)
