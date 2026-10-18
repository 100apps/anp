# deep_dub 制作台与译稿版本编辑

## 已运行验证

2026-10-18，shadcn/ui 制作台新增正式 V3 数据适配器，与旧任务兼容入口共用三栏交互组件。正式入口不调用旧 8765 服务，也不读取宿主机任务 JSON。

- 项目卡的「打开制作台」进入 `apps/deep_dub?project=<项目 UUID>&studio=1`。左侧分段导航，中间同步画面与监听轨，右侧译稿、历史版本及诊断；手机切换分段、画面、当前段三个面板。
- `GET /api/apps/deep_dub/projects/{id}/studio` 使用普通用户身份，在单个数据库事务中读取相互一致的来源、封存分段、质量档位、时间线与版本快照。显式来源、分段、档位、时间线必须匹配，不将新来源与旧分段拼接。没有对象存储路径或服务密钥。
- `POST /api/apps/deep_dub/projects/{project}/segments/{segment}/translations` 创建人工译稿新版本。必须提供父版本、预期修订、项目版本与正文；支持 `Idempotency-Key`。并发冲突返回 409，同键同正文重试返回原回执，同键不同正文拒绝。
- 新稿独立登记作者、输入摘要与不可变编辑回执。旧译稿、审核摘要、音频绑定和封存选版保留；不会为新稿自动创建审核、声音或成片。迁移使用独立回执表，避免给原译稿加列而改变既有审核内容摘要。
- Web、默认本地 CLI、HTTP CLI 共用服务、原用户身份、工作空间 RLS 和权限。查看者可读取、试听和检查版本，文本框只读、保存禁用；即使直接调用写入 API 仍为 403。未登录 401，外空间拒绝。媒体每次请求另行校验身份和权限。
- 浏览器实际在明确的 acceptance 项目创建第 3 版译稿，刷新后保留；原第 1、2 版正文保持一致，项目版本变为 3，没有新增音频、审核或成片。64×64、两秒无声灰色视频实际解码，`readyState=4`、播放时钟前进。人工原文与译稿只用于契约验收，不代表翻译质量。
- 浏览器管理员与查看者登录、退出已实测。1600px 三栏与 390px 手机检查通过；手机页面宽度为 390px。只读提示可见，保存和未开放生产参数禁用。
- 编辑与快照共 13 项独立数据库检查、1 项真实 HTTP/两种 CLI 检查通过；16 项前端版本映射断言、TypeScript 类型检查与正式 Vite 构建通过。构建有约 505 kB 入口块警告，未声称完成首屏性能优化。
- 迁移前完整逻辑备份并用 `pg_restore --list` 检查；013/014 迁移分别比较原 28/29 表内容摘要不变。只操作 V3 `54323/postgres`，没有使用宿主 PostgreSQL或修改旧 anp2 卷。本轮前 329 条 Agent 线程状态全部保留，结束快照 337 条；27 个受保护旧/存储容器 ID 保持一致。新增线程包括明确的验收运行，不算生产。

证据：[真实详情契约](evidence/deep-dub-studio-contract-20261018.json)、[桌面截图](evidence/deep-dub-studio-desktop-20261018.png)、[手机截图](evidence/deep-dub-studio-mobile-20261018.png)。测试日志与迁移备份留在私有 `.local/`。首轮数据库测试各出现一次测试假设错误（审核列名与查看者读权限），修正后完整重跑，原失败日志保留。

## 本机独立验收入口

为避免重启其他工作正在使用的业务进程，实际浏览器验收使用 API 8183、Vite 8767。此入口是业务源码开发进程，不是平台容器临时补丁，也不代表已有 8181 进程自动加载了新增路由。

独立 API 终端：

```powershell
$env:ANP_SUPABASE_PUBLIC_URL='http://127.0.0.1:8767/supabase'
uv run --no-sync python -m uvicorn anp.server.api:app --host 127.0.0.1 --port 8183
```

独立前端终端：

```powershell
$env:ANP_API_TARGET='http://127.0.0.1:8183'
$env:ANP_SUPABASE_DEV_GATEWAY='http://127.0.0.1:8089'
pnpm --dir console exec vite --host 127.0.0.1 --port 8767
```

登录通过 Vite 同源 `/supabase` 代理访问已有平台认证服务。最初直接跨域登录遭预检拒绝，SDK 同凭据正常；修正后同源登录成功，没有放宽平台 CORS 白名单或关闭浏览器安全限制。公开配置的可选 `ANP_SUPABASE_PUBLIC_URL` 只影响浏览器地址，内部 SDK 仍使用原地址。前端连接失败与密码错误分别提示。

CLI：

```powershell
uv run anp deep_dub projects studio <项目UUID>
uv run anp --transport http --endpoint http://127.0.0.1:8183 deep_dub projects studio <项目UUID>
uv run anp deep_dub edit-translation <项目UUID> <段UUID> --input edit.json --request-key <UUID>
```

`edit.json` 包含 `base_translation_id`、`expected_revision`、`expected_project_version`、`chinese_text`。从最新快照读取预期版本，不能遇到冲突后静默覆盖别人编辑。本地 CLI 实测将业务 API 地址设为不可达仍可通过原身份执行；HTTP CLI 得到同一回执。

## 实现与验收边界

旧任务兼容模式已保留实际 106 段和波形等交互，见 [功能对齐](deep_dub工作台功能对齐.md)。这次正式适配器的正向写入仅覆盖人工译稿。正式波形计算、配音生成、声音选版、落点提交、音色变更、档位变更和质量执行仍待接入；相关生产按钮禁用并说明原因，不借旧兼容接口绕过权限，也不返回模拟成功。独立库中的旧音频、选版与审核绑定测试不等于真实 TTS 验收。

真实长片续播、离线授权导出、两 GPU/两 CPU 生产、全片质量与 RTF≤0.5、B 站发布回执仍未完成。此阶段不据此关闭整体目标。
