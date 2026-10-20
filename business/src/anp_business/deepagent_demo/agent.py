import asyncio

from deepagents import create_deep_agent

from anp_business.deepagent_demo.tools import read_material
from anp_business.shared.models import get_model


def build_agent():
    # 工厂在创建运行时才读取模型配置，不在导入时连接外部平台。
    return create_deep_agent(model=get_model(), tools=[read_material],
        system_prompt="你是中文架构研究助手。先读取材料，再提交有依据的报告。",
        name="deepagent_demo")


async def agent(config=None):
    # Deep Agents 的版本探测会读文件系统，工厂在线程执行，避免阻塞 ASGI。
    return await asyncio.to_thread(build_agent)
