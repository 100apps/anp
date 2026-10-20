"""确定性演示模型与显式选择的真实模型。"""
import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

MATERIAL = "本项目采用三层架构。存储层提供 PostgreSQL、Redis、ClickHouse 和 RustFS。平台源码独立构建后与存储组合为统一工具镜像，启动时选择服务，可连接本地或外部资源。业务代码在宿主运行，使用 LangGraph 与 Deep Agents，内置 HTTP 扩展按路径代理平台。"


class DemoModel(BaseChatModel):
    """只用于离线演示，不伪装真实大模型调用。"""
    @property
    def _llm_type(self) -> str:
        return "anp-offline-demo"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        outputs = [m for m in messages if isinstance(m, ToolMessage) and m.name == "read_material"]
        if not outputs:
            message = AIMessage(content="", tool_calls=[{
                "name": "read_material", "args": {}, "id": "read-demo-material", "type": "tool_call"
            }])
        else:
            message = AIMessage(content="## 架构调研报告\n\n" + str(outputs[-1].content) +
                                "\n\n建议：按组件升级，采用兼容版本，分别验证本地与外部连接。\n\n（离线演示模型）")
        return ChatResult(generations=[ChatGeneration(message=message)])


def model_mode() -> str:
    return os.getenv("ANP_MODEL_MODE", "demo")


def get_model() -> BaseChatModel:
    if model_mode() == "demo":
        return DemoModel()
    if model_mode() != "openai":
        raise ValueError("ANP_MODEL_MODE 只能为 demo 或 openai")
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=os.getenv("ANP_MODEL", "gpt-4.1-mini"),
                      base_url=os.getenv("ANP_MODEL_BASE_URL") or None,
                      api_key=os.environ["ANP_MODEL_API_KEY"], temperature=0)
