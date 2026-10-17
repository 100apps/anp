from uuid import uuid4,UUID
from pydantic import BaseModel,Field,field_validator
from anp.errors import BusinessError
from anp.applications.registry import application
from anp.execution.local import run_local
from anp.execution.agent_server import start_remote


class TaskInput(BaseModel):
    title: str=Field(default='文本分析任务',min_length=1,max_length=120)
    text: str=Field(min_length=1,max_length=20000)

    @field_validator('text')
    @classmethod
    def not_blank(cls,value):
        if not value.strip(): raise ValueError('文本不能为空')
        return value


class TaskService:
    def __init__(self,app_id):
        self.app_id=app_id

    async def create(self,actor,payload,request_key=None):
        await actor.require(self.app_id+'.task.create')
        validated=TaskInput.model_validate(payload)
        key=request_key or str(uuid4())
        if len(key)>200: raise BusinessError('幂等键过长',422,'VALIDATION_ERROR')
        existing=await actor.objects.select('tasks',workspace_id='eq.'+actor.workspace_id,
            app_id='eq.'+self.app_id,request_key='eq.'+key)
        input_data={'text':validated.text}
        if existing:
            if existing[0]['input']!=input_data or existing[0]['title']!=validated.title:
                raise BusinessError('相同幂等键不能提交不同内容',409,'IDEMPOTENCY_CONFLICT')
            return existing[0]
        return (await actor.objects.insert('tasks',{'workspace_id':actor.workspace_id,
            'created_by':actor.user['id'],'app_id':self.app_id,'title':validated.title,
            'input':input_data,'request_key':key}))[0]

    async def list(self,actor):
        await actor.require(self.app_id+'.task.read')
        return await actor.objects.select('tasks',workspace_id='eq.'+actor.workspace_id,
            app_id='eq.'+self.app_id,order='created_at.desc',limit='100')

    async def get(self,actor,task_id):
        try: UUID(task_id)
        except ValueError: raise BusinessError('任务 ID 格式不正确',422,'VALIDATION_ERROR')
        await actor.require(self.app_id+'.task.read')
        rows=await actor.objects.select('tasks',id='eq.'+task_id,workspace_id='eq.'+actor.workspace_id,
                                        app_id='eq.'+self.app_id)
        if not rows: raise BusinessError('任务不存在或无权访问',404,'NOT_FOUND')
        task=rows[0]
        task['nodes']=await actor.objects.select('node_operations',task_id='eq.'+task_id,order='created_at.asc')
        return task

    async def run(self,actor,task_id,mode='local'):
        await self.get(actor,task_id)
        claim=await actor.objects.rpc('begin_task',p_task=task_id,p_mode=mode)
        task=claim['task']
        if not claim['acquired']: return task
        if mode=='local': return await run_local(actor,task)
        try: await start_remote(actor.objects,task)
        except Exception:
            # 保留派发意图；API 协调器会重试，不因网络故障丢失任务。
            pass
        return (await actor.objects.select('tasks',id='eq.'+task_id))[0]

    async def cancel(self,actor,task_id):
        await self.get(actor,task_id)
        task=await actor.objects.rpc('cancel_task',p_task=task_id)
        if task.get('mode')=='http' and task.get('run_id'):
            from anp.execution.agent_server import engine_client
            try:
                async with engine_client(self.app_id) as client:
                    response=await client.post(f"/threads/{task['thread_id']}/runs/{task['run_id']}/cancel",params={'action':'interrupt'})
                    response.raise_for_status()
            except Exception:
                # 后续节点同时检查数据库取消标记，仍不会继续写入业务结果。
                pass
        return task

    async def result(self,actor,task_id):
        await self.get(actor,task_id)
        rows=await actor.objects.in_schema('app_'+self.app_id).select('results',task_id='eq.'+task_id)
        if not rows: raise BusinessError('结果尚未生成',409,'RESULT_NOT_READY')
        return rows[0]
