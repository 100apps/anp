from datetime import datetime,timezone
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from anp.config import postgres_dsn
from anp.applications.registry import application,entrypoint
from anp.execution.nodes import execute_node


async def run_local(actor,task):
    objects=actor.objects
    def factory(name,key):
        async def node(state):
            output=await execute_node(objects,task,name,state)
            return {key:output}
        return node
    manifest=application(task['app_id'])
    try:
        async with AsyncPostgresSaver.from_conn_string(postgres_dsn()) as saver:
            await saver.setup()
            graph=entrypoint(manifest['flow']['entry'])(factory,saver)
            state=await graph.ainvoke({'task_id':task['id'],'attempt':task['attempt'],'input_data':task['input']},
                {'configurable':{'thread_id':f"{task['id']}:{task['attempt']}"}})
        latest=(await objects.select('tasks',id='eq.'+task['id']))[0]
        if latest['status']!='cancelled':
            await objects.patch('tasks',{'status':'succeeded','result':state['result'],
                'thread_id':f"{task['id']}:{task['attempt']}",'updated_at':datetime.now(timezone.utc).isoformat()},id='eq.'+task['id'])
    except Exception as error:
        latest=(await objects.select('tasks',id='eq.'+task['id']))[0]
        if latest['status']!='cancelled':
            await objects.patch('tasks',{'status':'failed','error':str(error)[:1000]},id='eq.'+task['id'])
    return (await objects.select('tasks',id='eq.'+task['id']))[0]
