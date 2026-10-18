"""薄图只传运行/尝试ID；引擎凭据来自部署环境，单尝试授权留在业务主机。"""
from typing import TypedDict,NotRequired
from uuid import UUID

from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt

from anp.errors import BusinessError
from anp.sdk.http import async_client


class DecodeState(TypedDict):
    run_id:str
    attempt_id:NotRequired[str]
    source_preparation_id:NotRequired[str]
    source_steps:NotRequired[dict]
    result:NotRequired[dict]


def build_decode_graph(worker_url,checkpointer=None,*,engine_token=None):
    async def worker(state:DecodeState):
        if not engine_token:raise BusinessError('图部署缺少引擎服务凭据',503,'ENGINE_CREDENTIAL_MISSING')
        run_id,attempt_id=str(UUID(state['run_id'])),str(UUID(state['attempt_id']))
        async with async_client(timeout=660,trust_env=False) as client:
            response=await client.post(worker_url.rstrip('/')+f'/internal/deep_dub/engine-jobs/{attempt_id}/decode',
                headers={'Authorization':'Bearer '+engine_token},json={'run_id':run_id})
        if not response.is_success:
            # 不回显头、上下文或原始HTTP异常，避免作业令牌进入图追踪。
            try:error=response.json()['error']
            except (ValueError,KeyError,TypeError):error={}
            raise BusinessError(error.get('message','业务Worker调用失败'),response.status_code,error.get('code','WORKER_ERROR'))
        result=response.json()
        if result['status'] in ('queued','running'):
            interrupt({'reason':'等待当前执行尝试','attempt_id':attempt_id})
        if result['status']!='succeeded':raise BusinessError('业务阶段未成功，需检查冻结回执',409,'STAGE_NOT_SUCCEEDED')
        return {'result':result}
    builder=StateGraph(DecodeState)
    builder.add_node('full_decode',worker)
    from apps.deep_dub.flow.source_preparation import build_source_preparation_graph
    builder.add_node('source_preparation',build_source_preparation_graph(worker_url,engine_token=engine_token))
    def route(state):
        if bool(state.get('attempt_id'))==bool(state.get('source_preparation_id')):
            raise BusinessError('必须指定单次解码或来源准备ID中的一种',422,'FLOW_INPUT_INVALID')
        return 'source_preparation' if state.get('source_preparation_id') else 'full_decode'
    builder.add_conditional_edges(START,route,{'source_preparation':'source_preparation','full_decode':'full_decode'})
    builder.add_edge('full_decode',END)
    return builder.compile(checkpointer=checkpointer)
