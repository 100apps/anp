FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /usr/local/bin/uv
WORKDIR /workspace
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY apps ./apps
COPY platform/config/agents ./platform/config/agents
COPY platform/init ./platform/init
COPY config/settings.example.toml ./config/settings.example.toml
ENV PATH="/opt/venv/bin:$PATH" ANP_ROOT=/workspace PYTHONPATH=/workspace/src:/workspace
CMD ["langgraph", "dev", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
