# GitHub构建

唯一流水线为.github/workflows/ci.yaml，main推送、版本tag和手动触发会运行；PR仅运行测试。

1. 锁定uv/pnpm依赖，运行离线业务与代理/运行时测试，构建工作台。
2. 原生ubuntu-24.04与ubuntu-24.04-arm分别构建linux/amd64和linux/arm64，验证源码版本和生成Dockerfile。
3. 推送带完整提交SHA和架构后缀的候选镜像；在对应架构启动候选、执行四存储恢复、选服、平台页面/API和两个业务图验收。
4. 只有两个架构都通过，才合并manifest并更新latest和sha-<提交SHA>；版本tag另外生成同名镜像tag。

构建使用GITHUB_TOKEN写入GHCR，不需要把本机供应商Key、数据和.env放入Actions secrets。首次发布后需确认GHCR包为public，才能让匿名clone用户直接拉取。固定SHA标签用于回溯；latest是便利入口，不是组件版本锁。

输出SBOM与构建来源证明，Actions固定提交SHA；每个架构内构建阶段并发限制为1，避免重型编译挤占共享内存；两个架构并行运行。首次全量构建较慢，候选完成前不更新已发布latest。流水线和本机结果分别记录，不以配置文件存在代替远程成功。

中间层缓存使用同一GHCR包的buildcache-amd64、buildcache-arm64独立标签，max模式保留多阶段产物，普通用户拉取latest不会下载这些构建缓存。缓存使用较快的Zstd级别3，发布镜像仍使用级别15；缓存导出失败不阻断镜像构建，实际启动验收仍必须通过。迁移阶段继续读取旧GHA缓存，不再写入。

首次实测双架构缓存已接近10GB且仍在上传，故采用registry cache，避免GitHub Actions默认10GB仓库缓存额度造成互相淘汰。没有提高付费缓存额度。依据：[Docker registry cache](https://docs.docker.com/build/cache/backends/registry/)、[GitHub缓存规则](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)。
