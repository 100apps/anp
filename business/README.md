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

`/dbx/`、`/langfuse/` 提供 HTTP、流式响应和 WebSocket 代理；`/litellm/` 下的具体 API 路径同样可以代理。平台自身的登录机制仍生效；业务会话不会转发给平台。`/supabase` 与 `/litellm` 的管理入口跳转到配置的独立地址，因为官方管理台的根路径资源不能仅通过代理安全改成子路径。远程部署需要配置浏览器能够访问的管理台地址，并配置各平台自身的访问控制。

数据 API 和文件操作使用 Supabase Data API/RPC、Storage；业务代码不直接连接 PostgreSQL 或对象存储。Realtime 未包含在本轮原生发行范围内。

## 运行数据与验证

独立启动默认写入本目录 `var/`；可用 `ANP_BUSINESS_DATA_ROOT` 指向独立数据目录。原生发行包统一写入包根目录 `var/data/business/`。源码与冻结依赖保持不变。

```sh
make test
```

原生包构建还会使用随包 Python 安装冻结依赖并构建前端。`native/business_check.py` 验证真实 Supabase 登录、管理员边界、跨用户线程隔离和两个 Demo，生成不含密码与令牌的回执。当前示例使用框架的本地 in-memory Agent Server，适用于本机开发和演示；生产部署需单独选择框架支持的运行与持久化方案。
