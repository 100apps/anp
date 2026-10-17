from uuid import uuid5,NAMESPACE_URL
import httpx
from anp.config import value,secret
from anp.sdk.tracing import task_attributes


def engine_client(app_id='demo'):
    from anp.applications.registry import applications
    base=value('agent','url').rstrip('/')
    from urllib.parse import urlsplit,urlunsplit
    parsed=urlsplit(base)
    index=list(applications()).index(app_id)
    endpoint=value('agent',app_id+'_url')
    if not endpoint:
        endpoint=urlunsplit((parsed.scheme,f'{parsed.hostname}:{(parsed.port or 8124)+index}',parsed.path,'',''))
    return httpx.AsyncClient(base_url=endpoint,
        headers={'Authorization':'Bearer '+secret('ENGINE_TOKEN')},timeout=20,trust_env=False)


async def start_remote(objects,task):
    thread_id=str(uuid5(NAMESPACE_URL,f"anp:{task['id']}:{task['attempt']}"))
    async with engine_client(task['app_id']) as client:
        response=await client.post('/threads',json={'thread_id':thread_id,'if_exists':'do_nothing'})
        response.raise_for_status()
        response=await client.get(f'/threads/{thread_id}/runs')
        response.raise_for_status()
        runs=response.json()
        if runs: run=runs[0]
        else:
            response=await client.post(f'/threads/{thread_id}/runs',json={
                'assistant_id':task['app_id'],'input':{'task_id':task['id'],
                    'attempt':task['attempt'],'input_data':task['input']},
                'metadata':{'task_id':task['id'],'app_id':task['app_id'],'anp_trace':task_attributes(task)},'multitask_strategy':'reject'})
            response.raise_for_status()
            run=response.json()
    await objects.patch('tasks',{'thread_id':thread_id,'run_id':run['run_id']},id='eq.'+task['id'])


async def reconcile_remote(objects,task):
    if not task['run_id']:
        await start_remote(objects,task)
        return
    async with engine_client(task['app_id']) as client:
        response=await client.get(f"/threads/{task['thread_id']}/runs/{task['run_id']}")
        response.raise_for_status()
        run=response.json()
    if task['cancel_requested']:
        async with engine_client(task['app_id']) as client:
            await client.post(f"/threads/{task['thread_id']}/runs/{task['run_id']}/cancel",params={'action':'interrupt'})
        return
    if run['status']=='success':
        rows=await objects.in_schema('app_'+task['app_id']).select('results',task_id='eq.'+task['id'])
        if not rows:
            await objects.patch('tasks',{'status':'failed','error':'图已结束，但结果没有保存'},id='eq.'+task['id'])
        else:
            await objects.patch('tasks',{'status':'succeeded','result':{'saved':True,'object_path':rows[0]['object_path']}},id='eq.'+task['id'])
    elif run['status'] in ('error','timeout','interrupted'):
        await objects.patch('tasks',{'status':'failed','error':'图执行失败，请查看节点日志'},id='eq.'+task['id'])
