from functools import lru_cache
from contextlib import contextmanager
from uuid import uuid5, NAMESPACE_URL
import logging
from langfuse import Langfuse
from langfuse import propagate_attributes
from anp.config import value,secret


@lru_cache
def get_client():
    return Langfuse(public_key=secret('LANGFUSE_PUBLIC_KEY'),secret_key=secret('LANGFUSE_SECRET_KEY'),
                    base_url=value('langfuse','url'),timeout=5)


def task_attributes(task):
    """身份来自已授权的任务记录，不使用节点输入提供的用户身份。"""
    return dict(user_id=task['created_by'],session_id=task['id'],
                trace_name=f"{task['app_id']}/{task['title']}",
                metadata={key:str(task[key]) for key in ('app_id','workspace_id','attempt','mode') if key in task})


def task_trace_id(task_id,attempt):
    return task_id.replace('-','') if attempt==1 else uuid5(NAMESPACE_URL,f'anp:trace:{task_id}:{attempt}').hex


@contextmanager
def task_observation(task,name,state,parent_span_id=None):
    context={'trace_id':task_trace_id(task['id'],task['attempt'])}
    if parent_span_id: context['parent_span_id']=parent_span_id
    with propagate_attributes(**task_attributes(task)):
        with get_client().start_as_current_observation(name=name,trace_context=context,input=state) as span:
            yield span


SCORE_DESCRIPTIONS={
    'node_success':'节点成功执行并提交幂等记录：1 成功，0 失败',
    'normalization_valid':'文本空白归一化及 SHA256 校验正确',
    'analysis_consistency':'文本统计、词频及预览与确定性参考计算一致',
    'artifact_saved':'业务结果记录存在且对象存储中的 JSON 与报告一致',
}


@lru_cache
def ensure_score_configs():
    api=get_client().api.score_configs
    configs={x.name:x for x in api.get(limit=100).data if not x.is_archived}
    for name,description in SCORE_DESCRIPTIONS.items():
        if name not in configs:
            configs[name]=api.create(name=name,data_type='BOOLEAN',description=description)
        if configs[name].data_type!='BOOLEAN':
            raise ValueError(f'评分配置类型不匹配：{name}')
    return {name:item.id for name,item in configs.items() if name in SCORE_DESCRIPTIONS}


def record_scores(task,node,observation_id,scores):
    """评分不可影响业务提交；使用固定 ID 避免重试重复写入。"""
    try:
        configs=ensure_score_configs()
        trace_id=task_trace_id(task['id'],task['attempt'])
        for name,passed in scores.items():
            get_client().create_score(name=name,value=int(passed),data_type='BOOLEAN',
                trace_id=trace_id,observation_id=observation_id,config_id=configs[name],
                score_id=str(uuid5(NAMESPACE_URL,f'{trace_id}:{node}:{name}')),
                comment=SCORE_DESCRIPTIONS[name],metadata={'node':node,'evaluator_version':'1.0.0'})
    except Exception:
        logging.getLogger(__name__).exception('评分写入失败：task=%s node=%s',task['id'],node)
