"""从锁定的生产配方生成单文件构建；构建阶段不引用本机组件镜像。"""
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / 'docker/build/recipes'


def namespace(text, prefix, context):
    stages = re.findall(r'^FROM .* AS ([\w-]+)', text, re.M | re.I)
    mapping = {stage: prefix + '-' + stage for stage in stages}
    lines = []
    for line in text.splitlines():
        if line.startswith('# syntax='):
            continue
        if line.startswith('FROM '):
            parts = line.split()
            for i, part in enumerate(parts):
                if part in mapping:
                    parts[i] = mapping[part]
                elif part == 'build-${STUDIO_FRAMEWORK}':
                    parts[i] = prefix + '-build-${STUDIO_FRAMEWORK}'
            line = ' '.join(parts)
        elif line.startswith('COPY '):
            # 此处的上游配方全部使用单行、shell形式COPY。
            parts = shlex.split(line)
            if any(p.startswith('--from=') for p in parts):
                parts = ['--from=' + mapping.get(p[7:], p[7:]) if p.startswith('--from=') else p for p in parts]
            else:
                for i in range(1, len(parts) - 1):
                    if not parts[i].startswith('--'):
                        parts[i] = context + '/' + parts[i].removeprefix('./')
            line = ' '.join(parts)
        lines.append(line)
    return '\n'.join(lines) + '\n'


def langfuse(name):
    text = (RECIPES / f'langfuse/{name}.Dockerfile.in').read_text()
    marker = 'web-tools' if name == 'web' else 'pruner'
    start = re.search(r'^FROM .* AS ' + marker + '$', text, re.M).start()
    text = '''FROM node:24-bookworm-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends build-essential python3 curl git openssl ca-certificates && rm -rf /var/lib/apt/lists/*
FROM base AS build-base
RUN npm install -g turbo@2.11.7 pnpm@12.8.2
ARG NODE_USE_ENV_PROXY
FROM base AS runtime-base
RUN groupadd -g 1001 nodejs && useradd -u 1001 -g nodejs nextjs && groupadd -g 1002 expressjs && useradd -u 1002 -g expressjs expressjs
''' + text[start:]
    text = text.replace('dnf -y install --setopt=install_weak_deps=0 gcc tar gzip && \\\n    dnf clean all', 'apt-get update && apt-get install -y --no-install-recommends gcc tar gzip && rm -rf /var/lib/apt/lists/*')
    text = text.replace('ARG NEXT_PUBLIC_BASE_PATH\n', 'ARG NEXT_PUBLIC_BASE_PATH=/langfuse\n')
    return namespace(text, 'lf-' + name, 'platform/sources/langfuse')


def main():
    chunks = ['''# 自动生成：python scripts/generate_dockerfile.py；固定源码和产物见versions.lock.json。
# 所有组件在本文件构建，最终使用Debian slim；不依赖预先存在的anp组件镜像。
ARG STUDIO_FRAMEWORK=next
FROM debian:trixie-slim AS downloader
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl python3 xz-utils && rm -rf /var/lib/apt/lists/*
COPY docker/toolbox/fetch.py /build/fetch.py
COPY docker/toolbox/versions.lock.json /build/versions.lock.json
ARG TARGETARCH
RUN python3 /build/fetch.py clickhouse "$TARGETARCH" /downloads/clickhouse && python3 /build/fetch.py rustfs "$TARGETARCH" /downloads/rustfs && python3 /build/fetch.py auth "$TARGETARCH" /downloads/auth && python3 /build/fetch.py postgrest "$TARGETARCH" /downloads/postgrest && python3 /build/fetch.py envoy "$TARGETARCH" /downloads/envoy
RUN mkdir -p /out/bin /out/auth && cp $(find /downloads/clickhouse -type f -path '*/usr/bin/clickhouse' | head -1) /out/bin/clickhouse && cp $(find /downloads/rustfs -type f -name rustfs | head -1) /out/bin/rustfs && cp $(find /downloads/auth -type f -name auth | head -1) /out/bin/supabase-auth && cp $(find /downloads/postgrest -type f -name postgrest | head -1) /out/bin/postgrest && cp /downloads/envoy/envoy /out/bin/envoy && chmod 755 /out/bin/*

FROM downloader AS redis-build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
RUN python3 /build/fetch.py redis-source all /redis
RUN cd "$(dirname "$(dirname "$(find /redis -type f -path '*/src/server.c' | head -1)")")" && make -C src -j2 BUILD_TLS=yes && make -C src PREFIX=/out/redis BUILD_TLS=yes install

FROM redis-build AS pgbouncer-build
RUN apt-get update && apt-get install -y --no-install-recommends libevent-dev && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL --retry 5 https://www.pgbouncer.org/downloads/files/1.26.0/pgbouncer-1.26.0.tar.gz -o /tmp/pgbouncer.tgz && echo 'afd25dd61ee6775d37b40629b87ce08736b3e6955f3057bb212e410fbf21c71d  /tmp/pgbouncer.tgz' | sha256sum -c - && mkdir /pgbouncer && tar xzf /tmp/pgbouncer.tgz --strip-components=1 -C /pgbouncer && cd /pgbouncer && ./configure --prefix=/out/pgbouncer --with-openssl=/usr && make -j2 pgbouncer && mkdir -p /out/pgbouncer/bin && cp pgbouncer /out/pgbouncer/bin/pgbouncer

FROM debian:trixie-slim AS postgres-build
RUN apt-get update && apt-get install -y --no-install-recommends nix-bin ca-certificates git curl xz-utils && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /etc/nix && printf '%s\\n' 'experimental-features = nix-command flakes' 'sandbox = false' 'build-users-group = nixbld' 'extra-substituters = https://nix-postgres-artifacts.s3.amazonaws.com' 'extra-trusted-public-keys = nix-postgres-artifacts:dGZlQOvKcNEjvT7QEAJbcV6b6uk7VF/hWMjhYleiaLI=' > /etc/nix/nix.conf && (getent group nixbld || groupadd -r nixbld) && for i in 1 2 3 4; do id nixbld$i >/dev/null 2>&1 || useradd -r -g nixbld -G nixbld -M -N -s /usr/sbin/nologin nixbld$i; done
COPY platform/sources/components/postgres /src
WORKDIR /src
ARG TARGETARCH
RUN --mount=type=cache,id=anp-nix-${TARGETARCH},target=/nix,sharing=locked nix profile install --profile /tmp/anp-pg-profile 'path:.#psql_17_slim/bin' 'path:.#supabase-groonga' && mkdir -p /out/nix/store /out/nix/var/nix/profiles /out/init && for path in $(nix-store --query --requisites "$(readlink -f /tmp/anp-pg-profile)"); do cp -a "$path" /out/nix/store/; done && ln -s "$(readlink -f /tmp/anp-pg-profile)" /out/nix/var/nix/profiles/default && test -x /out/nix/var/nix/profiles/default/bin/postgres && cp -a migrations/db/. /out/init/ && cp ansible/files/pgbouncer_config/pgbouncer_auth_schema.sql /out/init/init-scripts/00-schema.sql && cp ansible/files/stat_extension.sql /out/init/migrations/00-extension.sql
''']
    dbx = (RECIPES / 'dbx/Dockerfile.in').read_text()
    frontend = dbx[:dbx.index('# Stage 2:')]
    chunks.append(namespace(frontend, 'dbx', 'platform/sources/dbx'))
    chunks.append('''FROM rust:1-bookworm AS dbx-backend
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake pkg-config libfontconfig1-dev libfreetype6-dev libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY platform/sources/dbx/ .
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/target cargo build --locked --release -p dbx-web && mkdir -p /out && cp target/release/dbx-web /out/dbx-web
''')
    chunks += [langfuse('web'), langfuse('worker')]
    studio = (RECIPES / 'supabase/studio.Dockerfile.in').read_text()
    studio = studio.replace('ARG STUDIO_FRAMEWORK=next', '').replace('node:22-slim', 'node:22-bookworm-slim')
    studio = studio.replace('AS base\n', 'AS base\nENV pnpm_config_verify_deps_before_run=false pnpm_config_store_dir=/root/.cache/pnpm\n', 1)
    chunks.append(namespace(studio, 'studio', 'platform/sources/supabase'))
    meta = (RECIPES / 'supabase/meta.Dockerfile.in').read_text().replace('node:22 AS build', 'node:22-bookworm-slim AS build').replace('node:22-slim', 'node:22-bookworm-slim')
    chunks.append(namespace(meta, 'meta', 'platform/sources/components/postgres-meta'))
    storage = (RECIPES / 'supabase/storage.Dockerfile.in').read_text().replace('node:24-alpine3.23', 'node:24-bookworm-slim').replace('apk add --no-cache g++ make python3', 'apt-get update && apt-get install -y --no-install-recommends g++ make python3 && rm -rf /var/lib/apt/lists/*')
    chunks.append(namespace(storage, 'storage', 'platform/sources/components/storage'))
    chunks.append('''FROM node:24-bookworm-slim AS litellm-ui
WORKDIR /ui
COPY platform/sources/litellm/ui/litellm-dashboard/package*.json ./
RUN npm ci
COPY platform/sources/litellm/ui/litellm-dashboard/ ./
RUN npm run build -- --webpack

FROM debian:trixie-slim AS litellm-build
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-dev build-essential libssl-dev libsndfile1 ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
COPY --from=lf-web-base /usr/local/ /usr/local/
WORKDIR /app
COPY platform/sources/litellm/ ./
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin
RUN uv sync --frozen --no-install-project --no-install-workspace --no-default-groups --no-editable --extra proxy --extra proxy-runtime --group admin-mcp --extra extra_proxy --extra semantic-router --extra saml --extra bedrock-realtime --python /usr/bin/python3
COPY --from=litellm-ui /ui/out/ /app/litellm/proxy/_experimental/out/
RUN uv sync --frozen --no-default-groups --no-editable --extra proxy --extra proxy-runtime --group admin-mcp --extra extra_proxy --extra semantic-router --extra saml --extra bedrock-realtime --python /usr/bin/python3
RUN HOME=/opt/prisma XDG_CACHE_HOME=/opt/prisma/.cache PRISMA_BINARY_CACHE_DIR=/opt/prisma/binaries prisma generate --schema=./schema.prisma
RUN curl -fsSL https://github.com/BerriAI/litellm-admin-agent/archive/ef501e94bc9fbacb9233b922abf71427f030408c.tar.gz -o /tmp/liteadmin.tgz && echo '2f7ae5cdd9d91731c0990e74a58239dc3e3fd2bf28dab23b55eafcdc47aaf87e  /tmp/liteadmin.tgz' | sha256sum -c - && mkdir /tmp/liteadmin && tar xzf /tmp/liteadmin.tgz --strip-components=1 -C /tmp/liteadmin && uv venv /opt/liteadmin --python /usr/bin/python3 && uv --no-config pip install --python /opt/liteadmin/bin/python --require-hashes -r /tmp/liteadmin/requirements.txt && uv --no-config pip install --python /opt/liteadmin/bin/python --no-deps /tmp/liteadmin

FROM debian:trixie-slim AS assembled
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates bash openssl tzdata libsndfile1 libssl3t64 libatomic1 libstdc++6 fontconfig fonts-dejavu-core libfreetype6 openjdk-21-jre-headless libevent-2.1-7t64 libpq5 libgmp10 libnuma1 libtinfo6 && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
COPY --from=postgres-build /out/nix /nix
COPY --from=postgres-build /out/init /opt/defaults/postgres-init
COPY --from=downloader /out/bin/ /opt/anp/bin/
COPY --from=redis-build /out/redis/bin/ /opt/anp/bin/
COPY --from=pgbouncer-build /out/pgbouncer/bin/pgbouncer /opt/anp/bin/pgbouncer
COPY --from=dbx-backend /out/dbx-web /opt/anp/bin/dbx-web
COPY --from=dbx-frontend --chown=20005:20005 /app/dist /opt/anp/services/dbx/static
COPY --from=lf-web-runner --chown=20006:20006 /app /opt/anp/services/langfuse-web
COPY --from=lf-web-web-tools /opt/prisma-cli /opt/langfuse-tools
COPY --from=lf-web-migrate-builder /out/migrate /opt/anp/bin/migrate
COPY --from=lf-worker-runner --chown=20007:20007 /app/worker /opt/anp/services/langfuse-worker/worker
COPY --from=lf-worker-builder /usr/share/licenses/langfuse-native /usr/share/licenses/langfuse-native
COPY --from=studio-production --chown=20009:20009 /app /opt/anp/services/supabase-studio
COPY --from=meta-build --chown=20010:20010 /usr/src/app/dist /opt/anp/services/supabase-meta/dist
COPY --from=meta-build --chown=20010:20010 /usr/src/app/node_modules /opt/anp/services/supabase-meta/node_modules
COPY --from=meta-build /usr/src/app/package.json /opt/anp/services/supabase-meta/package.json
COPY --from=storage-final --chown=20013:20013 /app /opt/anp/services/supabase-storage
COPY platform/sources/components/auth/migrations /opt/anp/services/supabase-auth/migrations
COPY --from=lf-web-base /usr/local/bin/node /opt/anp/runtimes/node24/bin/node
COPY --from=studio-base /usr/local/bin/node /opt/anp/runtimes/node22/bin/node
COPY --from=litellm-build /app/.venv /app/.venv
COPY --from=litellm-build /opt/prisma /opt/prisma
COPY --from=litellm-build /opt/liteadmin /opt/liteadmin
COPY --from=litellm-build /app/docker /app/docker
COPY --from=litellm-build /app/enterprise /app/enterprise
COPY --from=litellm-build /app/litellm-proxy-extras /app/litellm-proxy-extras
COPY --from=litellm-build /app/litellm/proxy/prisma_migration.py /app/litellm/proxy/prisma_migration.py
COPY --from=litellm-build /app/schema.prisma /app/schema.prisma
COPY docker/licenses /usr/share/licenses/anp
COPY docker/toolbox/runtime /opt/anp/runtime
COPY docker/toolbox/versions.lock.json /opt/anp/versions.lock.json
COPY docker/toolbox/optimize.py /opt/anp/build/optimize.py
RUN ln -s /opt/anp/runtimes/node24/bin/node /usr/bin/node && /usr/bin/python3 /opt/anp/runtime/install.py && /usr/bin/python3 /opt/anp/build/optimize.py --apply
FROM debian:trixie-slim AS toolbox
COPY --from=assembled / /
WORKDIR /opt/anp
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NODE_ENV=production TZ=UTC
LABEL org.opencontainers.image.title="ANP" org.opencontainers.image.source="https://github.com/100apps/anp"
EXPOSE 5432 6379 8123 9000 9002 9003 4224 3100 4000 3200 8000 9750
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=5s --start-period=300s --retries=6 CMD ["/usr/bin/python3", "/opt/anp/runtime/supervisor.py", "check"]
ENTRYPOINT ["/usr/bin/python3", "/opt/anp/runtime/supervisor.py"]
''')
    result = '\n'.join(chunks)
    # 代理仅使用Docker预定义构建参数，不写入运行镜像ENV。
    result = re.sub(r'^RUN ((?:--[^ ]+ )*)', r'RUN \1export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; ', result, flags=re.M)
    # 共享下载缓存，在其它构建阶段失败重试时保留已校验的包。
    result = re.sub(r'^(RUN )((?:--[^ ]+ )*)(.*pnpm install --frozen-lockfile.*)$', lambda m: m[1] + '--mount=type=cache,target=/root/.cache/pnpm ' + m[2] + m[3].replace('pnpm install --frozen-lockfile', 'pnpm install --frozen-lockfile --store-dir /root/.cache/pnpm'), result, flags=re.M)
    result = re.sub(r'^RUN (.*uv (?:sync|pip).*|.*npm ci.*)$', r'RUN --mount=type=cache,target=/root/.cache \1', result, flags=re.M)
    (ROOT / 'docker/toolbox/Dockerfile').write_text(result, encoding='utf-8', newline='\n')
    print('已生成单文件Debian构建配方')


if __name__ == '__main__':
    main()
