"""验证独立业务图发现、条件分支和自定义 Studio 输入，不把引擎限制为 demo。"""
import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import sys
from typing import TypedDict
import httpx
from langgraph.graph import StateGraph,START,END

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'platform/runtime'))
from anp_platform.agent import graph_specs
from anp.applications.registry import applications
from anp.runtime import remote_node


def test_external_application_directory_controls_graph_catalog(monkeypatch,tmp_path):
    folder=tmp_path/'custom';folder.mkdir()
    (folder/'app.toml').write_text('id="custom"\n[flow]\ndeployment_entry="custom_package.graph:agent"\n',encoding='utf8')
    monkeypatch.setenv('ANP_APPLICATIONS_ROOT',str(tmp_path))
    applications.cache_clear()
    try:assert graph_specs()=={'custom':'custom_package.graph:agent'}
    finally:applications.cache_clear()


def test_custom_conditional_flow_and_studio_payload_are_preserved(monkeypatch):
    class State(TypedDict):
        source_url:str
        task_id:str
        attempt:int
        input_data:dict
        fetched:dict
        result:dict
    def build(factory):
        graph=StateGraph(State)
        graph.add_node('fetch',factory('fetch','fetched'))
        graph.add_node('fast',factory('fast','result'))
        graph.add_node('slow',factory('slow','result'))
        graph.add_edge(START,'fetch')
        graph.add_conditional_edges('fetch',lambda state:state['fetched']['route'],{'fast':'fast','slow':'slow'})
        graph.add_edge('fast',END);graph.add_edge('slow',END)
        return graph.compile()
    manifest={'flow':{'entry':'custom.flow:build','studio_entry_node':'fetch','studio_payload_fields':['source_url']}}
    monkeypatch.setattr(remote_node,'application',lambda _:manifest)
    monkeypatch.setattr(remote_node,'entrypoint',lambda _:build)
    class Span:
        def update(self,**_):pass
    class Tracing:
        def start_as_current_observation(self,**_):return nullcontext(Span())
        def get_current_observation_id(self):return '0'*16
    monkeypatch.setattr(remote_node,'get_client',Tracing)
    requests=[]
    def handle(request):
        payload=json.loads(request.content);requests.append((request.url.path,payload))
        if request.url.path.endswith('/studio/tasks'):
            assert payload['payload']=={'source_url':'https://example.com/media'}
            return httpx.Response(200,json={'task_id':'00000000-0000-4000-8000-000000000002','attempt':1,
                'input_data':payload['payload'],'trace_attributes':{}})
        if request.url.path.endswith('/nodes/fetch'):output={'route':'fast'}
        elif request.url.path.endswith('/nodes/fast'):output={'saved':True}
        else:raise AssertionError('不应执行 slow 分支')
        return httpx.Response(200,json={'output':output,'trace_attributes':{}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    result=asyncio.run(remote_node.build_remote_graph('custom').ainvoke({'source_url':'https://example.com/media'},
        {'configurable':{'thread_id':'custom-studio'}}))
    assert result['result']=={'saved':True}
    assert [path.rsplit('/',1)[-1] for path,_ in requests]==['tasks','fetch','fast']
