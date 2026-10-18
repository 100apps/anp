# deep_dub 整轨来源识别与受控模型复用

2026-10-19：已实现来源 ASR 业务服务、API、默认本地/HTTP CLI 和 Web 运行面板，并完成一次 2586 秒既有来源音轨的新识别验收。**这是完整音轨的识别阶段验收，不是整片译配生产或整片 RTF 通过。**

## 实现与职责

- `nodes/transcribe.py` 的 `AudioModelSession` 在同一次 GPU 阶段租约中复用一个受控原生进程。首次核对模型未加载；后续请求逐次核对同一模型仍处于已加载状态。每个异步 HTTP 客户端独立创建和关闭，仍只复用 SDK 的证书上下文。
- `nodes/source_asr.py` 将来源第一音轨实际解码为 16kHz、单声道、PCM16，按连续样本区间切块。块内 PCM 摘要与提交给模型的归一化 PCM 摘要一致；小于 100ms 的尾块并入前块，不丢弃尾样本。
- `services/source_recognition.py` 校验调用者、工作空间、项目、运行来源、不可变源文件和模型摘要，领取租约，保存 PCM 与唯一 JSON 证据，并登记五项分数。
- `api/router.py`、`cli.py` 和 `ui/source-recognition.tsx` 调用同一业务契约。没有新增数据库表或存储服务；完整 Agent 生产图尚未接入这个阶段，当前 HTTP 入口同步调用业务服务。

单块结果明确标记模型进程尚未退出；整批确认自身 Popen 进程退出后才生成退出回执，私有产物保存与阶段提交完成后释放租约。不能拿一个请求结束、HTTP 断开或租约过期替代进程停止证明。未知外部 GPU 进程仍拒绝执行，不停止别人的作业。

## 接口

```text
POST /api/apps/deep_dub/runs/{run_id}/recognize-source
GET  /api/apps/deep_dub/projects/{project_id}/sources/{source_id}/recognition

uv run anp deep_dub runs recognize-source RUN_ID SOURCE_ID --request-key KEY
uv run anp --transport http deep_dub runs recognize-source RUN_ID SOURCE_ID --request-key KEY
uv run anp deep_dub projects source-recognition PROJECT_ID SOURCE_ID
```

写入字段为 `source_id`、`language`（English/Chinese）、`chunk_seconds`（30–90，默认60）、`timeout_seconds`（60–7200，默认3600）。接收四小时、4GiB 以内的私有来源，分块输入不超过原单次识别限制。默认 CLI 不依赖业务 API/Worker；模型进程与平台身份、存储依赖仍须可用。

识别使用空文本提示，每请求固定上限 512 token。这个上限和样本连续覆盖均不能证明每个词都被准确识别。API 读取只返回当前来源最近一次成功证据，失败及取消尝试仍在运行记录中。

## 已运行验证

| 范围 | 结果 |
| --- | --- |
| 窗口精确覆盖、短尾块、同一真实 HTTP 子进程、多次请求、第二请求取消/失败、缓存被卸载时拒绝、原 ASR/TTS/对齐回归 | 42 passed，11.35 秒；其中 HTTP 夹具文本不是模型质量证据 |
| 整轨真实识别 | 1 passed，194.58 秒；来源是旧任务本机文件，未重新下载或核验网络来源与本机文件一致性 |
| 输入/输出 | 视频名义时长 2586.328 秒；实际 PCM 41,380,932 样本，即 2586.30825 秒；差 −19.75ms 如实记录，不补齐 |
| 受控复用 | 44 个连续分块、43 次已加载模型检查、一个受控模型进程，识别文本 31,566 字符，空文本块 0 |
| 租约和权限 | 真实退出后释放；只读成员可读但不可执行；匿名、外工作空间拒绝；两种 CLI 重放同一阶段回执 |
| Web | 确认入口、英文选择、相同输入复用、分块文本展开、取消运行后禁用执行且保留证据；Web 重放没有再启动模型 |
| 观测 | Langfuse 五个分数及原执行用户/session 已读回一致；没有生成分段、译文、字幕、时间线或质量审核 |
| 制品 | TypeScript/Vite 构建通过，含来源结果读取 CLI 的冻结生产制品 `20261018165820-48e8c079` 已正常 `make start`，十一平台组件 ready |
| 平台/业务回归与保护 | 最终冻结生产模式 21 passed，36.24 秒；切换前 426 条线程最终值未变，当前 430 条；27 个受保护容器 ID 未变 |
| 来源结果 CLI | 本地 CLI 配置不可达的业务 HTTP 地址仍能直接读取；HTTP CLI 读取相同证据，单独检查 1 passed、2.82 秒；亦纳入上述 21 项联合回归 |

服务器按现有受控进程方式主动终止，实际退出码 1 保留在回执中，没有改写成 0。成功判断来自所有识别请求成功、输入/输出核对和实际停止证明，不能只看一个退出码。本阶段还包含本机媒体 CPU 预处理；GPU 租约不代表整台 CPU 或显卡没有桌面图形进程共用。远端 V100/Xeon 未用于本轮阶段。

首次验收脚本的来源准备阶段误用了占位计时，发现后主动取消，保留该运行及 13 块进度，不作为通过证据；模型退出及租约释放已核对。修正为单调时钟后另起新运行完成上述验收。没有修改旧记录冒充首次成功。

## 分数和复核边界

- `source_asr_sample_coverage`：连续 PCM 样本均提交识别器，仅技术覆盖。
- `source_asr_empty_chunks`：实际识别为空的块数；为空不自动等于静音。
- `source_asr_model_sessions`：同阶段受控模型会话数。
- `source_asr_content_accuracy`、`source_asr_subtitle_timing`：保持未测，Langfuse 使用状态分数，不写成数值通过。

分块按样本连续切割，可能切在词或句中。音轨样本0不是自动成立的全片字幕原点；没有对专名、数字、否定和全文语义做独立核验，也没有声称来源识别文本是正确终稿。需要后续独立内容复核、词级对齐与语义分段后才能形成可审核字幕和译配输入。原片来源身份也需要在正式生产时重新获取和核实。

194.58 秒是这项验收总耗时，不能与完整视频时长相除后当作整片译配 RTF；尚未包括正式新下载、完整翻译、批量配音、背景、混音/字幕、最终解码和全部质量验收。整片 RTF≤0.5、两机资源调度、新平台 GHCR 发布与真实 B 站回执仍未完成。

## 证据

- [结构化回执及分块摘要](evidence/deep-dub-source-asr-20261019.json)：完整识别文本留在私有 RustFS 产物，本文件只保存计数、区间、摘要、模型和验收状态。
- [来源识别页面](evidence/deep-dub-source-asr-browser-20261019.png)
