# 连接 LangGraph Studio

Studio 使用 LangSmith 网页账号登录，本机 Agent Server 则使用独立的引擎 Token。两项都配置完成才能显示业务图。

1. 确认平台和业务开发进程已启动；F5 模式还需要成功附加 Agent 调试器。
2. 从工作台“平台连接”打开 LangGraph Studio，登录自己的 LangSmith 账号。
3. 点击 **Server connection settings**。Base URL 默认是 `http://127.0.0.1:8124`。
4. 在 **Custom Headers** 中添加 `Authorization`，值填写 `Bearer <ENGINE_TOKEN>`。完整可复制值位于本机私有文件 `config/local-credentials.md` 的 LangGraph Studio 小节。
5. 点击 **Connect**，选择 `demo`，应能看到 normalize → analyze → save 图。

该网页说明自定义请求头保存在当前浏览器本地，并用于连接这台服务器。换浏览器、清除站点数据或更换引擎 Token 后，需要重新配置。不要把 Token 放进 Studio URL、公开前端配置或版本库。

出现 `Unauthorized. 引擎访问凭据不正确` 表示已经访问到本机服务，但请求头缺失或 Token 不匹配。检查 `Bearer` 后有一个空格，以及使用的是 ENGINE_TOKEN，而不是 Supabase 用户密码或 LangSmith API Key。

`disable_studio_auth: true` 表示 Studio 也使用本项目的自定义认证，并不表示免认证。本项目保留此认证边界，不通过关闭引擎鉴权解决连接问题。

## 直接输入文本调试

新建线程，展开 **Text**，填写文本后点击 **Submit**。Title 可选，Task Id、Attempt、Input Data 留空。Raw 输入示例：

```json
{"text":"需要分析的文本。","title":"Studio 调试"}
```

Worker 会使用本机管理员身份，经过现有权限校验创建真实业务任务，以 `studio` 模式执行并保存结果；任务会出现在工作台中。管理员密码留在 Worker，不传给 Agent 或 Studio。该入口仅面向持有 ENGINE_TOKEN 的本机开发者，不作为普通用户登录入口。

图的输入与中间状态已分开，Normalized、Analysis、Result 不需要手填。空文本会得到明确提示。已有失败线程中的历史 KeyError 不会被抹除，使用新线程验证修复。

若图已经正常显示，但页面提示缺少 `LANGSMITH_API_KEY`，这是 LangSmith 云端追踪的提示，不是本机连接失败。本项目默认把追踪发到 Langfuse，未配置 LangSmith 云端追踪。

2026-10-17 用户明确选择只使用本机 Langfuse，保留该提示；不创建云端 API Key，也不上传任务追踪到 LangSmith。Studio 浏览器交互已验证 normalize → analyze → save 完成，结果 `saved=true`。

参考：[官方自定义认证说明](https://github.com/langchain-ai/docs/blob/main/src/langsmith/set-up-custom-auth.mdx)。
