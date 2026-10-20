from langchain_core.messages import HumanMessage

from anp_business.shared.models import get_model, model_mode
from anp_business.workflow_demo.state import WorkflowState


def normalize(state: WorkflowState) -> dict:
    text = " ".join(state.get("text", "").split())
    if not text:
        raise ValueError("请输入需要总结的文本")
    return {"normalized": text}


async def summarize(state: WorkflowState) -> dict:
    text = state["normalized"]
    if model_mode() == "demo":
        summary = "摘要：" + text[:160] + ("…" if len(text) > 160 else "")
    else:
        result = await get_model().ainvoke([HumanMessage(content="请用中文简洁总结：\n" + text)])
        summary = str(result.content)
    return {"summary": summary, "model_mode": model_mode()}


def finish(state: WorkflowState) -> dict:
    return {"summary": state["summary"]}
