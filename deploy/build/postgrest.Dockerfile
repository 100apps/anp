FROM nixos/nix:latest AS build
WORKDIR /src
COPY . .
# 使用上游 flake 的 static 目标；不下载旧版本 PostgREST 代替当前源码。
RUN nix --extra-experimental-features 'nix-command flakes' build .#static \
    --out-link /out --option sandbox false --cores 4 --max-jobs 2 \
    && mkdir -p /artifact && cp -L /out/bin/postgrest /artifact/postgrest

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /artifact/postgrest /usr/local/bin/postgrest
EXPOSE 3000
ENTRYPOINT ["/usr/local/bin/postgrest"]
