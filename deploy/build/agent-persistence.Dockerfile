# 基底必须是已经核实十一组件及上游 SHA 的完整本机镜像；不使用漂移的 latest。
ARG ANP_PLATFORM_BASE
FROM ${ANP_PLATFORM_BASE}
COPY agent.py agent_persistence.py /opt/anp/anp_platform/
RUN /opt/anp/.venv/bin/python -c "from anp_platform.agent_persistence import install; install(); from langgraph_runtime_inmem import checkpoint; assert checkpoint.InMemorySaver._anp_shared_persistence_view"
ARG ANP_PATCH_SHA256
LABEL dev.anp.agent_persistence_patch="${ANP_PATCH_SHA256}"
