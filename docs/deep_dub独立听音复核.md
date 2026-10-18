# deep_dub 独立听音复核

> 后续：本文保留 Voxtral 三次校准拒绝的历史事实。7B 第二套模型已使用 v5 十份校准处理完整来源，当前协议为 11 个产物和 10 项评分；长音轨意见发现矛盾，正文依然未测。详见 [第二套听音模型](deep_dub第二套听音模型.md)，不能将本文的四份校准成功路径设计当作后来实际结果。

状态：门禁与失败清理已实际验证；本机 Voxtral 3B 配置未通过盲校准，**未提交来源正文，完整听音复核未通过**。完整整片生产、质量、RTF≤0.5 与 B 站回执目标保持进行中。

## 目的与边界

现有 Qwen、Nemotron 和官方稿之间的差异只是待裁决线索。新增节点直接接收完整来源 PCM 的连续窗口与原 ASR 候选，不提供第二模型或官方稿作为答案，不自动改稿。四份盲校准先验证模型是否真的区分原音中的数字与否定；校准不满足时禁止进入正文。

`auto` 的实际音频能力探测使用两秒无语音合成单音，HTTP 200 但答复为「无法处理音频」。没有把这一响应当成可听音，也没有更改现有 auto 路由。来源原音不发送给该外部模型。

## 模型与执行器

- 模型来自 [官方 GGUF 仓库](https://huggingface.co/ggml-org/Voxtral-Mini-3B-2507-GGUF)，固定提交 `20616573013f28229fff61de74359d3eeff61f6a`。
- Q4_K_M 解码器：2,473,001,920 字节，SHA-256 `4705be8ec22ca23d12632f4b4a3691faa95917d90a06d3cf3c3ec0e91958f1a8`。
- Q8_0 音频编码器：715,714,080 字节，SHA-256 `4f24c4ef3ce929d02ed9d1cfb050ae9a7365f057c0ddec0d489580982ebe0d02`。
- 音频编码器下载在 629,145,600 字节处中断；保留首败，校验 HTTP 206 与 Content-Range 后续传，最终完整文件摘要核对通过。
- 使用本机已有 `F:/llama.cpp/llama-server.exe`，固定执行器及 33 份 DLL 摘要；执行器 SHA-256 `783370f14f05ca7748ba49180f2ea555c6d43dd5d82a1402df9a20d7cade0ff2`，自报 `b11256-c85b92c69`，对应公开提交 `c85b92c69c955961621193cd51da194f3cbcedf3`。这是已有二进制的摘要与自报版本核验，**不是本轮从该源码重建或官方发布包认证**。
- [模型卡](https://huggingface.co/mistralai/Voxtral-Mini-3B-2507) 的支持语言不包含中文。当前节点仅接受英语来源，不宣称可审核中文配音。

运行器复用现有物理 GPU 协调锁与设备租约，仅停止自己启动的原生进程；不停止其他作业。限制到指定 GPU UUID、回环监听、单槽、8192 上下文。临时访问凭据通过临时文件传入，不放在命令行、网页或证据响应中；每次请求检查监听 PID。健康后核对真实模型路径与音频能力，退出确认后才能释放设备。

## 业务契约

- `nodes/auditory_review.py`：四份校准、直接听音、原样判定与完整证据验证。
- `services/audio_judge_runtime.py`：原生执行器归属、摘要、临时凭据与生命周期。
- `services/auditory_review.py`：当前用户权限、冻结输入、受控阶段、私有对象、评分与失败审核。
- 迁移 `020_source_content_review.sql`：仅开放当前待审来源的 GPU 证据阶段；不能用于登记生产来源、分段或自动恢复原图。
- Web 卡片使用 shadcn/ui，可按模型判定筛选、分页并互斥试听来源或校准原音。「未报差异」不显示为「通过」。

默认本地 CLI 与 HTTP CLI 使用相同服务及权限：

```powershell
uv run anp deep_dub runs review-source-audio RUN_ID SOURCE_ID
uv run anp deep_dub projects source-auditory-review PROJECT_ID SOURCE_ID
uv run anp --transport http deep_dub runs review-source-audio RUN_ID SOURCE_ID
```

接口为 `POST /api/apps/deep_dub/runs/{run}/review-source-audio` 与 `GET /api/apps/deep_dub/projects/{project}/sources/{source}/auditory-review`。

## 校准、保存与质量语义

Windows 已安装的英语语音生成四份 16kHz 单声道 PCM：12 与 19 样本、不要移除与移除样本。每对使用完全相同的候选，原音不同，期待模型给出相反判定。校准随机顺序与窗口 ID；期待答案不进入请求。匹配必须无问题，差异必须有具体问题，数字与否定分别要求对应分类；不确定不会算校准成功。

模型响应由 JSON Schema 约束必填字段，随后仍执行严格 Python 校验。该约束只保证结构，不替模型选择匹配或差异。来源窗口绑定原 PCM 样本、候选文本和模型摘要；正式转录、人物身份、字幕时钟保持 `not_measured`。成功证据包含 JSON 和四份可查听校准原音，产物归属与摘要逐份复核。模型报正文差异时，仅登记绑定证据的 `independent_model/failed` 来源审核，永不自动签发通过。

## 当前实际验证

- 12 项专项检查通过：反事实校准、结构/模型/窗口绑定、音频和候选篡改、完整覆盖、伪造质量状态、真实原生 HTTP 进程身份与取消退出、独立库迁移/权限边界。
- 首次真实请求在冻结制品 `20261018212518-dc5e4db3` 上加载模型，返回 JSON 缺少必填 `issues`，被拒绝为 `AUDIO_JUDGE_RESPONSE_INVALID`；没有进入正文。失败尝试 `8745661f-fce2-48b1-8b96-5c3ba8cc8970` 保留，原生进程退出，GPU 回到 idle。
- 加入上游支持的 [Schema 约束](https://github.com/ggml-org/llama.cpp/blob/c85b92c69c955961621193cd51da194f3cbcedf3/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions-api) 后，制品 `20261018213257-e8f45c48` 经 Compose 重建。第二次尝试 `cdcf6995-8c7e-42aa-98e5-833cbe3d9cd0` 实际识别出两组反事实差异，但用 `facts`、`meaning` 泛称，未达到 `numbers`、`negation` 分类要求；返回 `AUDIO_JUDGE_CALIBRATION_FAILED`，没有提交正文。模型退出与租约释放保留。
- 修订 v2 明确所有输入共用的分类优先规则，不给校准期待答案，也不放松原门禁；添加失败校准原音留存与进度。制品 `20261018213643-481c81fd` 已重新构建并经 Compose 启动。22 项独立库回归通过。
- 第三次真实尝试 `065422ba-2478-4b99-94d8-d151eed8b932` 仍校准失败：将一致的否定句误判为差异，把 12 误听为 11；实际否定差异仍用泛称 meaning。四份合成校准原音与原始判定均保留。本配置不能用作可靠正文裁决器；不继续重试直到碰巧通过，也不将它的猜测写为质量通过。
- 真实失败回执专项 1 项通过（10.83 秒）：三次失败均保留、三份 GPU 租约 released/stopped、无正式版本/审核/评分/正文对象；原运行和原 213 项队列摘要不变。本地 CLI 指向不可用业务 URL 仍直接读回失败，HTTP CLI 同回执，均未重启模型。未授权读取 401、跨空间 404、viewer 执行 403。Langfuse 三条观察的原任务用户与本运行 session 均真实读回；首轮观察读回在投递可见前失败，等待投递并按节点名过滤后恢复核验，没有重跑模型。
- 正式冻结 Web 实际显示「正式转录仍待审」，可打开确认说明后返回，没有触发第四次尝试。[页面证据](evidence/deep-dub-auditory-gate-20261019.jpg)。成功证据的筛选、分页、校准原音试听代码已实现，但本次没有成功证据，**这些成功分支未做浏览器验收**。

后续需要更可靠、实际通过独立盲校准的英语听音模型，或可追溯的人工原音复核。现有三次失败不是必要用户输入的阻断：可继续调查模型与准备图、修复正文/词时间，但不得通过放宽校准、复制候选或客户端 approve 解除门禁。

## 冻结部署与保护结果

制品 `20261018213643-481c81fd` 的业务运行器、节点、服务和迁移文件与冻结安装内容逐字节一致；平台镜像 `sha256:2582e1bd9a81afb38492ebdaca1e79401b47e83f48e152e44573b5c4b108d5d0` 经 Compose 启动，11 组件 ready。30 项冻结生产回归通过（128.65 秒），TypeScript 检查通过。该制品包含本工作区其他尚未提交的修改，仅为本机冻结验证，不冒充已发布的新 GHCR 镜像。

原 456 条线程最终值全部保留，结束 458 条；完整来源准备父/子中断检查点不变，未自动恢复。27 个受保护容器 ID 保持。迁移前五库备份及逐库隔离恢复通过，位置 `.local/backups/v3-postgres-before-cloud-20261018T212511Z`；不是跨四种存储的原子快照。

公开结构化证据见 [校准拒绝与部署证据](evidence/deep-dub-auditory-rejection-20261019.json)。三个真实完整听音验收请求均失败；通过的是失败记录、资源释放、权限与重放边界，不能将两者混为「听音功能全量验收通过」。六项成功后评分以及 44 窗口/五产物的成功回归仍须合格模型实测。

尚未完成可信准备子图放行、源稿裁决和正式分段、独立人物/字幕/成片质量、远端 V100 实际调度、完整整片 RTF 与 B 站发布。本轮也未重新验收 IDE F5、真实云库或发布新 GHCR 制品。
