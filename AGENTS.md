# 项目开发约定

- 所有对话、说明与文档使用中文，文档采用 Markdown。
- Python 依赖使用 uv，前端依赖使用 pnpm；提交锁文件。
- 业务只能放在 apps/<应用ID>；遵守 ui、api、nodes、flow、services 的职责边界。
- CLI 默认本地执行，HTTP 与 Web 调用同一个业务服务契约；不得绕过用户权限。
- 数据库和对象文件放在 Docker 命名卷；不使用宿主机 PostgreSQL。
- 密钥与登录会话不得提交版本库或写入浏览器公开配置。
