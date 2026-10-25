# 平台控制台与 OAuth

工作台侧栏的「平台与存储控制台」位于 `/workbench/console/`，仅向 Supabase `app_metadata.anp_role=admin` 的管理员开放。普通业务用户不能访问管理 API、平台代理或平台凭据。

## 服务管理

控制台展示当前原生包的存储、平台及可观测性服务，区分运行正常、未就绪、停止、端口冲突、外部存储和未安装组件。启停为后台任务，页面显示结果；依赖仍在运行时拒绝停止共享组件。HTTP 网关可以继续运行并返回上游不可用，不视为阻止平台停止的业务依赖。

管理桥只认领本解压目录的 PID 身份与进程组。端口上的其他进程不能通过控制台接管或终止。PostgreSQL、RustFS、Auth、Data API、Storage、Agent Server 和网关是控制台依赖，需在终端执行 `./anp stop` / `./anp start` 维护，避免管理入口中途失效。

独立复制 `business/` 时可以配置远程上游与 OAuth，但不具备宿主原生包的服务启停能力。不要手工为不相关目录设置 `ANP_NATIVE_ROOT`。

## 在线配置与映射

- 「运行配置」只返回端口、固定回环监听与观测参数，不返回数据库密码、JWT 或云资源连接串。
- 配置使用版本摘要避免覆盖已变化的配置；写入为 0600 原子替换。
- 正在运行的服务不能修改端口。先停止相应服务，再修改端口并启动；修改服务端口后也要同步「端口映射」中的上游地址。
- `config/business-overrides.json` 保存原生包的代理配置，不会被启动器生成配置时覆盖；独立 Business 默认保存为 `business/var/console-platforms.json`，可通过 `ANP_BUSINESS_OVERRIDES` 指定。
- `/dbx/`、`/langfuse/`、`/grafana/` 等入口固定，HTTP/WebSocket 请求始终验证管理员身份；不提供数据库 TCP 端口穿透。
- 所有底层服务默认绑定 `127.0.0.1`。如需局域网访问，在「端口映射」中将 Agent Server 入口改为 `0.0.0.0`，重启后使用服务器地址加 Agent Server 端口访问。底层服务和本机网关继续保持回环监听。正式部署请给入口配置 HTTPS。
- 原生包由 Agent Server 统一代理：Studio 的 /project、/_next、/api/platform 与 LiteLLM /litellm/ 都受管理员鉴权保护；不再跳转到内部端口。独立 Business 未启用 ingress 时保留原有外部地址模式。

## 平台是否需要再次登录

| 平台 | 原生包策略 | 使用方式 |
| --- | --- | --- |
| DBX | 设置官方 `DBX_DISABLE_PASSWORD=true`，关闭独立密码 | 通过已鉴权的 `/dbx/` 进入，不再登录 |
| Grafana | 官方 Auth Proxy；匿名访问仍关闭 | Agent Server 移除客户端伪造的 `X-ANP-User`，验证管理员后注入 `admin`；不签发可绕过 Agent Server 的登录 Cookie |
| Supabase Studio | 无独立登录；保持仅本机入口 | 管理入口已鉴权；原生包同源 /project/default 打开；Data API/JWT/RLS 不关闭 |
| RustFS | 保留 S3 Access Key / Secret Key 和签名认证 | 从存储能力卡片「打开后台」进入 `/rustfs/console/`；在「登录与凭据」查看密钥 |
| Langfuse | 保留平台用户会话 | 在「登录与凭据」查看初始化用户名密码，手动输入 |
| LiteLLM | 保留管理员 UI 登录及 API master key | 在「登录与凭据」查看用户名密码，手动输入 |
| PostgreSQL、S3、平台内部 API | 保留必要的密码、签名密钥和服务凭据 | 不向普通用户或配置列表返回 |

凭据只在管理员主动点击后经不缓存的接口返回，切换页面、隐藏标签页或 60 秒后收起。不会提供本包凭据给已经配置成其他地址的外部平台。平台内手动改过密码后，初始化文件中的值不保证仍有效。Langfuse `/langfuse/api/public/*` 的 SDK 请求使用项目 Basic/Bearer 密钥，由 Langfuse 校验；它不是管理 UI 的免登录入口，也不携带 ANP 控制台 Cookie。

依据：[Grafana Auth Proxy](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/auth-proxy/)、[Langfuse 认证配置](https://langfuse.com/self-hosting/security/authentication-and-sso)、[LiteLLM 管理台认证](https://docs.litellm.ai/docs/proxy/ui)，以及本仓库锁定 DBX 源码的 `DBX_DISABLE_PASSWORD` 分支。Langfuse 的 `AUTH_DISABLE_USERNAME_PASSWORD` 是禁用密码登录方式，不是免登录开关。

## 可选 OAuth / OpenID Connect

发行默认不填写 issuer、client_id 和回调地址；不绑定公司站点，也不显示 OAuth 登录按钮。控制台的「OAuth 登录」可在线编辑，或复制包内 `defaults/oauth.example.json` 到 `config/oauth.json`。独立 Business 默认使用 `business/var/oauth.json`；可通过 `ANP_BUSINESS_OAUTH_FILE` 指定文件。

```json
{
  "issuer": "",
  "client_id": "",
  "label": "OAuth 登录",
  "redirect_uri": "",
  "scopes": "openid profile email",
  "token_endpoint_auth_method": "",
  "client_secret_file": "",
  "service_key_file": "",
  "admin_subjects": []
}
```

- issuer 填写标准 OIDC 提供方地址，例如 Google 的 `https://accounts.google.com`；程序通过 Discovery 读取端点和 JWKS。
- redirect_uri 必须是用户实际访问的完整 `/api/auth/oauth/callback` 地址，且在提供方登记。正式地址必须 HTTPS；本机 `127.0.0.1` / `localhost` 可使用 HTTP。
- 公共客户端启用 PKCE S256，不填 client_secret_file。机密客户端填写存有 secret 的服务端 0600 文件路径，默认使用 HTTP Basic 客户端认证，可通过 token_endpoint_auth_method 选择 `client_secret_post`（例如按 Google 应用配置）或 `client_secret_basic`；不要把 secret 填入 JSON 或前端。
- 原生包内部使用自身 Supabase 服务密钥完成会话交换；独立复制 Business 时，service_key_file 指向只含 Supabase service-role key 的服务端私有文件。不能把它交给浏览器。
- `admin_subjects` 明确列举此 issuer 下允许管理平台的 OIDC `sub`；默认空、禁止通配符。其他 OAuth 用户仅有普通业务权限。

流程由 Authlib 完成授权码、PKCE、state、nonce 和 ID Token 校验，再以 issuer+sub 创建隔离的 Supabase 用户并签发业务会话。不会根据相同邮箱自动关联现有管理员；UI 显示提供方返回的名称或邮箱，Supabase 内部使用稳定的专用身份邮箱。首次用户生成链接可能是 `signup` 验证类型，后续是 `magiclink`，按 Supabase 实际响应处理。退出撤销 ANP 会话，不等于退出提供方账户。

联调示例（不作为发行默认值）：

```json
{
  "issuer": "https://pre-cestlavie.alibaba-inc.com",
  "client_id": "local-spa",
  "label": "使用 BUC 登录",
  "redirect_uri": "http://127.0.0.1:58000/api/auth/oauth/callback",
  "scopes": "openid profile",
  "token_endpoint_auth_method": "",
  "client_secret_file": "",
  "service_key_file": "",
  "admin_subjects": []
}
```

该站点声明提供 `local-spa` 公共联调客户端；正式接入请注册独立客户端和明确的回调白名单。本机 Chrome 已完成真实公司 OAuth 回调与 Supabase 会话验证；内嵌浏览器遇到公司可信设备认证限制，不能绕过认证。Google 的真实账号与回调尚未测试。


## RustFS 后台代理

原生包启用官方二进制自带的 Console：S3 API 仍监听 `127.0.0.1:59002`，后台额外监听 `127.0.0.1:59003`。统一入口为 `/rustfs/console/`，不把内部端口直接暴露到局域网。旧配置自动补充 `rustfs-console` 端口，端口冲突时仍拒绝接管其他进程。

后台和 `/rustfs/admin/*` 都经过 Agent Server 管理员鉴权。官方前端的 S3 请求使用根路径；网关和 Agent Server 识别 AWS SigV4 或预签名请求，在验证管理员之后保留原始路径、Host 和查询串，转发给 `rustfs_api` 上游，由 RustFS 校验 S3 签名。该入口保留必要的 S3 权限控制，不把平台登录 Cookie 当作对象存储密钥。

代理配置分别为 `platforms.rustfs`（后台）和 `platforms.rustfs_api`（S3 根端点）。RustFS 卡片新增「打开后台」，初始化密钥在「登录与凭据 → RustFS」查看；密码文件中的字段分别为 `s3-access`、`s3`。不要在聊天中粘贴密钥。
