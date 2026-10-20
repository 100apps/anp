# 单镜像内保留官方运行时需要的目录；不运行 Docker daemon，也不使用特权容器。
ARG LITELLM_IMAGE=anp3/litellm:source
ARG POSTGRES_IMAGE=supabase/postgres:17.6.1.136
ARG REDIS_IMAGE=redis:8.10.2-trixie
ARG CLICKHOUSE_IMAGE=clickhouse/clickhouse-server:26.9.22.3
ARG RUSTFS_IMAGE=rustfs/rustfs:1.0.3
ARG DBX_IMAGE=anp3/dbx:source
ARG LANGFUSE_WEB_IMAGE=anp3/langfuse-web:source
ARG LANGFUSE_WORKER_IMAGE=anp3/langfuse-worker:source
ARG STUDIO_IMAGE=anp3/supabase-studio:source
ARG AUTH_IMAGE=anp3/supabase-auth:source
ARG REST_IMAGE=anp3/supabase-rest:source
ARG META_IMAGE=anp3/supabase-meta:source
ARG STORAGE_IMAGE=anp3/supabase-storage:source
FROM ${POSTGRES_IMAGE} AS postgres
FROM ${REDIS_IMAGE} AS redis
FROM ${CLICKHOUSE_IMAGE} AS clickhouse
FROM ${RUSTFS_IMAGE} AS rustfs
FROM ${DBX_IMAGE} AS dbx
FROM ${LANGFUSE_WEB_IMAGE} AS langfuse_web
FROM ${LANGFUSE_WORKER_IMAGE} AS langfuse_worker
FROM ${STUDIO_IMAGE} AS studio
FROM ${AUTH_IMAGE} AS auth
FROM ${REST_IMAGE} AS rest
FROM ${META_IMAGE} AS meta
FROM ${STORAGE_IMAGE} AS storage
FROM node:24-bookworm-slim AS node24
FROM envoyproxy/envoy:v1.39.1 AS envoy
FROM ${LITELLM_IMAGE} AS assembled
USER root
COPY --from=postgres /nix /nix
COPY --from=postgres /docker-entrypoint-initdb.d /opt/defaults/postgres-init
COPY --from=redis /usr/local/bin/redis-* /opt/bin/
COPY --from=redis /usr/lib/x86_64-linux-gnu/libssl.so.3* /usr/lib/x86_64-linux-gnu/libcrypto.so.3* /usr/lib/x86_64-linux-gnu/libsystemd.so.0* /usr/lib/x86_64-linux-gnu/libcap.so.2* /usr/lib/x86_64-linux-gnu/libz.so.1* /usr/lib/x86_64-linux-gnu/libzstd.so.1* /opt/lib/compat/
COPY --from=clickhouse /usr/bin/clickhouse /opt/bin/clickhouse
COPY --from=rustfs /usr/bin/rustfs /opt/bin/rustfs
COPY --from=dbx /usr/local/bin/dbx-web /opt/bin/dbx-web
COPY --from=dbx --chown=20005:20005 /app /opt/apps/dbx
COPY --from=dbx /usr/lib/jvm /usr/lib/jvm
COPY --from=dbx /etc/java-17-openjdk /etc/java-17-openjdk
COPY --from=dbx /usr/lib/x86_64-linux-gnu/libfontconfig.so.1* /usr/lib/x86_64-linux-gnu/libfreetype.so.6* /usr/lib/x86_64-linux-gnu/libpng16.so.16* /usr/lib/x86_64-linux-gnu/libexpat.so.1* /usr/lib/x86_64-linux-gnu/libuuid.so.1* /usr/lib/x86_64-linux-gnu/libbrotlidec.so.1* /usr/lib/x86_64-linux-gnu/libbrotlicommon.so.1* /usr/lib/x86_64-linux-gnu/libbz2.so.1.0* /opt/lib/compat/
COPY --from=dbx /etc/fonts /etc/fonts
COPY --from=dbx /usr/share/fonts /usr/share/fonts
COPY --from=langfuse_web --chown=20006:20006 /app /opt/apps/langfuse-web
COPY --from=langfuse_web /usr/local /opt/langfuse-tools
COPY --from=langfuse_web /usr/bin/migrate /opt/bin/migrate
COPY --from=langfuse_worker --chown=20007:20007 /app/worker /opt/apps/langfuse-worker/worker
COPY --from=studio --chown=20009:20009 /app /opt/apps/supabase-studio
COPY --from=auth /usr/local/bin/auth /opt/bin/supabase-auth
COPY --from=auth /usr/local/etc/auth/migrations /opt/apps/supabase-auth/migrations
COPY --from=rest /usr/local/bin/postgrest /opt/bin/postgrest
COPY --from=meta --chown=20010:20010 /usr/src/app /opt/apps/supabase-meta
COPY --from=meta /usr/local/bin/node /opt/node22/bin/node
COPY --from=storage --chown=20013:20013 /app /opt/apps/supabase-storage
COPY --from=storage /lib /opt/runtime/storage/lib
COPY --from=storage /usr/lib /opt/runtime/storage/usr/lib
COPY --from=storage /usr/local/lib /opt/runtime/storage/usr/local/lib
COPY --from=storage /usr/local/bin/node /opt/runtime/storage/node
COPY --from=node24 /usr/local/bin/node /opt/node24/bin/node
COPY --from=envoy /usr/local/bin/envoy /opt/bin/envoy
COPY docker/toolbox/runtime /opt/anp3/runtime
COPY docker/toolbox/versions.lock.json /opt/anp3/versions.lock.json
RUN ln -sf /opt/runtime/storage/lib/ld-musl-x86_64.so.1 /lib/ld-musl-x86_64.so.1 \
    && /usr/bin/python3.13 /opt/anp3/runtime/install.py
COPY docker/toolbox/optimize.py /opt/anp3/build/optimize.py
RUN /usr/bin/python3.13 /opt/anp3/build/optimize.py --apply

# 精简在中间层执行；最终只复制干净文件系统，已删除文件不会残留在镜像历史层。
FROM scratch AS toolbox
COPY --from=assembled / /
WORKDIR /opt/anp3
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NODE_ENV=production TZ=UTC
EXPOSE 5432 6379 8123 9000 9002 9003 4224 3100 3130 4000 3200 8080 9999 3300 5000 8000 9750
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=5s --start-period=300s --retries=6 CMD ["/usr/bin/python3.13", "/opt/anp3/runtime/supervisor.py", "check"]
ENTRYPOINT ["/usr/bin/python3.13", "/opt/anp3/runtime/supervisor.py"]
