import httpx
import hashlib
import json
from anp.sdk.tracing import get_client,task_trace_id
from langfuse import propagate_attributes
from langchain_core.runnables import RunnableConfig
from anp.config import value,secret
from anp.applications.registry import application,entrypoint


def build_remote_graph(app_id):
    manifest=application(app_id)
    studio=manifest['flow']
    def factory(name,output_key):
        async def node(state,config:RunnableConfig):
            attributes=config.get('metadata',{}).get('anp_trace',{})
            prepared={}
            fields=studio.get('studio_payload_fields',[])
            payload={field:state.get(field,(state.get('input_data') or {}).get(field)) for field in fields}
            changed_studio_input=(name==studio.get('studio_entry_node') and not attributes
                and any(payload.get(field) is not None and payload[field]!=(state.get('input_data') or {}).get(field)
                    for field in fields if field!='title'))
            if not state.get('task_id') or changed_studio_input:
                if name!=studio.get('studio_entry_node') or not fields:
                    raise ValueError('缺少任务上下文；请配置业务 flow 的 studio_entry_node 和 studio_payload_fields')
                if not any(value is not None and (not isinstance(value,str) or value.strip()) for field,value in payload.items() if field!='title'):
                    raise ValueError('请先填写该业务图的非空输入后运行')
                thread=str(config.get('configurable',{}).get('thread_id',''))
                key='studio:'+hashlib.sha256((thread+json.dumps(payload,sort_keys=True)).encode()).hexdigest()
                async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
                    response=await client.post(value('workbench','worker_url')+f'/internal/apps/{app_id}/studio/tasks',
                        headers={'Authorization':'Bearer '+secret('WORKER_TOKEN')},
                        json={'payload':payload,'request_key':key})
                    response.raise_for_status()
                    prepared=response.json()
                attributes=prepared.pop('trace_attributes')
                state={**state,**prepared}
            if not state.get('attempt') or not state.get('input_data'):
                raise ValueError('内部任务输入需要 task_id、attempt 和 input_data；Studio 新建调试请填写该业务图的输入字段')
            with propagate_attributes(**attributes), get_client().start_as_current_observation(name='引擎节点/'+name,
                input=state,trace_context={'trace_id':task_trace_id(state['task_id'],state['attempt'])}) as span:
                async with httpx.AsyncClient(timeout=90,trust_env=False) as client:
                    response=await client.post(value('workbench','worker_url')+f'/internal/apps/{app_id}/nodes/{name}',
                        headers={'Authorization':'Bearer '+secret('WORKER_TOKEN')},
                        json={'state':state,'parent_span_id':get_client().get_current_observation_id()})
                    response.raise_for_status()
                    output=response.json()['output']
                    with propagate_attributes(**response.json()['trace_attributes']):
                        span.update(output=output)
                    return {**prepared,output_key:output}
        return node
    return entrypoint(manifest['flow']['entry'])(factory)
