"""本地节点和 HTTP Worker 共用的幂等、追踪及授权数据上下文。"""
import asyncio
from dataclasses import dataclass
import hashlib
import json
from weakref import WeakValueDictionary
from anp.sdk.tracing import get_client,task_observation,record_scores
import logging
from anp.applications.registry import application, entrypoint
from anp.errors import BusinessError
from anp.sdk.objects import Objects

locks=WeakValueDictionary()


@dataclass
class NodeContext:
    objects: Objects
    task: dict
    operation_id: str


async def execute_node(objects, task, name, state, parent_span_id=None):
    current=await objects.select('tasks',id='eq.'+task['id'])
    if not current:
        raise BusinessError('任务不存在或无权访问',404,'NOT_FOUND')
    if current[0]['cancel_requested']:
        raise BusinessError('任务已取消',409,'TASK_CANCELLED')
    manifest=application(task['app_id'])
    registry=entrypoint(manifest['nodes']['entry'])
    if name not in registry:
        raise BusinessError('节点不存在',404,'NODE_NOT_FOUND')
    if state['attempt']!=task['attempt']:
        raise BusinessError('已过期的任务执行',409,'STALE_EXECUTION')
    operation_id=f"{task['id']}:{task['attempt']}:{manifest['version']}:{name}"
    digest=hashlib.sha256(json.dumps(state,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    lock=locks.setdefault(operation_id,asyncio.Lock())
    async with lock:
        rows=await objects.select('node_operations',operation_id='eq.'+operation_id)
        if rows:
            if rows[0]['input_hash']!=digest:
                raise BusinessError('幂等键对应的输入不一致',409,'IDEMPOTENCY_CONFLICT')
            return rows[0]['output']
        with task_observation(task,f'业务节点/{name}',state,parent_span_id) as span:
            context=NodeContext(objects,task,operation_id)
            observation_id=get_client().get_current_observation_id()
            try:
                output=await registry[name](state,context)
                await objects.insert('node_operations',{'operation_id':operation_id,'task_id':task['id'],
                    'node':name,'input_hash':digest,'output':output})
            except Exception as error:
                span.update(level='ERROR',status_message=str(error)[:1000])
                record_scores(task,name,observation_id,{'node_success':False})
                raise
            span.update(output=output)
            scores={'node_success':True}
            if manifest.get('evaluation',{}).get('entry'):
                try:
                    scores.update(await entrypoint(manifest['evaluation']['entry'])(name,state,output,context))
                except Exception:
                    logging.getLogger(__name__).exception('业务评分器执行失败：%s',name)
            record_scores(task,name,observation_id,scores)
            return output
