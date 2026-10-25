# ANP Business

可复制的业务示例，使用 LangGraph 内置 Agent Server 的 HTTP 扩展。界面使用 React 与 shadcn/ui，Python 依赖由 uv 和 `uv.lock` 管理。

## 独立使用

复制整个 `business/` 目录，准备 uv、Python 3.12 或 3.13、Node.js 24 和 pnpm 11.25.0：

```sh
cp config.example.json config.json
make install
make build
make start
```

访问 `http://127.0.0.1:20260/workbench/`。原生 ANP 发行包已提供构建后的界面和运行依赖，执行包根目录 `./anp start` 即可。

`config.json` 配置 Supabase API、DBX、Langfuse、LiteLLM 和 Studio 地址，默认均为 `127.0.0.1`。配置文件与 `.env` 不进入 Git。独立部署需要填写 Supabase 的公开 anon key；服务端验证登录时调用 Supabase Auth。模型 API 密钥只通过 `ANP_MODEL_API_KEY` 环境变量提供。

## 登录与权限

- 使用 Supabase 邮箱账号登录或注册。是否确认邮箱由 Supabase 服务端配置决定。
- 用户身份通过 Supabase `/auth/v1/user` 验证；会话保存在 HttpOnly Cookie，前端不保存访问令牌。
- 普通用户可运行两个 Demo，只能访问自己的线程和命名空间。
- 平台管理入口仅对 `app_metadata.anp_role=admin` 的管理员开放。用户自行填写的 `user_metadata` 不授予权限。
- 全新原生实例自动创建 `admin@anp.local`，密码保存在该实例的 `var/secrets/runtime.json` 的 `business-user-password` 字段。不会覆盖已有同邮箱账号。

## 两个演示

- Workflow：规范化文本、生成摘要、输出结果，展示三个 LangGraph 节点。
- Deep Agent：通过 Deep Agents 调用 `read_material` 工具，生成架构研究报告。

默认使用明确标注的离线演示模型，可在没有外网和 API 密钥时运行。配置 `model.mode=openai`、模型名、兼容 OpenAI 的 `base_url` 和环境变量密钥后才会调用真实供应商；离线通过不代表供应商调用已验收。

## 平台入口

`/dbx/`、`/langfuse/` 提供 HTTP、流式响应和 WebSocket 代理；`/litellm/` 下的具体 API 路径同样可以代理。平台自身的登录机制仍生效；业务会话不会转发给平台。独立 Business 默认未启用 ingress，此时 `/supabase` 与 `/litellm` 管理入口跳转到配置地址。原生包启用单入口后，Studio 通过同源 `/project/default` 及根路径资源代理，LiteLLM 使用 `/litellm/` 前缀；管理页面与资源均要求管理员身份。

数据 API 和文件操作使用 Supabase Data API/RPC、Storage；业务代码不直接连接 PostgreSQL 或对象存储。Realtime 未包含在本轮原生发行范围内。

## 运行数据与验证

独立启动默认写入本目录 `var/`；可用 `ANP_BUSINESS_DATA_ROOT` 指向独立数据目录。原生发行包统一写入包根目录 `var/data/business/`。源码与冻结依赖保持不变。

```sh
make test
```

原生包构建还会使用随包 Python 安装冻结依赖并构建前端。`native/checks/business_check.py` 验证真实 Supabase 登录、管理员边界、跨用户线程隔离和两个 Demo，生成不含密码与令牌的回执。当前示例使用框架的本地 in-memory Agent Server，适用于本机开发和演示；生产部署需单独选择框架支持的运行与持久化方案。

## 可选 OTel/LGTM

配置 `observability.enabled`、`observability.endpoint`、`observability.service_name` 可启用 OTLP HTTP 导出；默认关闭。Windows 原生开发可将 endpoint 指向 WSL 内的 Collector（默认 http://127.0.0.1:4318）。请求、Workflow 节点、Deep Agent 工具与项目 HTTP 调用会产生 span，并导出请求指标和固定格式日志，不采集认证头或正文。管理员导航新增 /grafana/，通过 `platforms.grafana` 或 ANP_PLATFORM_GRAFANA_URL 配置上游；保留 Grafana 独立登录。详见 [OTel/LGTM 使用说明](../docs/development/OTel-LGTM使用说明.md)。本轮源码接入尚未执行构建和运行验收。

## 平台控制台与可选 OAuth

管理员可在 `/workbench/console/` 查看平台、配置代理及登录策略。连接原生包时可管理当前目录服务；独立复制 Business 不会尝试操作其他宿主进程。OAuth 默认关闭，通过 `ANP_BUSINESS_OAUTH_FILE` 指定配置文件或在管理员控制台填写，原生默认 `config/oauth.json`，独立默认 `var/oauth.json`。Google 和标准 OIDC 均使用 Discovery；需要将真实回调地址登记到提供方。独立 Business 还须配置服务端 `service_key_file`，不能把 Supabase service-role key 放到浏览器。

详细字段、权限映射、平台代理限制及手动登录策略见[平台控制台与 OAuth](../docs/development/平台控制台与OAuth.md)。


### 可选的原生单入口代理

`config.example.json` 的 `ingress.enabled` 默认 false，复制 Business 独立开发时可继续使用外部 Supabase 入口。原生包由启动器生成 true，并将 Auth、Data、Storage、Studio 的内部上游地址逐一传入；不要将它们指回 Agent Server 自身，以免递归代理。启用后 `/auth/v1/`、`/rest/v1/`、`/storage/v1/` 在 Agent Server 内流式转发；Studio 的 `/project/`、`/_next/`、`/api/platform/` 由同一管理员鉴权保护。LiteLLM 上游配置须包含其实际根前缀（原生包为 `http://127.0.0.1:54000/litellm/`）。无需额外业务 HTTP 服务或 Caddy。
