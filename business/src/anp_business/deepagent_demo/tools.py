from langchain_core.tools import tool

from anp_business.shared.models import MATERIAL


@tool
def read_material() -> str:
    """读取本演示随附的架构资料，用于报告中的事实依据。"""
    return MATERIAL
