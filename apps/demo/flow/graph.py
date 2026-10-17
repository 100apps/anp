"""同一张业务图，根据执行适配器在本机或平台执行。"""
from langgraph.graph import StateGraph,START,END
from .state import State,FlowInput


def build_graph(node_factory, checkpointer=None):
    builder=StateGraph(State,input_schema=FlowInput)
    builder.add_node('normalize',node_factory('normalize','normalized'))
    builder.add_node('analyze',node_factory('analyze','analysis'))
    builder.add_node('save',node_factory('save','result'))
    builder.add_edge(START,'normalize')
    builder.add_edge('normalize','analyze')
    builder.add_edge('analyze','save')
    builder.add_edge('save',END)
    return builder.compile(checkpointer=checkpointer)
