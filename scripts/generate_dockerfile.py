"""从锁定的生产配方生成单文件构建；构建阶段不引用本机组件镜像。"""
from pathlib import Path
import re
import json
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
    if name == 'web':
        # 上游支持独立类型检查；先释放 Next 打包进程，再运行同一锁定编译器。
        # 两步都必须通过，避免打包和类型检查各自保留约 6-7GB 时挤占 16GB 主机。
        text = text.replace("NODE_OPTIONS='--max-old-space-size-percentage=75' turbo run build --filter=web...",
                            "NEXT_IGNORE_BUILD_ERRORS=true NODE_OPTIONS='--max-old-space-size=4096' turbo run build --filter=web... && NODE_OPTIONS='--max-old-space-size-percentage=75' pnpm --filter web run typecheck")
        # Next 使用宿主 os.cpus() 决定页面 worker 数，CFS 配额不限制该返回值。
        # Studio 和 Langfuse 均限制为两个页面 worker，避免 32 核宿主 OOM。
        # Langfuse 的 Next 16.3 已移除 turbopackMemoryLimit，仅 Studio 16.2 设置它。
        worker_patch = ("from pathlib import Path; p=Path('web/next.config.mjs'); s=p.read_text(); old="
                        + repr("\n  experimental: {")
                        + "; assert s.count(old)==1; p.write_text(s.replace(old,old+"
                        + repr("\n    cpus: 2,") + ",1))")
        text = text.replace('rm -f ./web/src/middleware.ts',
                            'rm -f ./web/src/middleware.ts && python3 -c ' + shlex.quote(worker_patch))
        begin = text.index('FROM --platform=${BUILDPLATFORM} golang:')
        end = text.index('FROM --platform=${TARGETPLATFORM:-linux/amd64} build-base AS pruner', begin)
        text = text[:begin] + 'FROM debian:trixie-slim AS migrate-builder\nCOPY --from=downloader /out/bin/migrate /out/migrate\n\n' + text[end:]
    if name == 'worker':
        begin = text.rfind('RUN ', 0, text.index('cargo install --locked --root'))
        end = text.index('\n\n', begin)
        text = text[:begin] + 'COPY --from=cargo-tools /out/bin/ /usr/local/bin/\n' + "RUN cargo auditable --version && printf '#!/bin/sh\\nexec /usr/local/bin/cargo auditable \"$@\"\\n' > /usr/local/bin/cargo-auditable-shim && chmod 0755 /usr/local/bin/cargo-auditable-shim\n" + text[end:]
    text = text.replace('dnf -y install --setopt=install_weak_deps=0 gcc tar gzip && \\\n    dnf clean all', 'apt-get update && apt-get install -y --no-install-recommends gcc tar gzip && rm -rf /var/lib/apt/lists/*')
    text = text.replace('ARG NEXT_PUBLIC_BASE_PATH\n', 'ARG NEXT_PUBLIC_BASE_PATH=/langfuse\n')
    return namespace(text, 'lf-' + name, 'platform/sources/langfuse')


def main():
    chunks = ['''# 自动生成：python scripts/generate_dockerfile.py；固定源码和产物见versions.lock.json。
# 所有组件在本文件构建，最终使用Debian slim；不依赖预先存在的anp组件镜像。
ARG STUDIO_FRAMEWORK=next
FROM debian:trixie-slim AS artifact-fetch
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl python3 xz-utils && rm -rf /var/lib/apt/lists/*
COPY docker/toolbox/fetch.py /build/fetch.py
COPY docker/toolbox/versions.lock.json /build/versions.lock.json
ARG TARGETARCH
FROM artifact-fetch AS downloader
RUN --mount=type=cache,target=/root/.cache/anp-artifacts python3 /build/fetch.py clickhouse "$TARGETARCH" /downloads/clickhouse && python3 /build/fetch.py rustfs "$TARGETARCH" /downloads/rustfs && python3 /build/fetch.py auth "$TARGETARCH" /downloads/auth && python3 /build/fetch.py postgrest "$TARGETARCH" /downloads/postgrest && python3 /build/fetch.py envoy "$TARGETARCH" /downloads/envoy && python3 /build/fetch.py migrate "$TARGETARCH" /downloads/migrate && python3 /build/fetch.py dbx "$TARGETARCH" /downloads/dbx && python3 /build/fetch.py redis-server "$TARGETARCH" /downloads/redis && python3 /build/fetch.py redis-tools "$TARGETARCH" /downloads/redis
RUN mkdir -p /out/bin /out/auth && cp $(find /downloads/clickhouse -type f -path '*/usr/bin/clickhouse' | head -1) /out/bin/clickhouse && cp $(find /downloads/rustfs -type f -name rustfs | head -1) /out/bin/rustfs && cp $(find /downloads/auth -type f -name auth | head -1) /out/bin/supabase-auth && cp $(find /downloads/postgrest -type f -name postgrest | head -1) /out/bin/postgrest && cp /downloads/envoy/envoy /out/bin/envoy && cp $(find /downloads/dbx -type f -name dbx-web-bin | head -1) /out/bin/dbx-web && cp -a /downloads/redis/usr/bin/* /out/bin/ && cp $(find /downloads/migrate -type f -name migrate | head -1) /out/bin/migrate && chmod 755 /out/bin/*

FROM artifact-fetch AS runtimes
RUN --mount=type=cache,target=/root/.cache/anp-artifacts python3 /build/fetch.py node24 "$TARGETARCH" /downloads/node24 && python3 /build/fetch.py node22 "$TARGETARCH" /downloads/node22 && python3 /build/fetch.py uv "$TARGETARCH" /downloads/uv
RUN mkdir -p /out/bin /out/node24 /out/node22 && cp -a /downloads/node24/node-*/. /out/node24/ && cp -a /downloads/node22/node-*/. /out/node22/ && cp $(find /downloads/uv -type f -name uv | head -1) /out/bin/uv && chmod 755 /out/bin/uv

FROM artifact-fetch AS cargo-tools
COPY docker/build/tools.lock.json /build/versions.lock.json
RUN --mount=type=cache,target=/root/.cache/anp-artifacts python3 /build/fetch.py cargo-about "$TARGETARCH" /downloads/about && python3 /build/fetch.py cargo-auditable "$TARGETARCH" /downloads/auditable
RUN mkdir -p /out/bin && cp $(find /downloads/about -type f -name cargo-about | head -1) /out/bin/ && cp $(find /downloads/auditable -type f -name cargo-auditable | head -1) /out/bin/ && chmod 755 /out/bin/* && /out/bin/cargo-about --version

FROM debian:trixie-slim AS postgres-build
RUN apt-get update && apt-get install -y --no-install-recommends nix-bin ca-certificates git curl xz-utils && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /etc/nix && printf '%s\\n' 'experimental-features = nix-command flakes' 'sandbox = false' 'build-users-group = nixbld' 'extra-substituters = https://nix-postgres-artifacts.s3.amazonaws.com' 'extra-trusted-public-keys = nix-postgres-artifacts:dGZlQOvKcNEjvT7QEAJbcV6b6uk7VF/hWMjhYleiaLI=' > /etc/nix/nix.conf && (getent group nixbld || groupadd -r nixbld) && for i in 1 2 3 4; do id nixbld$i >/dev/null 2>&1 || useradd -r -g nixbld -G nixbld -M -N -s /usr/sbin/nologin nixbld$i; done
COPY platform/sources/components/postgres /src
WORKDIR /src
ARG TARGETARCH
RUN --mount=type=cache,id=anp-nix-${TARGETARCH},target=/nix,sharing=locked nix profile install --profile /tmp/anp-pg-profile 'path:.#psql_17_slim/bin' 'path:.#supabase-groonga' && mkdir -p /out/nix/store /out/nix/var/nix/profiles /out/init && for path in $(nix-store --query --requisites "$(readlink -f /tmp/anp-pg-profile)"); do cp -a "$path" /out/nix/store/; done && ln -s "$(readlink -f /tmp/anp-pg-profile)" /out/nix/var/nix/profiles/default && test -x /out/nix/var/nix/profiles/default/bin/postgres && cp -a migrations/db/. /out/init/ && cp ansible/files/stat_extension.sql /out/init/migrations/00-extension.sql
''']
    chunks.append('''FROM hexpm/elixir:1.19.5-erlang-28.5.0.4-debian-trixie-20260713-slim AS realtime-build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=runtimes /out/node24/ /usr/local/
ENV MIX_ENV=prod ERL_FLAGS="+S 4:4"
WORKDIR /src
RUN mix local.hex --force && mix local.rebar --force
COPY platform/sources/components/realtime/mix.exs platform/sources/components/realtime/mix.lock ./
COPY platform/sources/components/realtime/forum forum
RUN cp mix.lock /tmp/mix.lock && mix deps.get --only prod && cmp mix.lock /tmp/mix.lock
COPY platform/sources/components/realtime/config/config.exs platform/sources/components/realtime/config/prod.exs config/
# 仅在跨架构仿真时显式传入 +JMsingle true；原生构建默认沿用上游 JIT。
ARG REALTIME_BUILD_ERL_AFLAGS=
ENV ERL_AFLAGS=$REALTIME_BUILD_ERL_AFLAGS
RUN mix deps.compile
COPY platform/sources/components/realtime/priv priv
COPY platform/sources/components/realtime/lib lib
COPY platform/sources/components/realtime/assets assets
RUN cd assets && npm ci --omit=dev
RUN mix assets.deploy && mix compile
COPY platform/sources/components/realtime/config/runtime.exs config/
COPY platform/sources/components/realtime/rel rel
RUN mix release && test -x _build/prod/rel/realtime/bin/realtime
''')
    chunks += [langfuse('web'), langfuse('worker')]
    studio = (RECIPES / 'supabase/studio.Dockerfile.in').read_text()
    # 锁定快照保留上游配方，ANP 适配逻辑统一从 patches 读取。
    patch = (ROOT / 'docker/patches/supabase/studio_prefix.py').read_text()
    lines = studio.splitlines()
    matches = [i for i, line in enumerate(lines) if line.startswith('RUN ') and 'python3 -c ' in line]
    assert len(matches) == 1, 'Studio 配方入口已变更，请审核补丁插入位置'
    index = matches[0]
    lines[index] = lines[index].split('python3 -c ', 1)[0] + 'python3 -c ' + shlex.quote('exec(' + repr(patch) + ')')
    studio = '\n'.join(lines) + '\n'
    studio = studio.replace('ARG STUDIO_FRAMEWORK=next', '').replace('node:22-slim', 'node:22-bookworm-slim')
    studio = studio.replace('AS base\n', 'AS base\nENV pnpm_config_verify_deps_before_run=false pnpm_config_store_dir=/root/.cache/pnpm\n', 1)
    studio = studio.replace('ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH', 'ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH\nENV NEXT_PUBLIC_DISABLED_FEATURES=project_edge_function:all,logs:all')
    studio = studio.replace('pnpm --filter studio exec next build', 'NODE_OPTIONS=--max-old-space-size=4096 pnpm --filter studio exec next build')
    chunks.append(namespace(studio, 'studio', 'platform/sources/supabase'))
    chunks.append('''FROM node:22-bookworm-slim AS meta-build
WORKDIR /usr/src/app
COPY docker/packages/meta/package.json docker/packages/meta/package-lock.json ./
RUN npm ci --omit=dev && test -f node_modules/@supabase/postgres-meta/dist/server/server.js
''')
    storage = (RECIPES / 'supabase/storage.Dockerfile.in').read_text().replace('node:24-alpine3.23', 'node:24-bookworm-slim').replace('apk add --no-cache g++ make python3', 'apt-get update && apt-get install -y --no-install-recommends g++ make python3 && rm -rf /var/lib/apt/lists/*')
    chunks.append(namespace(storage, 'storage', 'platform/sources/components/storage'))
    chunks.append('''FROM debian:trixie-slim AS litellm-build
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-dev build-essential libssl-dev libsndfile1 ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=runtimes /out/bin/uv /usr/local/bin/uv
COPY --from=runtimes /out/node24/ /usr/local/
WORKDIR /app
COPY docker/packages/litellm/ ./
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin
RUN uv sync --frozen --no-dev --python /usr/bin/python3 --no-build-package litellm
RUN cp /app/.venv/lib/python*/site-packages/litellm/proxy/schema.prisma /app/schema.prisma
RUN HOME=/opt/prisma XDG_CACHE_HOME=/opt/prisma/.cache PRISMA_BINARY_CACHE_DIR=/opt/prisma/binaries prisma generate --schema=./schema.prisma
RUN curl -fsSL https://github.com/BerriAI/litellm-admin-agent/archive/ef501e94bc9fbacb9233b922abf71427f030408c.tar.gz -o /tmp/liteadmin.tgz && echo '2f7ae5cdd9d91731c0990e74a58239dc3e3fd2bf28dab23b55eafcdc47aaf87e  /tmp/liteadmin.tgz' | sha256sum -c - && mkdir /tmp/liteadmin && tar xzf /tmp/liteadmin.tgz --strip-components=1 -C /tmp/liteadmin && uv venv /opt/liteadmin --python /usr/bin/python3 && uv --no-config pip install --python /opt/liteadmin/bin/python --require-hashes -r /tmp/liteadmin/requirements.txt && uv --no-config pip install --python /opt/liteadmin/bin/python --no-deps /tmp/liteadmin

FROM debian:trixie-slim AS assembled
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates bash openssl tzdata libsndfile1 libssl3t64 libatomic1 libstdc++6 fontconfig fonts-dejavu-core libfreetype6 openjdk-21-jre-headless libncurses6 libpq5 libgmp10 libnuma1 libtinfo6 && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
COPY --from=postgres-build /out/nix /nix
COPY --from=postgres-build /out/init /opt/defaults/postgres-init
COPY --from=downloader /out/bin/ /opt/anp/bin/
COPY --from=lf-web-runner --chown=20006:20006 /app /opt/anp/services/langfuse-web
COPY --from=lf-web-web-tools /opt/prisma-cli /opt/langfuse-tools
COPY --from=lf-web-migrate-builder /out/migrate /opt/anp/bin/migrate
COPY --from=lf-worker-runner --chown=20007:20007 /app/worker /opt/anp/services/langfuse-worker/worker
COPY --from=lf-worker-builder /usr/share/licenses/langfuse-native /usr/share/licenses/langfuse-native
COPY --from=studio-production --chown=20009:20009 /app /opt/anp/services/supabase-studio
COPY --from=meta-build --chown=20010:20010 /usr/src/app /opt/anp/services/supabase-meta
COPY --from=storage-final --chown=20013:20013 /app /opt/anp/services/supabase-storage
COPY --from=realtime-build --chown=20015:20015 /src/_build/prod/rel/realtime /opt/anp/services/supabase-realtime
COPY platform/sources/components/auth/migrations /opt/anp/services/supabase-auth/migrations
COPY --from=runtimes /out/node24/bin/node /opt/anp/runtimes/node24/bin/node
COPY --from=runtimes /out/node22/bin/node /opt/anp/runtimes/node22/bin/node
COPY --from=litellm-build /app/.venv /app/.venv
COPY --from=litellm-build /opt/prisma /opt/prisma
COPY --from=litellm-build /opt/liteadmin /opt/liteadmin
COPY --from=litellm-build /app/schema.prisma /app/schema.prisma
COPY docker/licenses /usr/share/licenses/anp
COPY docker/toolbox/runtime /opt/anp/runtime
COPY docker/toolbox/versions.lock.json /opt/anp/versions.lock.json
COPY docker/build/tools.lock.json /opt/anp/build-tools.lock.json
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
    # 限制并发下载，避免代理连接被大量包请求挤断；失败重试仍由官方源返回并校验。
    result = result.replace('pnpm install --frozen-lockfile', 'pnpm install --frozen-lockfile --network-concurrency=8')
    result = result.replace('npm ci', 'npm ci --cache=/root/.cache/npm --maxsockets=4 --fetch-retries=5')
    result = result.replace('npm install -g --prefix /opt/prisma-cli', 'npm install -g --cache=/root/.cache/npm --maxsockets=4 --fetch-retries=5 --fetch-retry-mintimeout=2000 --fetch-retry-maxtimeout=20000 --prefix /opt/prisma-cli')

    # 代理仅使用Docker预定义构建参数，不写入运行镜像ENV。
    result = re.sub(r'^RUN ((?:--[^ ]+ )*)', r'RUN \1export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; ', result, flags=re.M)
    # 共享下载缓存，在其它构建阶段失败重试时保留已校验的包。
    result = re.sub(r'^(RUN )((?:--[^ ]+ )*)(.*pnpm install --frozen-lockfile.*)$', lambda m: m[1] + '--mount=type=cache,target=/root/.cache/pnpm ' + m[2] + m[3].replace('pnpm install --frozen-lockfile', 'pnpm install --frozen-lockfile --store-dir /root/.cache/pnpm'), result, flags=re.M)
    result = re.sub(r'^RUN (.*uv (?:sync|pip).*|.*npm ci.*|.*npm install -g --cache=.*)$', r'RUN --mount=type=cache,target=/root/.cache \1', result, flags=re.M)
    # 固定官方多架构 manifest，避免浮动标签漂移使同一源码重试变成全量重编译。
    bases = json.loads((ROOT / 'docker/build/base-images.lock.json').read_text())['images']
    for image, metadata in bases.items():
        result = re.sub(r'^(FROM (?:--[^ ]+ )?)' + re.escape(image) + r'(?= AS |\n)',
                        lambda match: match[1] + image + '@' + metadata['digest'], result, flags=re.M)
    # 固定官方代码包仍需抵抗代理偶发断线；校验值保持不变。
    result = result.replace('curl -fsSL https://github.com/BerriAI/litellm-admin-agent/',
                            'curl --retry 5 --retry-all-errors --retry-delay 2 --connect-timeout 20 --max-time 300 -fsSL https://github.com/BerriAI/litellm-admin-agent/')
    (ROOT / 'docker/toolbox/Dockerfile').write_text(result, encoding='utf-8', newline='\n')
    print('已生成单文件Debian构建配方')


if __name__ == '__main__':
    main()
