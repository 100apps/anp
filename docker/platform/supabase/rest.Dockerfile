# 从固定 submodule 的 Nix 静态包构建，不下载 PostgREST 发布二进制。
FROM nixos/nix:2.28.3 AS builder
WORKDIR /src
COPY . .
RUN echo 'experimental-features = nix-command flakes' >> /etc/nix/nix.conf
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY"; nix build --accept-flake-config path:/src#static --out-link /out
FROM debian:bookworm-slim
RUN export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY"; apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /out/bin/postgrest /usr/local/bin/postgrest
USER 65532:65532
EXPOSE 3000
ENTRYPOINT ["postgrest"]
