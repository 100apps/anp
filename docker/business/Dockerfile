# 使用 Docker Desktop 自带 BuildKit 前端，避免启动时拉取额外构建前端。
FROM node:24-bookworm-slim AS ui
WORKDIR /ui
RUN npm install -g pnpm@11.25.0
COPY business/ui/package.json business/ui/pnpm-lock.yaml business/ui/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store npm_config_https_proxy="$HTTPS_PROXY" pnpm install --frozen-lockfile
COPY business/ui/ ./
RUN pnpm run build

FROM python:3.12-slim AS python-deps
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY business/pyproject.toml business/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --group demo --group build --no-install-project
COPY business/src ./src
RUN --mount=type=cache,target=/root/.cache/uv /opt/venv/bin/python -m hatchling build -t wheel \
    && uv sync --frozen --no-dev --group demo --no-install-project
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt > /build/requirements.txt

FROM python:3.12-slim AS demo
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::Proxy="$HTTPS_PROXY" update \
    && apt-get -o Acquire::https::Proxy="$HTTPS_PROXY" install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/venv/bin:$PATH" ANP_UI_DIST=/app/ui LANGSMITH_TRACING=false
WORKDIR /app
COPY --from=python-deps /opt/venv /opt/venv
COPY --from=python-deps /build/dist/ /tmp/wheels/
RUN /opt/venv/bin/python -m ensurepip && /opt/venv/bin/python -m pip install --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels
COPY --from=ui /ui/dist /app/ui
COPY docker/business/langgraph.demo.json /app/langgraph.json
RUN useradd --uid 10001 --create-home anp && mkdir /app/.langgraph_api && chown -R anp:anp /app
USER anp
EXPOSE 2024
# 开发运行器仅用于 demo；不把 --no-reload 冒充生产持久化。
CMD ["langgraph", "dev", "--no-browser", "--no-reload", "--host", "0.0.0.0", "--port", "2024"]

FROM langchain/langgraph-api:3.12 AS production
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::Proxy="$HTTPS_PROXY" update \
    && apt-get -o Acquire::https::Proxy="$HTTPS_PROXY" install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
COPY --from=python-deps /build/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv uv pip install --system --require-hashes -r /tmp/requirements.txt
COPY --from=python-deps /build/dist/ /tmp/wheels/
RUN uv pip install --system --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels /tmp/requirements.txt
RUN uv pip check
COPY --from=ui /ui/dist /app/ui
ENV ANP_UI_DIST=/app/ui
ENV LANGGRAPH_HTTP='{"app":"anp_business.server.http:app"}'
ENV LANGSERVE_GRAPHS='{"workflow_demo":"anp_business.workflow_demo.graph:graph","deepagent_demo":"anp_business.deepagent_demo.agent:agent","video_dubbing":"anp_business.dubbing.graph:entry_graph"}'
# 保留官方生产镜像的入口；PG/Redis 与生产授权通过运行配置提供。

FROM python:3.12-slim AS dev
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
WORKDIR /workspace/business
# 前端使用独立 dev 服务；Python 保持官方 reload。
CMD ["uv", "run", "langgraph", "dev", "--no-browser", "--host", "0.0.0.0", "--port", "2024"]
