# ANP 开发约定

- 所有对话和文档使用中文，文档使用 Markdown。
- 项目名称为 ANP；当前工作区路径可能仍叫 anp3，不得据此改变产品名称。
- 不得操作相邻 anp、anp2 项目的服务、数据、卷和配置；Docker 默认使用独立实例 anp-next。
- 数据仅写入本工作区 var/ 或用户明确配置的独立 ANP_DATA_ROOT。当前机器已有数据根 /var/lib/anp3/data，不能因改名而迁移或覆盖。
- Python 使用 uv，前端使用 pnpm；使用 frozen 安装并提交锁文件。
- 镜像从 docker/toolbox/Dockerfile 单文件构建，最终为 Debian slim；禁止引入本机私有组件镜像依赖。
- 官方二进制必须固定 URL 和校验值；源码必须固定真实 submodule 提交；基础镜像使用 docker/build/base-images.lock.json 固定官方多架构 manifest 摘要；双架构为 linux/amd64 和 linux/arm64。
- 修改生成输入后执行 python scripts/generate_dockerfile.py 并提交生成结果；更新组件必须同步版本锁与安装检查。
- 业务对象统一通过 Supabase Data API/RPC 管理，文件通过 Supabase Storage，状态订阅通过 Realtime；业务模块不直连 PostgreSQL 或 RustFS。数据库迁移、初始化和验收脚本可使用底层连接。
- 优先官方固定发行二进制或代码包，缺少适用产物时才从锁定源码构建；不从第三方服务镜像提取运行文件。
- 业务通过 LangGraph 内置 Agent Server 的 HTTP 扩展提供路由，不另起业务HTTP服务器。前端使用 shadcn/ui。
- 未执行的检查不能写成通过。区分离线演示、真实数据库操作、真实供应商调用和远程流水线结果。
- 不读取或输出 .env、credentials.json 的实际值到日志、文档、测试快照或提交。

## 开始工作

1. 执行 make context 读取机器可读入口，make doctor 检查工具。
2. 阅读 README.md 与 docs/architecture/架构与Agent指南.md。
3. 常规业务开发不需要平台源码；make init、make up、make dev 即可。
4. 修改镜像前执行 make sources、python scripts/validate_sources.py。
5. 最小验证使用 make test；镜像或代理改动再执行真实验收，不能只看HTTP200。

## 完成标准

修改可复现，配置示例与实际一致，无秘密提交；说明测试范围、未验证事项和证据路径。需要发布时，以 GitHub Actions 和镜像 manifest 的实际状态为准。
