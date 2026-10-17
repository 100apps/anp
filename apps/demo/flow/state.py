from typing import TypedDict
from pydantic import BaseModel,Field


class FlowInput(BaseModel):
    text: str | None=Field(default=None,max_length=20000,description='Studio 调试直接填写文本；系统自动创建业务任务')
    title: str=Field(default='Studio 文本分析',min_length=1,max_length=120)
    task_id: str | None=Field(default=None,description='业务 API 内部任务 ID；Studio 调试时留空')
    attempt: int | None=Field(default=None,ge=1,description='内部执行次数；Studio 调试时留空')
    input_data: dict | None=Field(default=None,description='兼容业务 API 输入；Studio 通常只填写 text')


class State(TypedDict):
    text: str
    title: str
    task_id: str
    attempt: int
    input_data: dict
    normalized: dict
    analysis: dict
    result: dict
