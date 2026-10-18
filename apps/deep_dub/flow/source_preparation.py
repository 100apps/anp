"""来源准备子图：只传ID和轻量回执；复核未完成时持续interrupt，不接受客户端放行。"""
from typing import TypedDict,NotRequired
from uuid import UUID
from langgraph.graph import StateGraph,START
from langgraph.types import interrupt
from anp.errors import BusinessError
from anp.sdk.http import async_client


class SourcePreparationState(TypedDict):
    run_id:str
    source_preparation_id:str
    source_steps:NotRequired[dict]


def build_source_preparation_graph(worker_url,checkpointer=None,*,engine_token=None,dispatcher=None):
    async def call(state,step):
        run_id,procedure_id=str(UUID(state['run_id'])),str(UUID(state['source_preparation_id']))
        if dispatcher is not None:return await dispatcher(run_id,procedure_id,step)
        if not engine_token:raise BusinessError('缺少引擎访问凭据',503,'ENGINE_CREDENTIAL_MISSING')
        async with async_client(timeout=3700,trust_env=False) as client:
            response=await client.post(worker_url.rstrip('/')+f'/internal/deep_dub/source-preparation/{procedure_id}/{step}',
                headers={'Authorization':'Bearer '+engine_token},json={'run_id':run_id})
        if not response.is_success:
            try:error=response.json()['error']
            except (ValueError,TypeError,KeyError):error={}
            raise BusinessError(error.get('message','来源准备Worker失败'),response.status_code,error.get('code','SOURCE_WORKER_FAILED'))
        return response.json()
    def step(name):
        async def execute(state):
            while True:
                result=await call(state,name)
                if result['status'] not in ('queued','running'):break
                interrupt({'reason':'等待本轮设备或原执行尝试','step':name,'attempt_id':result.get('attempt_id')})
                # 恢复后的输入不作为成功凭据，重新走Worker和数据库检查。
            if result['status'] not in ('succeeded','skipped'):
                raise BusinessError('来源准备阶段未成功',409,'SOURCE_STAGE_NOT_SUCCEEDED')
            return {'source_steps':{**state.get('source_steps',{}),name:result}}
        return execute
    async def review(state):
        while True:
            result=await call(state,'review')
            if result['status']!='waiting_review':raise BusinessError('来源复核门禁响应无效',409,'SOURCE_REVIEW_GATE_INVALID')
            interrupt({'reason':'来源准备完成，等待独立正文、角色及时序复核','status':'waiting_review',**result})
            # Command(resume=...)仅触发重查，不能让passed/approve等客户端字段绕过审核。
    builder=StateGraph(SourcePreparationState)
    names=('acquire','decode','recognize','align','reference')
    for name in names:builder.add_node(name,step(name))
    builder.add_node('review',review);builder.add_edge(START,names[0])
    for a,b in zip(names,names[1:]):builder.add_edge(a,b)
    builder.add_edge(names[-1],'review')
    return builder.compile(checkpointer=checkpointer)
