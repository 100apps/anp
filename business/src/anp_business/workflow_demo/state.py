from typing import TypedDict


class WorkflowState(TypedDict, total=False):
    text: str
    normalized: str
    summary: str
    model_mode: str
