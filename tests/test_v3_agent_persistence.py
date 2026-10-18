"""真实上游保存器的磁盘往返；不通过重放任务重建丢失状态。"""
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=r'''
import asyncio,gc,json,os,time
from pathlib import Path
import sys
sys.path.insert(0,os.environ['ANP_RUNTIME_SOURCE'])
os.environ.update(REDIS_URI='fake',DATABASE_URI=':memory:',LANGGRAPH_RUNTIME_EDITION='inmem',LANGSMITH_LANGGRAPH_API_VARIANT='local_dev',MIGRATIONS_PATH='__inmem')
from langgraph_runtime_inmem import checkpoint as cp
from langgraph_runtime_inmem import _persistence as persistence
from langgraph.graph import StateGraph,START,END
from typing import TypedDict
class State(TypedDict):
    count:int
if sys.argv[1]=='fixed':
    from anp_platform.agent_persistence import install
    install();install()
owner=cp.Checkpointer()
builder=StateGraph(State);builder.add_node('double',lambda state:{'count':state['count']*2});builder.add_edge(START,'double');builder.add_edge('double',END)
graph=builder.compile(checkpointer=owner)
first={'configurable':{'thread_id':'first'}}
second={'configurable':{'thread_id':'after-view'}}
def flush():
    for ref in list(persistence._stores.values()):
        store=ref()
        if store is not None:store.sync()
try:
    if sys.argv[2] in ('write','wait-write'):
        assert graph.invoke({'count':3},first)=={'count':6};flush()
        view=cp.Checkpointer(unpack_hook=lambda code,data:None)
        assert view.storage is owner.storage and view.serde is not owner.serde
        assert builder.compile(checkpointer=view).get_state(first).values=={'count':6}
        assert graph.invoke({'count':7},second)=={'count':14}
        if sys.argv[1]=='fixed':
            asyncio.run(view.__aexit__(None,None,None))
            assert graph.get_state(first).values=={'count':6}
            assert persistence._flush_thread is not None
            assert any(ref() is owner.storage for ref in persistence._stores.values())
        if sys.argv[2]=='write':flush()
        print(json.dumps({'in_memory_second':graph.get_state(second).values}),flush=True)
        if sys.argv[2]=='wait-write':
            while True:time.sleep(.1)
    else:
        print(json.dumps({'first':graph.get_state(first).values,'second':graph.get_state(second).values}))
finally:persistence.stop_flush_loop()
'''


@pytest.mark.parametrize('mode',['upstream','fixed'])
def test_state_read_view_must_not_replace_live_checkpoint_flush_target(tmp_path,mode):
    environment={**os.environ,'ANP_RUNTIME_SOURCE':str(ROOT/'platform/runtime')}
    def run(phase):
        result=subprocess.run([sys.executable,'-c',SCRIPT,mode,phase],cwd=tmp_path,env=environment,
            capture_output=True,text=True,encoding='utf-8',timeout=25)
        assert result.returncode==0,result.stderr[-3000:]
        return json.loads(result.stdout.splitlines()[-1])
    assert run('write')['in_memory_second']=={'count':14}
    restored=run('read')
    assert restored['first']=={'count':6}
    if mode=='upstream':assert restored['second']=={},'上游缺陷已变化，需要重新核对适配'
    else:assert restored['second']=={'count':14}


def test_real_background_flush_survives_owned_process_termination(tmp_path):
    environment={**os.environ,'ANP_RUNTIME_SOURCE':str(ROOT/'platform/runtime')}
    process=subprocess.Popen([sys.executable,'-c',SCRIPT,'fixed','wait-write'],cwd=tmp_path,env=environment,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        deadline=time.monotonic()+22
        file=tmp_path/'.langgraph_api/.langgraph_checkpoint.1.pckl'
        while True:
            assert process.poll() is None,'隔离持久化探针意外退出'
            if file.exists():
                # 仅读取本测试自己生成的 pickle，不读取任何外部上传文件。
                with file.open('rb') as source:stored=pickle.load(source)
                if 'after-view' in stored:break
            assert time.monotonic()<deadline,'真实后台刷新未保存读取视图之后的检查点'
            time.sleep(.1)
        process.terminate();process.wait(timeout=5)
        restored=subprocess.run([sys.executable,'-c',SCRIPT,'fixed','read'],cwd=tmp_path,env=environment,
            capture_output=True,text=True,encoding='utf-8',timeout=20)
        assert restored.returncode==0,restored.stderr[-2000:]
        assert json.loads(restored.stdout.splitlines()[-1])=={'first':{'count':6},'second':{'count':14}}
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
        process.communicate(timeout=5)
