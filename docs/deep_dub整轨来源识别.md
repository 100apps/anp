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

## 官方参考比对后续阶段（2026-10-19）

已实际获取 [耶鲁 HIST 119 第1讲官方页](https://oyc.yale.edu/history/hist-119/lecture-1)，其 iframe 嵌入的视频 ID 为 `QXXp1bHd6gI`，与本次来源登记的 YouTube ID 一致。页面有 6 个章节、解析得到 38 个正文段落。这证明官方页面与登记视频 ID 的关系，**没有证明旧本机视频字节与线上完整媒体一致**，也没有重新下载视频。

### 实现与使用

`nodes/source_reference.py` 限量解析官方 HTML，只读取逐字稿正文容器，忽略脚本/样式/导航/页脚，核对嵌入视频和递增的章节时间。章节原报告时间只保留为参考，不写入字幕落点。逐词比对保留大小写/标点前的文本，比较时归一化大小写和弯引号，输出增删替换及附近 ASR 块索引；不修改任一正文，不判定参考方必然正确。

`services/source_reference.py` 使用当前用户身份、工作空间和来源，CPU 阶段租约覆盖获取、解析、比较及私有对象保存。HTTP 仅允许固定耶鲁官方主机及课程路径，无调用者 Cookie/授权头，拒绝重定向、端口、查询、用户信息与超两 MiB 响应；复用 SDK 的经校验证书上下文。原始 HTML 与 JSON 证据均进入 RustFS 私有对象；读取和评分重新核对原 HTML、源 ASR、模型证据、分区及阶段输入摘要。没有新增存储或数据库表。

```text
POST /api/apps/deep_dub/runs/{run_id}/compare-source-reference
GET  /api/apps/deep_dub/attempts/{attempt_id}/source-reference
GET  /api/apps/deep_dub/projects/{project_id}/sources/{source_id}/reference

uv run anp deep_dub runs compare-source-reference RUN_ID SOURCE_ID https://oyc.yale.edu/history/hist-119/lecture-1
uv run anp --transport http deep_dub runs compare-source-reference RUN_ID SOURCE_ID https://oyc.yale.edu/history/hist-119/lecture-1
uv run anp deep_dub source-reference ATTEMPT_ID
```

生产运行必须绑定相同来源。为避免恢复或重写已取消的历史运行，未绑定来源的 **acceptance + approved_versions** 运行可显式引用同项目历史来源做诊断；证据标明 `explicit-historical-source-diagnostic`，不绑定或替换运行来源，不把这种历史资料诊断当作正式新生产。其他未绑定或跨项目引用仍拒绝。

Web 在运行详情提供 shadcn/ui 来源版本选择、官方 URL、差异摘要、参考/ASR 两列和分批展开。输入相同则重放成功阶段。附近块从 1 开始编号，仅帮助回到原音，不是词时间或字幕时间。取消后按钮禁用且证据保留。普通生产模式与显式历史诊断的权限和约束均由服务端执行。

### 已运行验证

| 范围 | 实际结果 |
| --- | --- |
| 单元及真实 API/两种 CLI | 22 passed，10.19 秒。覆盖错误来源 ID、重复正文、损坏/截断/超限 HTML、章节时间、数字/否定差异及固定主机限制；真实官方获取与私有对象保存、只读/匿名/跨空间权限、POST/读取重放、租约释放及四项分数 |
| 实际文本比对 | 官方参考 5669 词，空提示 ASR 5688 词；152 处差异，其中 20 处涉及数字。参考方也含讲者标识和编辑性说明，这些数量不是错误率或漏译率 |
| 专名线索 | 官方稿的 Shaara、Doctorow 与 ASR 对应词有差异，已定位到第 7–8 音轨块；只记待复核，不自动纠正原文或写通过 |
| Web | 使用正式同源 `8089/workbench` 生产入口登录，选择来源、展开差异、从 10 项增加至 20 项、点击同输入比对；后台仍仅 1 个尝试，未新取模型结果。取消诊断运行后执行禁用、全部证据可读 |
| Langfuse | 逐项读回 differences=152、numeric_candidates=20；semantics、subtitle_timing 均为 not_measured；userId/sessionId 与原执行者/run 一致 |
| 冻结制品及 Compose | 制品 `20261018172206-5ee42a2d` 完成类型/生产构建、重新 Compose 启动；十一组件 ready、平台容器健康，实际运行镜像 ID 与 manifest 一致，四个业务 Python 源文件逐字节相同；UI 由该制品静态包提供 |
| 制品联合回归及保护 | 22 passed，38.21 秒；原430条线程最终值完整保留，结束432条；27个受保护容器 ID 不变，无本机原生模型进程被启动或停止 |

首轮参考诊断因新运行尚未绑定来源被拒绝，随后将历史诊断限定为上述显式契约；另一次因 CPU 回调误用了 GPU 三参数签名而返回 500，修复后重新执行。原失败/取消运行仍保留，未将其改写成通过。前端独立 8767 预览曾在重新登录时因跨源认证连接失败，未降低 CORS/认证限制；本轮改用正式同源入口验证通过，不将独立预览登录写成通过。

本次没有创建 source 审核、原文分段、译稿、字幕、时间线或成片。差异逐项独立复核、源字词时序、语义分段、正式新下载与媒体身份、完整生产图/批量译配/质量/整片 RTF≤0.5、真实 B站回执仍待完成。新本机制品也尚未进行本轮 GHCR 发布和拉取。

公开证据：[参考比对摘要](evidence/deep-dub-source-reference-20261019.json)、[正式工作台截图](evidence/deep-dub-source-reference-browser-20261019.png)。官方全文和所有差异留在私有对象及本机 `.local/`，不提交到仓库。

## 完整来源词时间后续阶段（2026-10-19）

已接通 `nodes/source_alignment.py`、`services/source_alignment.py`、API、默认本地/HTTP CLI 和 shadcn/ui 来源面板。该阶段读取来源识别的原完整 PCM 和原始 ASR 候选，在同一 GPU 租约中复用一次 Qwen3 ForcedAligner 0.6B F16 受控进程，逐块实际核对监听 PID、模型身份/已加载状态及输入样本摘要；结果和证据保存到 RustFS 私有对象。业务仍在 `apps/deep_dub`，不进入平台镜像，没有新增存储或数据库表。

```text
POST /api/apps/deep_dub/runs/{run_id}/align-source
GET  /api/apps/deep_dub/projects/{project_id}/sources/{source_id}/word-timing

uv run anp deep_dub runs align-source RUN_ID SOURCE_ID
uv run anp --transport http deep_dub runs align-source RUN_ID SOURCE_ID
uv run anp deep_dub projects source-word-timing PROJECT_ID SOURCE_ID
```

每块给定文本不超过2000字，音频不超过120秒；本次44块最长978字，未截断文本。空ASR块明确标记并跳过对齐请求，不伪造词边界。模型保持 `clamp_timestamps_to_audio=false`，时间转换仅加原音轨分块的整数样本起点，不钳制、回填或改写原始输出。最终模型进程实际停止后才提交、释放租约；各请求的临时HTTP客户端相互独立。与参考比对相同，未绑定来源的历史资料诊断仅在显式 acceptance + approved_versions 下允许，不绑定运行来源，不当作正式生产。

### 已运行验证

| 范围 | 实际结果 |
| --- | --- |
| 工具与回归 | 24 passed，81.58秒；包含成功、空ASR块、原始坏边界保留、第二请求取消/失败、缓存卸载后拒绝，以及原ASR/词对齐工具回归。新进程夹具直接使用真实解释器并保留Windows监听PID校验；其他平台对该Windows专用夹具明确跳过，不声称运行器已支持Linux |
| 真实完整音轨 | 1 passed，204.93秒；既有完整PCM的新词时间测量，未新下载视频、未重跑来源ASR，不算整片译配生产或整片RTF |
| 实际结果 | 44块、5654个模型词边界、44次请求、43次已加载核对、1次模型会话。0个空ASR块、0个零时长词；**1个时间异常块、17个严格字符覆盖差异块、跨块2560样本重叠（160ms）**均保留，不写通过 |
| 缺陷定位 | 第18块（从1开始）末词 `history` 的局部时间为960000–962560样本，超过该块末端160ms；17处覆盖不一致均有标点差异，包括连字符/破折号和数字间分隔符。不能把它们直接称为17块正文漏读，也不能删除数字标点或放宽规则把它们自动改为通过 |
| 权限与契约 | 真实API执行/只读查询，匿名及跨工作空间拒绝；默认本地CLI在不可达业务HTTP地址下重放，同HTTP CLI结果一致；7项分数幂等、实际退出后租约释放，无原文分段/审核/选版/时间线写入 |
| Web | 正式8089/workbench入口选择来源、确认同输入测量、成功回执重放；后台仍为原1次尝试，没有重启模型；词表从30增加至60行；取消运行后禁用执行且证据保留 |
| Langfuse | 7项分数及userId/sessionId均真实读回；词数、异常块、严格覆盖差异、零时长词和跨块重叠为测量值；语义、字幕时序保持not_measured |
| 制品与Compose | TypeScript/生产构建通过，冻结制品 `20261018174137-99781ae0` 经Compose重建并实启十一组件；镜像ID、健康、冻结业务Python文件摘要实际核对，UI来自该静态包 |
| 联合回归和保护 | 新冻结制品23 passed，40.62秒；原432条线程最终值和27个受保护容器ID全部保留，结束434条；本次自身89个子进程记录全部有退出码，未停止其他模型/作业 |

模型进程实际退出码1与原受控终止方式一致，如实保存；所有HTTP对齐请求成功才提交该技术测量。测量自身188.375秒，模型会话188.328秒，请求耗时合计13.422秒，请求前核对合计165.221秒。后者包含监听归属与模型状态等开销，表明后续应拆分测量并优化前置核对；未单独计时前，不把全部开销归因于某个系统调用，更不能以13.422秒当完整阶段或整片吞吐。

工具验收曾先因Windows虚拟环境启动器与实际监听PID不同而被正确拒绝；改为真实解释器后，取消夹具的7.5秒等待不足以覆盖真实PID核对，改为最多45秒且仍以第二个请求实际出现为条件。两次原失败日志保留，未降低产品归属检查，也未在第二请求未开始时声称取消路径通过。

原始词时间只以来源音轨PCM样本0为原点，音视频流的原报告start_time/time_base保留在证据中；尚未用独立实际解码证明字幕/视频时钟映射。本次仍未创建语义段、字幕、翻译或成片。下一步需要区分纯词法标点与数字/意义差异，修复真实160ms边界重叠，再在可接受的词时间上实施语义分段；同时优化已测到的前置核对开销。来源身份/新下载、独立内容与听感、完整生产图及两机批量生产、整片RTF≤0.5、B站回执及新GHCR发布/拉取仍未完成。

公开证据：[整轨词时间回执摘要](evidence/deep-dub-source-word-20261019.json)、[正式工作台词时间截图](evidence/deep-dub-source-word-browser-20261019.png)。全文、所有词及模型完整响应继续留在私有对象与`.local/`。
