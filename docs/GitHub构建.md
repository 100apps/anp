# GitHub构建

唯一流水线为.github/workflows/ci.yaml，main推送、版本tag和手动触发会运行；PR仅运行测试。

1. 锁定uv/pnpm依赖，运行离线业务与代理/运行时测试，构建工作台。
2. 原生ubuntu-24.04与ubuntu-24.04-arm分别构建linux/amd64和linux/arm64，验证源码版本和生成Dockerfile。
3. 推送带完整提交SHA和架构后缀的候选镜像；在对应架构启动候选、执行四存储恢复、选服、平台页面/API和两个业务图验收。
4. 只有两个架构都通过，才合并manifest并更新latest和sha-<提交SHA>；版本tag另外生成同名镜像tag。

构建使用GITHUB_TOKEN写入GHCR，不需要把本机供应商Key、数据和.env放入Actions secrets。首次发布后需确认GHCR包为public，才能让匿名clone用户直接拉取。固定SHA标签用于回溯；latest是便利入口，不是组件版本锁。

输出SBOM与构建来源证明，Actions固定提交SHA；原生架构分别缓存，每个架构内构建阶段并发限制为1，避免重型编译挤占共享内存；两个架构并行运行。首次全量构建较慢，候选完成前不更新已发布latest。流水线和本机结果分别记录，不以配置文件存在代替远程成功。
