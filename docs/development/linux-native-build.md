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


## 已有 Linux 主机的功能验收

完整干净环境验收仍使用上面的 Debian/Alpine 容器，不要用实机结果替代无外网结论。已有 Linux 主机可显式使用 `python3 -m native.checks.platform_check --host-runtime --bundle <未运行的目录>`；回执会标注 `runtime_scope=linux-host` 和 `external_network_isolation_enforced=false`。

执行验收脚本的 Python 需要放在独立 bootstrap 目录，不能使用待验收输入目录自身的 Python：原生 Python 启动会生成该目录的 `var/cache`，导致“不得复制已有运行数据”的保护拒绝输入。bootstrap 需带上包内 `tools/python`、`tools/libc`、`tools/elf` 和 `runtime/elf_exec.py`。

源码修复版为旧 glibc 宿主增加了兼容处理：保留锁定官方静态 patchelf，在实例缓存中先设置传递性 RPATH，再修改 INTERP；不继承 `LD_LIBRARY_PATH`，避免 PostgreSQL 初始化和 Node/Python 子进程调用宿主 shell 时加载错误的 libc。运行文件仍保持不可变，搬移后重新生成缓存。

LiteLLM 平台模式增加独立数据库，构建时冻结 Prisma Python 客户端、5.17.0 CLI 和官方引擎；启动使用本地工具执行官方迁移，不在用户机器下载依赖。验收包含实际管理台登录，不能仅以模型 API 或 UI 页面 HTTP 200 代替。具体目标主机结果以对应验收报告为准。

多服务共享 cgroup 时，ClickHouse 使用 `memory_worker_use_cgroup=0` 按自身分配器统计，保留进程 4 GiB 和单查询 512 MiB 限制；否则其他应用与文件缓存可能触发其内存保护，导致 Langfuse 追踪读回 422。目标主机两种存储模式的证据与未通过项见 [实机验收报告](../acceptance/linux-host-2026-10-24.md)。

出网 DNS 还需覆盖 NSS 动态加载：构建阶段将 `native/linux/nss.c` 编译为包内 `libanp_nss.so`，只为本包进程选择内置 files/DNS 服务，不修改宿主 `/etc/nsswitch.conf`。运行时仍审计实际 maps，不能把宿主 NSS 插件加入放行清单。官方 Slim Node 的临时预载在共享库构造器中清除，并验证 shell 子进程不继承；最终用户无需编译器。

共享云数据库中，LiteLLM 迁移的同名约束/列检查须限制当前 schema；构建回执 `manifest/litellm-migration-scope.json` 记录兼容改动。Storage 的 S3 删除命令补 MD5，回执为 `manifest/storage-s3-compat.json`；Node 公共 CA 来自冻结 certifi 包。不能通过关闭 TLS 校验来解决 OSS 证书错误。
