"""来源准备范围、身份与真实LangGraph中断；HTTP夹具不是生产质量证据。"""
import asyncio,json,time
from uuid import uuid4
from types import SimpleNamespace
import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from anp.sdk.http import async_client
from anp.errors import BusinessError
from apps.deep_dub.services.source_jobs import SourceVault
from apps.deep_dub.flow import source_preparation as flows
from apps.deep_dub.flow.decode import build_decode_graph


def test_private_source_scope_mac_expiry_and_binding(tmp_path):
    vault=SourceVault(tmp_path);run,procedure=str(uuid4()),str(uuid4())
    record={'version':1,'scope':'deep_dub.source_preparation','run_id':run,'procedure_id':procedure,
        'actor_token':'fixture-private-not-real','expires_at':time.time()+60}
    token=vault.save(record);vault.bind_engine(token,run,procedure)
    assert vault.load(vault.engine_token(run,procedure),run,procedure)==record
    assert record['actor_token'] not in token
    for change in ({'scope':'deep_dub.full_decode'},{'run_id':str(uuid4())},{'procedure_id':str(uuid4())},{'expires_at':time.time()-1}):
        with pytest.raises(BusinessError):vault.load(vault.save({**record,**change}),run,procedure)
    file=vault.directory/(token.split('.')[0]+'.json');file.write_text(json.dumps({**record,'expires_at':time.time()+1000}))
    with pytest.raises(BusinessError):vault.load(token,run,procedure)


def test_source_graph_checkpoints_and_cannot_be_approved_by_resume(monkeypatch):
    calls=[]
    async def handler(request):
        step=request.url.path.split('/')[-1];calls.append(step)
        assert request.headers['Authorization']=='Bearer fixture-engine'
        body=json.loads(request.content);assert set(body)=={'run_id'}
        if step=='review':return httpx.Response(200,json={'status':'waiting_review','summary':{'word_summary':{'invalid_timestamp_chunks':1}}})
        return httpx.Response(200,json={'status':'succeeded','attempt_id':str(uuid4()),'outputs':[str(uuid4())]})
    def client(**kwargs):return async_client(**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(flows,'async_client',client)
    async def run():
        saver=InMemorySaver();config={'configurable':{'thread_id':'source-fixture'}}
        graph=build_decode_graph('http://fixture',saver,engine_token='fixture-engine')
        result=await graph.ainvoke({'run_id':str(uuid4()),'source_preparation_id':str(uuid4())},config)
        assert result['__interrupt__'] and calls==['acquire','decode','recognize','align','reference','review']
        for _ in range(2):
            result=await graph.ainvoke(Command(resume={'approve':True,'status':'passed'}),config)
            assert result['__interrupt__'] and calls[-1]=='review'
        state=await graph.aget_state(config,subgraphs=True)
        child=state.tasks[0].state
        assert state.next==('source_preparation',) and set(child.values['source_steps'])=={'acquire','decode','recognize','align','reference'}
        assert 'fixture-engine' not in json.dumps(child.values) and calls.count('acquire')==1
        with pytest.raises(BusinessError):
            await graph.ainvoke({'run_id':str(uuid4()),'attempt_id':str(uuid4()),'source_preparation_id':str(uuid4())},
                {'configurable':{'thread_id':'ambiguous-fixture'}})
    asyncio.run(run())


def test_source_worker_rechecks_revoked_identity_before_execution(monkeypatch):
    from apps.deep_dub.services import source_jobs
    run,procedure=str(uuid4()),str(uuid4())
    record={'workspace_id':str(uuid4()),'user_id':str(uuid4()),'actor_token':'fixture'}
    monkeypatch.setattr(source_jobs,'vault',SimpleNamespace(engine_token=lambda *a:'fixture',load=lambda *a:record))
    async def revoked(*args):raise BusinessError('会话已撤销',401,'UNAUTHENTICATED')
    monkeypatch.setattr(source_jobs,'actor_from_token',revoked)
    with pytest.raises(BusinessError,match='会话已撤销'):asyncio.run(source_jobs.dispatch(run,procedure,'acquire'))


def test_local_source_graph_runs_without_http_or_checkpointer(monkeypatch):
    def forbidden(**kwargs):raise AssertionError('默认本机图不得调用业务HTTP')
    monkeypatch.setattr(flows,'async_client',forbidden)
    calls=[]
    async def dispatch(run_id,procedure_id,step):
        calls.append(step)
        return {'status':'waiting_review','summary':{}} if step=='review' else {'status':'succeeded','outputs':[]}
    graph=flows.build_source_preparation_graph('',dispatcher=dispatch)
    result=asyncio.run(graph.ainvoke({'run_id':str(uuid4()),'source_preparation_id':str(uuid4())}))
    assert result['__interrupt__'] and calls==['acquire','decode','recognize','align','reference','review']


def test_thread_scope_uses_business_run_id_instead_of_reserved_metadata(monkeypatch):
    from apps.deep_dub.services import source_orchestration as orchestration
    run,procedure=str(uuid4()),str(uuid4());wrong=[False]
    async def handler(request):
        if request.url.path.endswith('/state'):
            assert request.url.params['subgraphs']=='true'
            return httpx.Response(200,json={'next':['source_preparation'],'values':{},'tasks':[
                {'name':'source_preparation','state':{'values':{'source_steps':{'acquire':{'status':'succeeded'}}}}}]})
        if request.url.path.endswith('/runs'):return httpx.Response(200,json=[{'status':'success'}])
        return httpx.Response(200,json={'status':'interrupted','metadata':{'business_run_id':str(uuid4()) if wrong[0] else run,'source_preparation_id':procedure}})
    async def select(*args,**kwargs):return [{'status':'waiting_review','technical_verified_at':None}]
    async def require(*args):pass
    actor=SimpleNamespace(workspace_id=str(uuid4()),objects=SimpleNamespace(select=select),require=require)
    monkeypatch.setattr(orchestration,'engine_client',lambda *a:async_client(base_url='http://fixture',transport=httpx.MockTransport(handler)))
    result=asyncio.run(orchestration.read(actor,run,procedure))
    assert result['business_status']=='waiting_review' and result['source_steps']['acquire']['status']=='succeeded'
    assert result['engine_status']=='interrupted' and result['native_run_status']=='success'
    wrong[0]=True
    with pytest.raises(BusinessError,match='不属于本轮'):asyncio.run(orchestration.read(actor,run,procedure))
