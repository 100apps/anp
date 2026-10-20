# 自动生成：python scripts/generate_dockerfile.py；固定源码和产物见versions.lock.json。
# 所有组件在本文件构建，最终使用Debian slim；不依赖预先存在的anp组件镜像。
ARG STUDIO_FRAMEWORK=next
FROM debian:trixie-slim AS downloader
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends ca-certificates curl python3 xz-utils && rm -rf /var/lib/apt/lists/*
COPY docker/toolbox/fetch.py /build/fetch.py
COPY docker/toolbox/versions.lock.json /build/versions.lock.json
ARG TARGETARCH
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; python3 /build/fetch.py clickhouse "$TARGETARCH" /downloads/clickhouse && python3 /build/fetch.py rustfs "$TARGETARCH" /downloads/rustfs && python3 /build/fetch.py auth "$TARGETARCH" /downloads/auth && python3 /build/fetch.py postgrest "$TARGETARCH" /downloads/postgrest && python3 /build/fetch.py envoy "$TARGETARCH" /downloads/envoy
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; mkdir -p /out/bin /out/auth && cp $(find /downloads/clickhouse -type f -path '*/usr/bin/clickhouse' | head -1) /out/bin/clickhouse && cp $(find /downloads/rustfs -type f -name rustfs | head -1) /out/bin/rustfs && cp $(find /downloads/auth -type f -name auth | head -1) /out/bin/supabase-auth && cp $(find /downloads/postgrest -type f -name postgrest | head -1) /out/bin/postgrest && cp /downloads/envoy/envoy /out/bin/envoy && chmod 755 /out/bin/*

FROM downloader AS redis-build
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; python3 /build/fetch.py redis-source all /redis
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; cd "$(dirname "$(dirname "$(find /redis -type f -path '*/src/server.c' | head -1)")")" && make -C src -j2 BUILD_TLS=yes && make -C src PREFIX=/out/redis BUILD_TLS=yes install

FROM redis-build AS pgbouncer-build
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends libevent-dev && rm -rf /var/lib/apt/lists/*
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; curl -fsSL --retry 5 https://www.pgbouncer.org/downloads/files/1.26.0/pgbouncer-1.26.0.tar.gz -o /tmp/pgbouncer.tgz && echo 'afd25dd61ee6775d37b40629b87ce08736b3e6955f3057bb212e410fbf21c71d  /tmp/pgbouncer.tgz' | sha256sum -c - && mkdir /pgbouncer && tar xzf /tmp/pgbouncer.tgz --strip-components=1 -C /pgbouncer && cd /pgbouncer && ./configure --prefix=/out/pgbouncer --with-openssl=/usr && make -j2 pgbouncer && mkdir -p /out/pgbouncer/bin && cp pgbouncer /out/pgbouncer/bin/pgbouncer

FROM debian:trixie-slim AS postgres-build
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends nix-bin ca-certificates git curl xz-utils && rm -rf /var/lib/apt/lists/*
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; mkdir -p /etc/nix && printf '%s\n' 'experimental-features = nix-command flakes' 'sandbox = false' 'build-users-group = nixbld' 'extra-substituters = https://nix-postgres-artifacts.s3.amazonaws.com' 'extra-trusted-public-keys = nix-postgres-artifacts:dGZlQOvKcNEjvT7QEAJbcV6b6uk7VF/hWMjhYleiaLI=' > /etc/nix/nix.conf && (getent group nixbld || groupadd -r nixbld) && for i in 1 2 3 4; do id nixbld$i >/dev/null 2>&1 || useradd -r -g nixbld -G nixbld -M -N -s /usr/sbin/nologin nixbld$i; done
COPY platform/sources/components/postgres /src
WORKDIR /src
ARG TARGETARCH
RUN --mount=type=cache,id=anp-nix-${TARGETARCH},target=/nix,sharing=locked export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; nix profile install --profile /tmp/anp-pg-profile 'path:.#psql_17_slim/bin' 'path:.#supabase-groonga' && mkdir -p /out/nix/store /out/nix/var/nix/profiles /out/init && for path in $(nix-store --query --requisites "$(readlink -f /tmp/anp-pg-profile)"); do cp -a "$path" /out/nix/store/; done && ln -s "$(readlink -f /tmp/anp-pg-profile)" /out/nix/var/nix/profiles/default && test -x /out/nix/var/nix/profiles/default/bin/postgres && cp -a migrations/db/. /out/init/ && cp ansible/files/pgbouncer_config/pgbouncer_auth_schema.sql /out/init/init-scripts/00-schema.sql && cp ansible/files/stat_extension.sql /out/init/migrations/00-extension.sql


# Stage 1: Build frontend (native, no emulation)
FROM --platform=$BUILDPLATFORM node:22-slim AS dbx-frontend
ARG VITE_DBX_BASE_PATH=/dbx
ENV VITE_DBX_BASE_PATH=$VITE_DBX_BASE_PATH
WORKDIR /app
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm i -g pnpm
# Keep the workspace package available because Vite resolves it directly from source.
COPY platform/sources/dbx/package.json platform/sources/dbx/pnpm-lock.yaml platform/sources/dbx/pnpm-workspace.yaml ./
COPY platform/sources/dbx/packages/mongo-shell/ packages/mongo-shell/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; \
    pnpm install --frozen-lockfile
COPY platform/sources/dbx/apps/desktop/ apps/desktop/
# The vite connection-types plugin runs scripts/sync-connection-types.mjs during
# `pnpm build`, which reads plugins/connection-types + plugins/dialects and
# regenerates the manifest and generated TS types in place.
COPY platform/sources/dbx/scripts/ scripts/
COPY platform/sources/dbx/plugins/ plugins/
COPY platform/sources/dbx/crates/dbx-core/assets/database-drivers.manifest.json crates/dbx-core/assets/database-drivers.manifest.json
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm build


FROM rust:1-bookworm AS dbx-backend
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends build-essential cmake pkg-config libfontconfig1-dev libfreetype6-dev libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY platform/sources/dbx/ .
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/src/target export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; cargo build --locked --release -p dbx-web && mkdir -p /out && cp target/release/dbx-web /out/dbx-web

FROM node:24-bookworm-slim AS lf-web-base
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends build-essential python3 curl git openssl ca-certificates && rm -rf /var/lib/apt/lists/*
FROM lf-web-base AS lf-web-build-base
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm install -g turbo@2.11.7 pnpm@12.8.2
ARG NODE_USE_ENV_PROXY
FROM lf-web-base AS lf-web-runtime-base
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; groupadd -g 1001 nodejs && useradd -u 1001 -g nodejs nextjs && groupadd -g 1002 expressjs && useradd -u 1002 -g expressjs expressjs
FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-web-build-base AS lf-web-web-tools
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm install -g --prefix /opt/prisma-cli --no-package-lock --no-save prisma@6.19.3
ARG NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; mkdir -p /opt/dd/node_modules && \
    if [ -n "$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION" ]; then \
    cd /opt/dd && npm install --no-package-lock --no-save dd-trace@5.109.0; \
    fi

FROM --platform=${BUILDPLATFORM} golang:1.26 AS lf-web-migrate-builder
ARG TARGETOS
ARG TARGETARCH
ENV CGO_ENABLED=0 \
    GOBIN=/out \
    GOOS=${TARGETOS} \
    GOARCH=${TARGETARCH}
# Build against the validated Go Cryptographic Module snapshot (Go >= 1.24) so
# the migrate CLI's ClickHouse TLS uses FIPS-approved crypto; GOFIPS140 also
# defaults GODEBUG=fips140=on at runtime.
ENV GOFIPS140=v1.0.0
# Build only the ClickHouse migrate CLI Langfuse uses at runtime.
# compile this ourselves instead of downloading the upstream release
# because prebuilt bins bundle many unused drivers and thus inherit CVEs
# eg.: https://github.com/golang-migrate/migrate/issues/1357
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; /usr/local/go/bin/go install -trimpath -tags 'clickhouse' -ldflags='-s -w' \
    github.com/golang-migrate/migrate/v4/cmd/migrate@v4.19.1

FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-web-build-base AS lf-web-pruner

WORKDIR /app

COPY platform/sources/langfuse/. .
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; turbo prune web --docker

FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-web-build-base AS lf-web-builder

WORKDIR /app

# First install the dependencies (as they change less often)
COPY --from=lf-web-pruner /app/out/pnpm-lock.yaml ./pnpm-lock.yaml
COPY --from=lf-web-pruner /app/out/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=lf-web-pruner /app/out/json/ .

RUN --mount=type=cache,target=/root/.cache/pnpm export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm install --frozen-lockfile --store-dir /root/.cache/pnpm

# pnpm can verify dependencies before each `pnpm run`, so every command
# spawned by turbo re-checks node_modules via file mtimes. Cached install
# layers make those mtimes look stale, and the concurrent auto-installs race
# in the hoisting step (ENOENT unlink in node_modules/.pnpm/node_modules).
# The frozen-lockfile install above is authoritative; skip the check.
ENV pnpm_config_verify_deps_before_run=false

ENV DOCKER_BUILD 1
ENV NEXT_MANUAL_SIG_HANDLE true

# Allows the CI docker build smoke test to skip the Next.js type check that
# the lint job already runs (`pnpm run typecheck`). Release builds do not set
# this and still fail on TypeScript errors.
ARG NEXT_IGNORE_BUILD_ERRORS
ENV NEXT_IGNORE_BUILD_ERRORS=$NEXT_IGNORE_BUILD_ERRORS

# pass public variables in build step
ARG NEXT_PUBLIC_PLAIN_APP_ID
ARG NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ENV NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ARG NEXT_PUBLIC_DEMO_ORG_ID
ENV NEXT_PUBLIC_DEMO_ORG_ID=$NEXT_PUBLIC_DEMO_ORG_ID
ARG NEXT_PUBLIC_DEMO_PROJECT_ID
ENV NEXT_PUBLIC_DEMO_PROJECT_ID=$NEXT_PUBLIC_DEMO_PROJECT_ID
ARG NEXT_PUBLIC_SIGN_UP_DISABLED
ENV NEXT_PUBLIC_SIGN_UP_DISABLED=$NEXT_PUBLIC_SIGN_UP_DISABLED
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY
ENV NEXT_PUBLIC_TURNSTILE_SITE_KEY=$NEXT_PUBLIC_TURNSTILE_SITE_KEY
ARG NEXT_PUBLIC_PREVIEW_PR_URL
ENV NEXT_PUBLIC_PREVIEW_PR_URL=$NEXT_PUBLIC_PREVIEW_PR_URL
ARG NEXT_PUBLIC_PREVIEW_PR_AUTHOR
ENV NEXT_PUBLIC_PREVIEW_PR_AUTHOR=$NEXT_PUBLIC_PREVIEW_PR_AUTHOR
ARG NEXT_PUBLIC_PREVIEW_LAST_UPDATED
ENV NEXT_PUBLIC_PREVIEW_LAST_UPDATED=$NEXT_PUBLIC_PREVIEW_LAST_UPDATED
ARG NEXT_PUBLIC_PREVIEW_DEMO_AUTO_SIGN_IN
ENV NEXT_PUBLIC_PREVIEW_DEMO_AUTO_SIGN_IN=$NEXT_PUBLIC_PREVIEW_DEMO_AUTO_SIGN_IN
ARG NEXT_PUBLIC_POSTHOG_KEY
ENV NEXT_PUBLIC_POSTHOG_KEY=$NEXT_PUBLIC_POSTHOG_KEY
ARG NEXT_PUBLIC_POSTHOG_HOST
ENV NEXT_PUBLIC_POSTHOG_HOST=$NEXT_PUBLIC_POSTHOG_HOST
ARG NEXT_PUBLIC_LANGFUSE_TRACING_SAMPLE_RATE
ENV NEXT_PUBLIC_LANGFUSE_TRACING_SAMPLE_RATE=$NEXT_PUBLIC_LANGFUSE_TRACING_SAMPLE_RATE
ARG NEXT_PUBLIC_SENTRY_ENVIRONMENT
ENV NEXT_PUBLIC_SENTRY_ENVIRONMENT=$NEXT_PUBLIC_SENTRY_ENVIRONMENT
ARG NEXT_PUBLIC_SENTRY_DSN
ENV NEXT_PUBLIC_SENTRY_DSN=$NEXT_PUBLIC_SENTRY_DSN
ARG NEXT_PUBLIC_BASE_PATH=/langfuse
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
# Origin serving this build's /_next/static/* output. Must be a build arg: the
# bundler writes it into the client runtime's public path, so it cannot be
# changed by a runtime environment variable.
ARG NEXT_PUBLIC_ASSET_PREFIX
ENV NEXT_PUBLIC_ASSET_PREFIX=$NEXT_PUBLIC_ASSET_PREFIX

# Sentry already needs to be set on build time to upload sourcemaps
# This must not be set for OSS releases as we would share our Sentry secret.
ARG SENTRY_AUTH_TOKEN
ENV SENTRY_AUTH_TOKEN=$SENTRY_AUTH_TOKEN
ARG SENTRY_ORG
ENV SENTRY_ORG=$SENTRY_ORG
ARG SENTRY_PROJECT
ENV SENTRY_PROJECT=$SENTRY_PROJECT

# Accept build id as NEXT_PUBLIC_BUILD_ID
ARG NEXT_PUBLIC_BUILD_ID
ENV NEXT_PUBLIC_BUILD_ID=$NEXT_PUBLIC_BUILD_ID
ENV SENTRY_RELEASE=$NEXT_PUBLIC_BUILD_ID

# Copy source code of isolated subworkspace
COPY --from=lf-web-pruner /app/out/full/ .

# Keep the documented direct golang-migrate recovery paths available in the
# production image while maintaining canonical/ as the only authored tree.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; set -eu; \
    node ./packages/shared/clickhouse/scripts/prepare-migrations.mjs materialize ./packages/shared/clickhouse/migrations; \
    chmod 755 ./packages/shared/clickhouse/migrations/clustered ./packages/shared/clickhouse/migrations/unclustered

# remove middleware.ts if it exists - not needed in self-hosted environments
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; rm -f ./web/src/middleware.ts

# Next.js collects completely anonymous telemetry data about general usage.
# Learn more here: https://nextjs.org/telemetry
# Uncomment the following line in case you want to disable telemetry during the build.
ENV NEXT_TELEMETRY_DISABLED 1
ENV NEXT_MANUAL_SIG_HANDLE true
# set the CI flag to true to get CI specific logs
ENV CI true

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; NODE_OPTIONS='--max-old-space-size-percentage=75' turbo run build --filter=web...

# Production image, copy all the files and run next
FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-web-runtime-base AS lf-web-runner

ARG TARGETPLATFORM
ARG BUILDPLATFORM

WORKDIR /app

ARG NEXT_PUBLIC_BUILD_ID
ENV BUILD_ID=$NEXT_PUBLIC_BUILD_ID
ARG NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ENV NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION

ENV NODE_ENV production
# Uncomment the following line in case you want to disable telemetry during runtime.
ENV NEXT_TELEMETRY_DISABLED 1
# Needed to re-enable validation of environment variables during runtime
ENV DOCKER_BUILD 0
# Set NEXT_MANUAL_SIG_HANDLE for runtime
ENV NEXT_MANUAL_SIG_HANDLE true

# The non-root user (nextjs, uid 1001) is baked into the runtime rootfs by
# the rpm-installer stage, since micro has no shadow-utils.

# prisma CLI from the tools stage (bin/prisma symlink + lib/node_modules
# structure lands under /usr/local); the runtime image has no npm.
COPY --from=lf-web-web-tools /opt/prisma-cli /usr/local

# dd-trace, npm-installed in the tools stage (node_modules is empty unless
# NEXT_PUBLIC_LANGFUSE_CLOUD_REGION was set); the standalone output copied
# below merges on top of it.
COPY --from=lf-web-web-tools --chown=nextjs:nodejs /opt/dd/node_modules ./node_modules

COPY --from=lf-web-migrate-builder /out/migrate /usr/bin/migrate

COPY --from=lf-web-builder --chown=nextjs:nodejs /app/web/next.config.mjs .
COPY --from=lf-web-builder --chown=nextjs:nodejs /app/web/package.json .

# Automatically leverage output traces to reduce image size
# https://nextjs.org/docs/advanced-features/output-file-tracing
COPY --from=lf-web-builder --chown=nextjs:nodejs /app/web/.next/standalone ./
COPY --from=lf-web-builder --chown=nextjs:nodejs /app/web/.next/static ./web/.next/static
COPY --from=lf-web-builder --chown=nextjs:nodejs /app/web/public ./web/public

COPY --from=lf-web-builder --chown=nextjs:nodejs /app/packages/shared/prisma ./packages/shared/prisma
COPY --from=lf-web-builder --chown=nextjs:nodejs /app/packages/shared/clickhouse ./packages/shared/clickhouse

COPY --chown=nextjs:nodejs platform/sources/langfuse/web/entrypoint.sh ./web/entrypoint.sh
COPY --chown=nextjs:nodejs platform/sources/langfuse/packages/shared/scripts/cleanup.sql ./packages/shared/scripts/cleanup.sql
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; chmod +x ./web/entrypoint.sh

# Numeric USER (uid of nextjs) so runAsNonRoot checks and Iron Bank hardening
# validation can verify the user without resolving names.
ARG UID=1001
USER ${UID}

# Default port to 3000
ENV PORT 3000

# Docker ENTRYPOINT (dumb-init) is covered by semantic versioning, not the entrypoint.sh itself
# Reasoning: ENTRYPOINT is overridden by some self-hosted deployments, thus changing this is breaking
ENTRYPOINT ["dumb-init", "--", "./web/entrypoint.sh"]

# startup command - use dd-trace if NEXT_PUBLIC_LANGFUSE_CLOUD_REGION is configured
CMD if [ -n "$NEXT_PUBLIC_LANGFUSE_CLOUD_REGION" ]; then \
    node --import dd-trace/initialize.mjs ./web/server.js --keepAliveTimeout 110000; \
    else \
    node ./web/server.js --keepAliveTimeout 110000; \
    fi

FROM node:24-bookworm-slim AS lf-worker-base
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends build-essential python3 curl git openssl ca-certificates && rm -rf /var/lib/apt/lists/*
FROM lf-worker-base AS lf-worker-build-base
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm install -g turbo@2.11.7 pnpm@12.8.2
ARG NODE_USE_ENV_PROXY
FROM lf-worker-base AS lf-worker-runtime-base
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; groupadd -g 1001 nodejs && useradd -u 1001 -g nodejs nextjs && groupadd -g 1002 expressjs && useradd -u 1002 -g expressjs expressjs
FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-worker-build-base AS lf-worker-pruner

WORKDIR /app

COPY platform/sources/langfuse/. .
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; turbo prune worker --docker

# UBI build toolchain: RPM Node, pnpm, turbo, gcc and Rust. CI's tests-fips job
# also runs the web and worker test suites on this stage with the OpenSSL FIPS
# provider forced on (scripts/ci/fips-tests.sh).
FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-worker-build-base AS lf-worker-toolchain

ARG TARGETARCH

# @langfuse/native is a Rust addon (napi-rs) compiled by `turbo run build` for
# the glibc (`*-unknown-linux-gnu`) target, linked against UBI9's glibc so it
# loads in the runtime image. gcc provides the linker; tar and gzip unpack the
# pinned toolchain.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends gcc tar gzip && rm -rf /var/lib/apt/lists/*

# Pinned standalone Rust installer, the exact channel from
# packages/native/rust-toolchain.toml (bump the two together), checksum-verified
# like the dumb-init binary above. Only rustc, cargo and the target's std are
# needed to build; clippy and rustfmt run outside Docker.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; set -eux; \
    case "${TARGETARCH:-amd64}" in \
    amd64) triple=x86_64-unknown-linux-gnu; toolchain_sha256=aa30409afa67bd1ada244cefd82c7980e6a65bc113bb978e934b2413c75e3900 ;; \
    arm64) triple=aarch64-unknown-linux-gnu; toolchain_sha256=5fbb4282403046d52a4672765c6761a809bf9f33e699b17e6eb7a93ab7770cc3 ;; \
    *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/rust.tar.gz "https://static.rust-lang.org/dist/rust-1.98.0-${triple}.tar.gz"; \
    echo "${toolchain_sha256}  /tmp/rust.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/rust.tar.gz -C /tmp; \
    /tmp/rust-1.98.0-${triple}/install.sh --prefix=/usr/local \
    --components=rustc,cargo,rust-std-${triple}; \
    rm -rf /tmp/rust.tar.gz /tmp/rust-1.98.0-${triple}

# cargo-about renders the third-party license notices for the native addon's
# Rust dependencies. cargo-auditable embeds the addon's resolved dependency
# list in the .node binary so image scanners (Trivy, Grype, Docker Scout) can
# match its Rust crates against advisories. Both build from crates.io at pinned
# versions with their own lockfiles; keep the versions in sync with
# ai-gateway/Dockerfile. `napi build` spawns $CARGO instead of cargo when it is
# set, so the build step points it at a wrapper that runs `cargo auditable`.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; cargo install --locked --root /usr/local --features cli cargo-about@0.9.2 && \
    cargo install --locked --root /usr/local cargo-auditable@0.7.7 && \
    rm -rf "${CARGO_HOME:-$HOME/.cargo}/registry" && \
    printf '#!/bin/sh\nexec /usr/local/bin/cargo auditable "$@"\n' > /usr/local/bin/cargo-auditable-shim && \
    chmod 0755 /usr/local/bin/cargo-auditable-shim

FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-worker-toolchain AS lf-worker-builder

WORKDIR /app

# First install the dependencies (as they change less often)
COPY --from=lf-worker-pruner /app/out/pnpm-lock.yaml ./pnpm-lock.yaml
COPY --from=lf-worker-pruner /app/out/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=lf-worker-pruner /app/out/json/ .

RUN --mount=type=cache,target=/root/.cache/pnpm export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm install --frozen-lockfile --store-dir /root/.cache/pnpm

# pnpm can verify dependencies before each `pnpm run`, so every command
# spawned by turbo re-checks node_modules via file mtimes. Cached install
# layers make those mtimes look stale, and the concurrent auto-installs race
# in the hoisting step (ENOENT unlink in node_modules/.pnpm/node_modules).
# The frozen-lockfile install above is authoritative; skip the check.
ENV pnpm_config_verify_deps_before_run=false

# pass public variables in build step
ARG NEXT_PUBLIC_LANGFUSE_CLOUD_REGION
ARG NEXT_PUBLIC_DEMO_ORG_ID
ARG NEXT_PUBLIC_DEMO_PROJECT_ID
ARG NEXT_PUBLIC_POSTHOG_KEY
ARG NEXT_PUBLIC_POSTHOG_HOST

# Copy source code of isolated subworkspace
COPY --from=lf-worker-pruner /app/out/full/ .

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; CARGO=/usr/local/bin/cargo-auditable-shim turbo run build --filter=worker...

# Runs after the build so `cargo fetch` adds the sources the build skipped (dev
# and other-target dependencies) that cargo metadata still reads; `generate`
# then reads licenses only from local crate sources and fails on any dependency
# whose license packages/native/about.toml does not accept.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; cd packages/native && \
    mkdir -p /usr/share/licenses/langfuse-native && \
    cargo fetch --locked && \
    cargo about generate --frozen --fail about.hbs \
    -o /usr/share/licenses/langfuse-native/THIRD_PARTY_LICENSES.txt

FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-worker-builder AS lf-worker-prod-deps

# previously we copied the --from=builder /app . (includes full node_modules etc)
# we only need the prod + generated prisma client plus .prisma artifacts
# @langfuse/shared still pulls in next-auth transitively, so keep the deploy output
# intact here instead of pruning node_modules in Docker.
# pnpm deploy materializes linked workspace packages into the standalone artifact.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm --filter worker deploy --prod /prod/worker && \
    builder_prisma_client_dir="$(find /app/node_modules/.pnpm -path '*/node_modules/@prisma/client' -type d | head -n 1)" && \
    deployed_prisma_client_dir="$(find /prod/worker/node_modules/.pnpm -path '*/node_modules/@prisma/client' -type d | head -n 1)" && \
    builder_prisma_runtime_dir="$(dirname "$(dirname "$builder_prisma_client_dir")")/.prisma" && \
    deployed_prisma_runtime_dir="$(dirname "$(dirname "$deployed_prisma_client_dir")")/.prisma" && \
    rm -rf "$deployed_prisma_client_dir" "$deployed_prisma_runtime_dir" && \
    cp -R "$builder_prisma_client_dir" "$deployed_prisma_client_dir" && \
    cp -R "$builder_prisma_runtime_dir" "$deployed_prisma_runtime_dir"

FROM --platform=${TARGETPLATFORM:-linux/amd64} lf-worker-runtime-base AS lf-worker-runner

ARG TARGETPLATFORM
ARG BUILDPLATFORM

WORKDIR /app

ARG NEXT_PUBLIC_BUILD_ID
ENV BUILD_ID=$NEXT_PUBLIC_BUILD_ID

ENV NODE_ENV production
ENV DOCKER_BUILD 0

# The non-root user (expressjs, uid 1001) is baked into the runtime rootfs by
# the rpm-installer stage, since micro has no shadow-utils.

# Copy only production worker payload instead of full builder workspace (just /prod/worker not entire /app)
COPY --from=lf-worker-prod-deps --chown=expressjs:expressjs /prod/worker ./worker
COPY --from=lf-worker-builder /usr/share/licenses/langfuse-native /usr/share/licenses/langfuse-native
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; chmod +x ./worker/entrypoint.sh
# Numeric USER (uid of expressjs) so runAsNonRoot checks and Iron Bank
# hardening validation can verify the user without resolving names.
ARG UID=1001
USER ${UID}

EXPOSE 3030
ENV PORT=3030

# Docker ENTRYPOINT (dumb-init) is covered by semantic versioning, not the entrypoint.sh itself
# Reasoning: ENTRYPOINT is overridden by some self-hosted deployments, thus changing this is breaking
ENTRYPOINT ["dumb-init", "--", "./worker/entrypoint.sh"]

# startup command
CMD ["node", "worker/dist/index.js"]

# To be run in the root of the turbo monorepo
# NOTE: It's highly recommended to use the new builder, Buildkit. https://docs.docker.com/build/buildkit/
## USAGE:
# Build (Next):     docker build . -f apps/studio/Dockerfile --target production -t studio:latest
# Build (TanStack): docker build . -f apps/studio/Dockerfile --target production -t studio:latest --build-arg STUDIO_FRAMEWORK=tanstack
# Run:          docker run -p 3000:3000 supabase/studio
# Deploy:       docker push supabase/studio:latest
# Clean build:
#    docker builder prune
#    docker build . -f apps/studio/Dockerfile --target production -t studio:latest --no-cache

# Which framework's build ends up in the image. This is the same variable
# scripts/dispatch.js keys on for the dev/build/start scripts, so
# `--build-arg STUDIO_FRAMEWORK=tanstack` is the docker spelling of the
# switch used everywhere else. Framework selection happens at image build
# time (the two runtimes need different build outputs and dependency
# trees), not at container start.


FROM node:22-bookworm-slim AS studio-base
ENV pnpm_config_verify_deps_before_run=false pnpm_config_store_dir=/root/.cache/pnpm
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

# Fixes issues with Sentry CLI and SSL certificates during build
# TODO: Git is added because it's needed to build libpg, remove it once they publish a binary on the S3 bucket
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update -qq && \
  apt-get install -y --no-install-recommends \
  git \
  python3 \
  ca-certificates \
  build-essential && \
  rm -rf /var/lib/apt/lists/* && \
  update-ca-certificates

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm install -g pnpm@11.13.1

WORKDIR /app

# Prune unneeded dependencies with turbo (from apps/ for example)
FROM studio-base AS studio-turbo
COPY platform/sources/supabase/. .

RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm dlx turbo@2.9.14 prune studio --docker

# Install dev dependencies (only if needed)
FROM studio-base AS studio-deps
COPY --from=studio-turbo /app/out/json ./
COPY --from=studio-turbo /app/out/pnpm-lock.yaml ./
COPY platform/sources/supabase/patches/ ./patches

# No need to clean cache because production uses standalone build
RUN --mount=type=cache,target=/root/.cache/pnpm export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm install --frozen-lockfile --store-dir /root/.cache/pnpm

# dev contains dependencies and source code not compiled
FROM studio-deps AS studio-dev
COPY --from=studio-turbo /app/out/full ./
ENTRYPOINT ["docker-entrypoint.sh"]
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; python3 -c 'exec('"'"'"""仅在镜像构建目录修复锁定 Studio 源码中的根路径假设。"""\nfrom pathlib import Path\n\n\ndef replace(relative, old, new, count):\n    path = Path("apps/studio") / relative\n    text = path.read_text()\n    assert text.count(old) == count, f"上游已变更，请重新审核目录补丁：{relative}"\n    path.write_text(text.replace(old, new))\n\n\nreplace("lib/constants/index.ts", "return \'"'"'/api\'"'"'",\n        "return (process.env.NEXT_PUBLIC_BASE_PATH ?? \'"'"'\'"'"') + \'"'"'/api\'"'"'", 2)\nreplace("lib/constants/api.ts", "${PUBLIC_URL.origin}/rest/v1/",\n        "${PUBLIC_URL.origin}${PUBLIC_URL.pathname.replace(/\\\\/$/, \'"'"'\'"'"')}/rest/v1/", 1)\nreplace("lib/constants/api.ts", "PROJECT_ENDPOINT = PUBLIC_URL.host",\n        "PROJECT_ENDPOINT = PUBLIC_URL.host + PUBLIC_URL.pathname.replace(/\\\\/$/, \'"'"'\'"'"')", 1)\nfor name, variable in (("public-url", "publicUrl"), ("sign", "signedUrl"), ("sign-multi", "signedUrl")):\n    replace(f"pages/api/platform/storage/[ref]/buckets/[id]/objects/{name}.ts",\n            f"{variable}.port = parsed.port",\n            f"{variable}.port = parsed.port\\n  {variable}.pathname = parsed.pathname.replace(/\\\\/$/, \'"'"'\'"'"') + {variable}.pathname", 1)\n'"'"')'
EXPOSE 8082
CMD ["pnpm", "dev:studio"]

# Compile Next.js
FROM studio-dev AS studio-build-next
ARG NEXT_PUBLIC_BASE_PATH=/supabase
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm --filter studio exec next build

# Assemble the runtime tree at /srv in the layout the production stage
# serves from: Next's self-contained standalone output is the app root,
# with the static assets and public/ laid alongside it.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; mkdir -p /srv && \
  cp -a apps/studio/.next/standalone/. /srv/ && \
  mkdir -p /srv/apps/studio/.next && \
  cp -a apps/studio/.next/static /srv/apps/studio/.next/static && \
  cp -a apps/studio/public /srv/apps/studio/public

# Compile TanStack Start (Vite)
FROM studio-dev AS studio-build-tanstack
ARG NEXT_PUBLIC_BASE_PATH=/supabase
ENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH
# build:tanstack = vite build --mode production, then a smoke test that
# boots the server bundle so module-scope crashes fail the image build.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; NODE_OPTIONS=--max-old-space-size=4096 pnpm --filter studio run build:tanstack

# Assemble the runtime tree at /srv. Unlike Next's standalone output, the
# Vite SSR bundle externalizes studio's dependencies and resolves them from
# node_modules at request time, so the tree is a prod-only `pnpm deploy` of
# studio (node_modules + manifest) plus the built dist/ and the runtime
# scripts. `scripts/serve.js` is the HTTP server (the same entry
# start:tanstack uses); the server.js shim gives the production stage a
# single CMD that works for both frameworks. `.env` is kept because
# serve.js loads it as the base of the runtime env cascade (container env
# vars always win over file values).
#
# --ignore-scripts: pnpm 11 hard-errors (ERR_PNPM_IGNORED_BUILDS) on
# dependency build scripts without an allowBuilds entry, and deploy turns
# the workspace packages into file: deps whose `only-allow pnpm` preinstall
# guards trip it. No lifecycle script is needed here anyway: the tree is
# fully prebuilt, and nothing in studio's prod graph is approved to build
# (allowBuilds only permits node-pty and supabase, both dev-only).
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; pnpm --filter studio deploy --prod --legacy --ignore-scripts /srv/apps/studio && \
  cd /srv/apps/studio && \
  find . -mindepth 1 -maxdepth 1 \
  ! -name node_modules ! -name package.json ! -name scripts \
  ! -name instrument.server.mjs ! -name .env \
  -exec rm -rf {} + && \
  cp -a /app/apps/studio/dist ./dist && \
  printf "import('./scripts/serve.js')\n" > server.js

# Boot the pruned tree exactly the way the container will run it, so a
# dependency that's runtime-imported but missing from `dependencies`
# (present only in devDependencies) fails the build here instead of
# 500ing the deployed container.
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; cd /srv/apps/studio && node scripts/smoke-server.mjs

# Alias whichever framework build was selected so the production stage can
# COPY from a single stage name. BuildKit only builds the selected branch.
FROM studio-build-${STUDIO_FRAMEWORK} AS studio-build

# Copy only compiled code and dependencies
FROM studio-base AS studio-production
COPY --from=studio-build /srv ./
# serve.js (TanStack) defaults to port 8082; pin both servers to the port
# the healthcheck and compose files expect. Next's server.js reads PORT too.
ENV PORT=3000
EXPOSE 3000
ENTRYPOINT ["docker-entrypoint.sh"]
HEALTHCHECK --interval=5s --timeout=5s --retries=3 CMD node -e "fetch('http://localhost:3000/supabase/api/platform/profile').then((r) => {if (r.status !== 200) throw new Error(r.status)})"
CMD ["node", "apps/studio/server.js"]

FROM node:22-bookworm-slim AS meta-build
WORKDIR /usr/src/app
# Do `npm ci` separately so we can cache `node_modules`
# https://nodejs.org/en/docs/guides/nodejs-docker-webapp/
COPY platform/sources/components/postgres-meta/package.json platform/sources/components/postgres-meta/package-lock.json ./
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm clean-install
COPY platform/sources/components/postgres-meta/. .
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm run build && npm prune --omit=dev

FROM node:22-bookworm-slim
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/src/app
COPY --from=meta-build /usr/src/app/node_modules node_modules
COPY --from=meta-build /usr/src/app/dist dist
COPY platform/sources/components/postgres-meta/package.json ./
ENV PG_META_PORT=8080
# `npm run start` does not forward signals to child process
CMD ["node", "dist/server/server.js"]
EXPOSE 8080
# --start-period defaults to 0s, but can't be set to 0s (to be explicit) by now
HEALTHCHECK --interval=5s --timeout=5s --retries=3 CMD node -e "fetch('http://localhost:8080/health').then((r) => {if (r.status !== 200) throw new Error(r.status)})"

# Base stage for shared environment setup
FROM node:24-bookworm-slim AS storage-base
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends g++ make python3 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY platform/sources/components/storage/package.json platform/sources/components/storage/package-lock.json ./
COPY platform/sources/components/storage/scripts/ensure-npm-version.cjs ./scripts/ensure-npm-version.cjs
# The optional `npmrc` build secret lets CI route registry traffic through a
# different registry layer.
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; node scripts/ensure-npm-version.cjs

# Dependencies stage - install and cache all dependencies
FROM storage-base AS storage-dependencies
RUN --mount=type=cache,target=/root/.cache --mount=type=secret,id=npmrc,target=/root/.npmrc export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm ci --ignore-scripts
# Native addons can use the Node headers already included in the image.
RUN --network=none export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm_package_config_node_gyp_nodedir=/usr/local npm rebuild
# Cache the installed node_modules for later stages
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; cp -R node_modules /node_modules_cache

# Build stage - use cached node_modules for building the application
FROM storage-base AS storage-build
COPY --from=storage-dependencies /node_modules_cache ./node_modules
COPY platform/sources/components/storage/. .
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm run build

# Production dependencies stage - prune dev dependencies from cached node_modules
FROM storage-base AS storage-production-deps
COPY --from=storage-dependencies /node_modules_cache ./node_modules
# Use npm prune to remove dev dependencies while keeping compiled native modules
# Remove dev dependencies and keep native modules without downloading replacements or running scripts.
RUN --network=none export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm prune --omit=dev --offline --ignore-scripts

# Final stage - for the production build
FROM storage-base AS storage-final
ARG VERSION
ENV VERSION=$VERSION
COPY platform/sources/components/storage/migrations migrations

# Copy production node_modules from the production dependencies stage
COPY --from=storage-production-deps /app/node_modules node_modules
# Copy build artifacts from the build stage
COPY --from=storage-build /app/dist dist
COPY --from=storage-build /app/watt.json /app
COPY --from=storage-build /app/watt.storage.json /app

EXPOSE 5000
CMD ["node", "dist/start/server.js"]

FROM node:24-bookworm-slim AS litellm-ui
WORKDIR /ui
COPY platform/sources/litellm/ui/litellm-dashboard/package*.json ./
RUN --mount=type=cache,target=/root/.cache export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm ci
COPY platform/sources/litellm/ui/litellm-dashboard/ ./
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; npm run build -- --webpack

FROM debian:trixie-slim AS litellm-build
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-dev build-essential libssl-dev libsndfile1 ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
COPY --from=lf-web-base /usr/local/ /usr/local/
WORKDIR /app
COPY platform/sources/litellm/ ./
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin
RUN --mount=type=cache,target=/root/.cache export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; uv sync --frozen --no-install-project --no-install-workspace --no-default-groups --no-editable --extra proxy --extra proxy-runtime --group admin-mcp --extra extra_proxy --extra semantic-router --extra saml --extra bedrock-realtime --python /usr/bin/python3
COPY --from=litellm-ui /ui/out/ /app/litellm/proxy/_experimental/out/
RUN --mount=type=cache,target=/root/.cache export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; uv sync --frozen --no-default-groups --no-editable --extra proxy --extra proxy-runtime --group admin-mcp --extra extra_proxy --extra semantic-router --extra saml --extra bedrock-realtime --python /usr/bin/python3
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; HOME=/opt/prisma XDG_CACHE_HOME=/opt/prisma/.cache PRISMA_BINARY_CACHE_DIR=/opt/prisma/binaries prisma generate --schema=./schema.prisma
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; curl -fsSL https://github.com/BerriAI/litellm-admin-agent/archive/ef501e94bc9fbacb9233b922abf71427f030408c.tar.gz -o /tmp/liteadmin.tgz && echo '2f7ae5cdd9d91731c0990e74a58239dc3e3fd2bf28dab23b55eafcdc47aaf87e  /tmp/liteadmin.tgz' | sha256sum -c - && mkdir /tmp/liteadmin && tar xzf /tmp/liteadmin.tgz --strip-components=1 -C /tmp/liteadmin && uv venv /opt/liteadmin --python /usr/bin/python3 && uv --no-config pip install --python /opt/liteadmin/bin/python --require-hashes -r /tmp/liteadmin/requirements.txt && uv --no-config pip install --python /opt/liteadmin/bin/python --no-deps /tmp/liteadmin

FROM debian:trixie-slim AS assembled
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates bash openssl tzdata libsndfile1 libssl3t64 libatomic1 libstdc++6 fontconfig fonts-dejavu-core libfreetype6 openjdk-21-jre-headless libevent-2.1-7t64 libpq5 libgmp10 libnuma1 libtinfo6 && rm -rf /var/lib/apt/lists/* /var/cache/apt/*
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
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; ln -s /opt/anp/runtimes/node24/bin/node /usr/bin/node && /usr/bin/python3 /opt/anp/runtime/install.py && /usr/bin/python3 /opt/anp/build/optimize.py --apply
FROM debian:trixie-slim AS toolbox
COPY --from=assembled / /
WORKDIR /opt/anp
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NODE_ENV=production TZ=UTC
LABEL org.opencontainers.image.title="ANP" org.opencontainers.image.source="https://github.com/100apps/anp"
EXPOSE 5432 6379 8123 9000 9002 9003 4224 3100 4000 3200 8000 9750
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=5s --start-period=300s --retries=6 CMD ["/usr/bin/python3", "/opt/anp/runtime/supervisor.py", "check"]
ENTRYPOINT ["/usr/bin/python3", "/opt/anp/runtime/supervisor.py"]
