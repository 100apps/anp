"""把业务限时授权绑定至平台持久化Agent线程，不向引擎传用户会话。"""
from uuid import uuid5,NAMESPACE_URL
from anp.errors import BusinessError
from anp.execution.agent_server import engine_client
from apps.deep_dub.services.source_jobs import issue,REVISION
from apps.deep_dub.services.versions import identifier


def thread_identifier(run_id,procedure_id):
    return str(uuid5(NAMESPACE_URL,f'anp:deep_dub:source-preparation-thread:{identifier(run_id)}:{identifier(procedure_id)}'))


async def prepare_local(actor,run_id,payload,request_key=None):
    """默认CLI直接运行相同业务节点，不要求启动业务API/Worker或Agent。"""
    from apps.deep_dub.services.source_jobs import dispatch
    from apps.deep_dub.flow.source_preparation import build_source_preparation_graph
    grant=await issue(actor,run_id,payload,request_key)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+grant['run_id'])
    if runs[0]['status']=='waiting_review':
        review=await dispatch(grant['run_id'],grant['procedure_id'],'review')
    else:
        graph=build_source_preparation_graph('',dispatcher=dispatch)
        result=await graph.ainvoke({'run_id':grant['run_id'],'source_preparation_id':grant['procedure_id']})
        interruptions=result.get('__interrupt__',[])
        if not interruptions:raise BusinessError('来源准备没有停在复核门禁',409,'SOURCE_REVIEW_GATE_MISSING')
        review={k:interruptions[0].value[k] for k in ('status','summary','review_attempt_id')}
    return {**grant,'status':'waiting_review','review':review,'execution':'local','flow_revision':REVISION}


async def launch(actor,run_id,payload,request_key=None):
    grant=await issue(actor,run_id,payload,request_key)
    thread=thread_identifier(run_id,grant['procedure_id'])
    async with engine_client('deep_dub') as client:
        response=await client.post('/threads',json={'thread_id':thread,'if_exists':'do_nothing',
            'metadata':{'app_id':'deep_dub','business_run_id':grant['run_id'],'source_preparation_id':grant['procedure_id'],'flow_revision':REVISION,
                'source_request_key':grant['request_key'],'source_parameters':{k:v for k,v in payload.items() if k!='ttl_seconds'}}})
        if not response.is_success:raise BusinessError('平台未能创建来源准备线程',503,'SOURCE_ENGINE_UNAVAILABLE')
        response=await client.get(f'/threads/{thread}/runs')
        if not response.is_success:raise BusinessError('无法读取来源准备运行',503,'SOURCE_ENGINE_UNAVAILABLE')
        runs=response.json()
        if runs and runs[0]['status'] not in ('error','timeout'):
            active=runs[0]
        else:
            response=await client.post(f'/threads/{thread}/runs',json={'assistant_id':'deep_dub',
                'input':None if runs else {'run_id':grant['run_id'],'source_preparation_id':grant['procedure_id']},
                'metadata':{'app_id':'deep_dub','business_run_id':grant['run_id'],'flow_revision':REVISION},'multitask_strategy':'reject'})
            if not response.is_success:raise BusinessError('来源准备图未能启动；授权与既有阶段已保留',503,'SOURCE_ENGINE_UNAVAILABLE')
            active=response.json()
    current=await read(actor,grant['run_id'],grant['procedure_id'])
    return {**grant,'thread_id':thread,'agent_run_id':active['run_id'],'status':current['engine_status'],
        'native_run_status':active['status'],'flow_revision':REVISION}


async def read(actor,run_id,procedure_id):
    await actor.require('deep_dub.task.read');run_id,procedure_id=identifier(run_id),identifier(procedure_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs:raise BusinessError('运行不存在或无权访问',404,'NOT_FOUND')
    thread=thread_identifier(run_id,procedure_id)
    async with engine_client('deep_dub') as client:
        response=await client.get(f'/threads/{thread}')
        if response.status_code==404:raise BusinessError('来源准备线程不存在',404,'NOT_FOUND')
        if not response.is_success:raise BusinessError('来源准备线程暂不可读',503,'SOURCE_ENGINE_UNAVAILABLE')
        thread_info=response.json();metadata=thread_info.get('metadata',{})
        if metadata.get('business_run_id')!=run_id or metadata.get('source_preparation_id')!=procedure_id:
            raise BusinessError('来源准备线程不属于本轮',409,'SOURCE_THREAD_MISMATCH')
        response=await client.get(f'/threads/{thread}/state',params={'subgraphs':'true'})
        if not response.is_success:raise BusinessError('来源准备状态暂不可读',503,'SOURCE_ENGINE_UNAVAILABLE')
        state=response.json()
        response=await client.get(f'/threads/{thread}/runs')
        if not response.is_success:raise BusinessError('来源准备运行暂不可读',503,'SOURCE_ENGINE_UNAVAILABLE')
        engine_runs=response.json()
    values=state.get('values') or {}
    for task in state.get('tasks',[]):
        if task.get('name')=='source_preparation' and isinstance(task.get('state'),dict):
            values=task['state'].get('values') or {}
    native_status=engine_runs[0]['status'] if engine_runs else 'not_started'
    interrupted=thread_info.get('status')=='interrupted' or bool(state.get('interrupts')) or any(t.get('interrupts') for t in state.get('tasks',[]))
    return {'run_id':run_id,'procedure_id':procedure_id,'thread_id':thread,'business_status':runs[0]['status'],
        'engine_status':'interrupted' if interrupted else native_status,'native_run_status':native_status,'next':state.get('next',[]),
        'source_steps':values.get('source_steps',{}),'technical_verified_at':runs[0]['technical_verified_at'],
        'request_key':metadata.get('source_request_key'),'parameters':metadata.get('source_parameters',{})}


async def latest(actor,run_id):
    await actor.require('deep_dub.task.read');run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs:raise BusinessError('运行不存在或无权访问',404,'NOT_FOUND')
    async with engine_client('deep_dub') as client:
        response=await client.post('/threads/search',json={'metadata':{'app_id':'deep_dub','business_run_id':run_id,'flow_revision':REVISION},'limit':1})
        if not response.is_success:raise BusinessError('来源准备线程暂不可读',503,'SOURCE_ENGINE_UNAVAILABLE')
        threads=response.json()
    return await read(actor,run_id,threads[0]['metadata']['source_preparation_id']) if threads else None
