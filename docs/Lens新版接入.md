# V3 独立 Lens 接入与真实诊断验收

本文件适用于 V3 的独立 Lens。旧 anp2 的 LiteLLM v1.105 内置 Lens 继续使用原有配置，见 `litellm-lens.md`；不能混用两个版本的 API 和 Key。

## 三种凭据

| 凭据 | 用途 | 不应使用的位置 |
| --- | --- | --- |
| Lens Admin Token | `/lens/ui/` 后台和管理 API | 业务追踪写入、模型调用 |
| Lens 追踪写入 Key | `/lens/v1/traces` OTLP 接收 | 模型调用、管理后台 |
| LiteLLM 分析虚拟 Key | 只允许 `auto`，供 Lens 分析器调用模型 | OTLP 接收、平台管理 |

凭据只保存在本机私有配置；`config/local-credentials.md` 给出本机入口和已创建的写入 Key。V3 管理界面与 API 统一通过平台 URL 访问。

## 写入追踪

最新版 LiteLLM 的 `/litellm/v1/traces` 返回 410，要求直接向 Lens 写入。模型虚拟 Key 或网关 Master Key 不能代替独立 Lens 的追踪 Key。

先在 Lens 后台创建追踪 Key，或使用管理 API：

```http
POST /lens/lens/tracing/keys
Authorization: Bearer <Lens Admin Token>
Content-Type: application/json

{"name":"我的业务追踪"}
```

返回的 `key` 仅保存到私有配置。追踪发送到：

```http
POST /lens/v1/traces
Authorization: Bearer <Lens 追踪写入 Key>
Content-Type: application/json
```

请求体采用 OTLP JSON 或 protobuf。保留 trace/span 父子关系、服务名、Agent 名、输入输出与真实错误；合成测试必须显式标注，不能冒充业务观测。

管理 API 也支持 Cookie 会话。使用 Cookie 进行写操作时需要与配置的平台 URL 匹配的 Origin；缺少 Origin 返回 403 是其保护机制，不通过关闭保护来适配网关。

## 手动分析与限额

分析模型是用户选择的 `auto`。分析 Key 仅允许该模型；默认 20 RPM / 100,000 TPM，可以通过私有部署配置调整：

```toml
[lens]
analysis_rpm_limit = 20
analysis_tpm_limit = 100000
```

旧的 20,000 TPM 在真实诊断中被较长系统提示和连续工具请求耗尽，产生本机网关 429。初始化器支持幂等更新现有受限 Key 的限额，仍不授予其他模型权限。

本机诊断配置“ANP V3 合成接入诊断（手动）”保持 `enabled=false`，不会周期扫描。`auto` 的网关记账价是 0，这不代表上游免费，也不能把 Lens 显示的内部预算视为上游实际消费上限。

新版默认扫描窗口会排除最近约两分钟的数据。刚写入追踪时，应等待窗口覆盖，或通过 `/lens/lens/<id>/runs` 提供明确的 `start` 和 `end`。完成但 `reviewed=0` 不能作为模型诊断通过的证据。

## 本机已验证

- 独立追踪 Key 的 OTLP 写入和管理员读取成功。
- 使用 `auto` 完成一条显式标记为合成测试的失败追踪分析。
- `reviewed=1`、`investigated=1`，产出一条结论、一条评估和一条 review。
- 结论识别出合成样本中的 `normalize` / `KeyError('task_id')`；这不是新发生的真实业务故障。
- 首次空窗口任务不计为通过；实际任务包含一次限流与退避，未把这些等待从耗时中扣除。

证据：`docs/evidence/v3-lens-investigation-20261018.json`。这些检查不代表整个平台或 deep_dub 已验收。
