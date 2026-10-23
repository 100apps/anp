# Linux 原生包干净构建


以下命令在 Linux x64 主机上，以对当前项目目录有写权限的普通用户执行；构建器和业务运行容器分开，构建工具不会进入发行目录。

```sh
make native-sources NATIVE_TARGET=linux-amd64
docker build --build-arg BUILD_UID="$(id -u)" --build-arg BUILD_GID="$(id -g)" \
  -f native/linux/Dockerfile.builder -t anp-native-builder:local native/linux
docker run --rm -v "$PWD:/workspace" anp-native-builder:local \
  make build NATIVE_TARGET=linux-amd64
make native-check NATIVE_TARGET=linux-amd64
make dist NATIVE_TARGET=linux-amd64
python3 native/linux_check.py --archive var/exports/anp-0.3.0-poc-linux-amd64.tar.gz
```

默认使用官方镜像名称和锁定摘要；访问受限时可以在构建参数 `BASE_IMAGE` 或验收环境变量 `ANP_RUNTIME_REGISTRY` 指定镜像传输站，摘要仍需与锁一致。`ANP_WHEELHOUSE` 是可选的构建缓存目录：从同一冻结锁导出依赖，离线安装仍校验哈希，不改变依赖解析结果。
