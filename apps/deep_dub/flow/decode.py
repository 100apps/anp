"""薄图适配：引擎只带单尝试能力令牌调用业务Worker，不加载业务用户会话。"""
from dataclasses import dataclass,field
from typing import TypedDict,NotRequired
from uuid import UUID

from langgraph.graph import StateGraph,START,END
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from anp.errors import BusinessError
from anp.sdk.http import async_client


class DecodeState(TypedDict):
    run_id:str
    attempt_id:str
    result:NotRequired[dict]


@dataclass
class JobContext:
    # LangGraph context不进入节点输入/输出；令牌也不出现在对象repr。
    job_token:str=field(repr=False)


def build_decode_graph(worker_url,checkpointer=None):
    async def worker(state:DecodeState,runtime:Runtime[JobContext]):
        context=runtime.context
        token=context.get('job_token') if isinstance(context,dict) else getattr(context,'job_token',None)
        if not token:raise BusinessError('需要先为本次调试签发作业授权',401,'INVALID_JOB_GRANT')
        run_id,attempt_id=str(UUID(state['run_id'])),str(UUID(state['attempt_id']))
        async with async_client(timeout=660,trust_env=False) as client:
            response=await client.post(worker_url.rstrip('/')+f'/internal/deep_dub/jobs/{attempt_id}/decode',
                headers={'Authorization':'Bearer '+token},json={'run_id':run_id})
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
    builder=StateGraph(DecodeState,context_schema=JobContext)
    builder.add_node('full_decode',worker)
    builder.add_edge(START,'full_decode');builder.add_edge('full_decode',END)
    return builder.compile(checkpointer=checkpointer)
