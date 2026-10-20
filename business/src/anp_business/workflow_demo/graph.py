from langgraph.graph import END, START, StateGraph

from anp_business.workflow_demo.nodes import finish, normalize, summarize
from anp_business.workflow_demo.state import WorkflowState

builder = StateGraph(WorkflowState)
builder.add_node("normalize", normalize)
builder.add_node("summarize", summarize)
builder.add_node("finish", finish)
builder.add_edge(START, "normalize")
builder.add_edge("normalize", "summarize")
builder.add_edge("summarize", "finish")
builder.add_edge("finish", END)
graph = builder.compile(name="workflow_demo")
