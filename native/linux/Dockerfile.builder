# 官方 Debian 固定 manifest；构建工具不进入原生发行目录。
ARG BASE_IMAGE=debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
FROM ${BASE_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive
ARG DEBIAN_MIRROR=http://deb.debian.org
# 可指定 Debian 镜像传输站；仍验证 Debian 签名与 Packages 校验和。
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv build-essential clang cmake pkg-config patchelf git \
    ca-certificates curl xz-utils unzip zstd libssl-dev libicu-dev libxml2-dev \
    libxslt1-dev zlib1g-dev libreadline-dev liblz4-dev libzstd-dev libffi-dev \
    autoconf automake libtool binutils && rm -rf /var/lib/apt/lists/*
ARG BUILD_UID=1000
ARG BUILD_GID=1000
RUN groupadd --gid ${BUILD_GID} builder && useradd --uid ${BUILD_UID} --gid ${BUILD_GID} --create-home builder
USER builder
WORKDIR /workspace
