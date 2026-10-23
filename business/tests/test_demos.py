import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from anp_business.deepagent_demo.agent import agent
from anp_business.server.http import app
from anp_business.workflow_demo.graph import graph


@pytest.mark.asyncio
async def test_workflow_normalizes_and_returns_summary(monkeypatch):
    monkeypatch.setenv("ANP_MODEL_MODE", "demo")
    events = [event async for event in graph.astream({"text": "  组件  独立部署\n 可插拔。 "}, stream_mode="updates")]
    assert [next(iter(event)) for event in events] == ["normalize", "summarize", "finish"]
    result = await graph.ainvoke({"text": "  组件  独立部署\n 可插拔。 "})
    assert result["normalized"] == "组件 独立部署 可插拔。"
    assert "组件" in result["summary"]
    assert result["model_mode"] == "demo"


@pytest.mark.asyncio
async def test_workflow_rejects_empty():
    with pytest.raises(ValueError, match="请输入"):
        await graph.ainvoke({"text": " \n "})


@pytest.mark.asyncio
async def test_deep_agent_really_calls_tool(monkeypatch):
    monkeypatch.setenv("ANP_MODEL_MODE", "demo")
    graph = await agent()
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "请调研架构"}]})
    tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert any(message.name == "read_material" and "PostgreSQL" in message.content for message in tools)
    assert "报告" in result["messages"][-1].content
    assert "离线演示模型" in result["messages"][-1].content


def test_extension_health():
    value = TestClient(app).get("/api/health").json()
    assert value["service"] == "anp"
    assert value["model_mode"] == "demo"


def test_workbench_only_exposes_supported_business():
    client = TestClient(app)
    assert client.get("/api/workbench").json()["graphs"] == ["workflow_demo", "deepagent_demo"]
    assert client.get("/api/dubbing/runs").status_code == 404
