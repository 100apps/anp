# deep_dub 译配业务

## 来源准备编排

`runs prepare-source <运行UUID> --request-key <稳定键>` 默认在本机直接执行相同业务节点，无需启动业务Web、Worker或Agent；HTTP模式调用同一服务。`runs launch-source-preparation` 是显式平台Agent编排入口，`runs source-preparation <运行UUID> <流程UUID>` 读取其持久化状态。可用 `--reference-url` 传入耶鲁官方课程页。

平台已真实串联新原片获取、完整解码、整轨识别、词时间和参考比对，随后停在独立复核；用户会话只存业务主机私有授权目录，图内只有ID和轻量回执。原生调用success不代表业务完成；线程中断和业务waiting_review分别保留。完整父/子检查点经Compose重建读回相同，客户端approve不能通过门禁。具体新执行、CLI重放、失败记录和未完成范围见 `docs/deep_dub来源准备编排.md`；尚不是完整译配生产或RTF通过。

## 新来源获取入口

来源获取已接入 `runs acquire-source <运行UUID>`，结果读取为 `runs source-acquisition <运行UUID>`；HTTP CLI 和 Web 调用相同服务。只接受 run 排队时冻结的 HTTPS YouTube 单视频地址，不替换已绑定的其他来源。`config/settings.local.toml` 的 `[deep_dub] source_fetch_config` 指向管理员本机配置，格式见 `source-fetch.local.example.json`；零摘要、示例用户路径和UA须先按本机实际配置替换，不能原样运行。下载器摘要变化时拒绝执行。

每次创建独立的下载临时目录，最高可用画质与音轨无损合并，按真实SHA上传Docker私有对象存储后清理宿主暂存；使用所选Firefox实时会话，不写入公开浏览器配置或业务证据。Windows Job Object约束本次下载器/Node/FFmpeg子孙，停止确认后释放租约；遇到站点限流/本人验证不自动轮换账号或网络。当前只实现Windows本机，其他站点/系统不能凭模板存在称为已支持。

这是完整生产的来源阶段，不能把获取与元数据核对当作内容、听感、字幕、整片RTF或发布通过。最新实际验证及限制见 `docs/deep_dub新来源获取.md`。

状态：**实现中。正式 shadcn/ui 详情、私有媒体/波形、人工译稿版本与真实网关候选生成已实测接入；配音/选版写入、独立质量审核与整片生产未完成。** 业务始终位于本目录，不打进平台镜像。

## 已运行验证

- `migrations/001_foundation.sql`：首批 7 表（项目、质量规则修订、私有产物、来源修订、运行、阶段和尝试），全部开启工作空间 RLS；外键校验空间/项目归属，运行只能绑定 deep_dub 平台任务。
- 项目创建只允许受限 RPC；幂等重放保留项目，内容冲突拒绝。版本行禁止覆盖，普通用户不能直接改表或回填运行排队起点。
- 独立 PostgreSQL 临时库由最新备份还原，迁移执行两遍；8 项真实数据库检查通过，含 12 次并发幂等请求。临时库清理，未改动原生产表。
- 首批迁移应用时，既有任务数保持 944；真实 Supabase 用户调用 `services/projects.py` 创建、重放与查询通过。此后新增验收任务计数保留在各轮证据中，不把 944 当作当前总数。
- 已创建 Yale 来源的项目配置，尚未创建整片生产 run。真实 `auto` 调用使用原片新截的 26 秒验收片段，不能当作完整来源下载或生产 RTF。项目配置时间不是生产排队时间，首次真实排队/生产起点将在 run 中记录，不能事后重置。

## 已实现但未完成集成

`api/router.py` 提供项目至成片登记、运行与阶段/评分和实际解码适配。早期 15 项真实集成覆盖 Supabase/RustFS/Langfuse、业务宿主 API/Worker、根 CLI、本机 CPU 运行器及平台 Agent 解码子图。后续已接正式三栏制作台、私有媒体与派生波形、人工版本保存和真实 `auto` 候选生成；分阶段证据见根目录 `docs/deep_dub制作台版本编辑.md`、`docs/deep_dub波形派生产物.md`、`docs/deep_dub真实译稿生成.md`。完整生产图、独立质量审核、配音/选版、整片 RTF 与发布回执仍待完成，不能以各轮夹具取代最终验收。

## 内容与版本关系（已部署、关系约束已验证）

`002_content_versions.sql` 与 `003_selection_contracts.sql` 新增 16 表，共 23 表全部启用 RLS。除原计划的候选与组合关系外，增加 `deep_dub_selection_audits` 显式关联表，避免将审核 UUID 数组当作无外键的长期关系。

- 同一工作空间内也不能跨项目、来源、分段或角色拼接译文、音频和参考。成片的 run、来源、时间线及质量规则使用复合外键绑定。
- 审核绑定具体候选指纹和质量规则修订；没有证据、未测状态、旧版本审核或较新的失败均不能满足必需门禁。译文不能用技术自评直接写通过。
- 选版使用项目版本号和幂等键；并发覆盖返回 409。审核集合、分段集和时间线封存后不能覆盖、重开或追加成员。
- 移动播放落点形成新快照，不修改原声情绪窗口。快照只证明输入已冻结，不能当作碰撞、自然度或整片质量已经通过；这些仍需真实媒体审核。
- 默认 high 保留背景与本集音色参考，不允许静默降档。生成侧倍率上限与成品 PCM 变速上限分别建模；模型能力与具体参数仍待真实实测，不能声称已复用 T2S 缓存。
- 25 项独立临时库检查通过：从当前 V3 主库的只读逻辑快照排除已有 deep_dub 对象，再从基础版本重建迁移，不依赖旧私有备份；临时库、快照及还原目录全部清理。另有 3 项真实 Supabase 的 ASGI 契约检查通过，没有生成或伪造真实媒体评分。

PostgREST 领域冲突使用 `PT409`，对象不存在使用 `PT404`，避免被数据库回滚/过程错误的默认 HTTP 500 语义混淆。依据：[官方错误映射与自定义状态码](https://docs.postgrest.org/en/v14/references/errors.html)；本机真实映射已验证。

## 执行、租约、观测与发布账本（已部署）

`004` 至 `007` 迁移补齐设备、租约、评分、事务 outbox、发布五表，当前共 28 表，全部启用工作空间 RLS。迁移前五库备份均完成独立实际恢复；应用迁移不重建平台镜像、不改变原有任务。

- 排队起点、首次启动与完成时间由数据库保存；默认本地运行，生产与验收目的分开。运行冻结质量规则、源输入与复用策略，不允许事后缩短墙钟。
- 设备初始未核验；过期租约隔离设备，不能把超时当作实际停止。CPU 容量与 GPU 独占通过数据库锁维护，迟到 worker 的 token/fence 不能提交成功结果。管理员恢复也需要实际停止及外部进程检查证据。
- 阶段回执与 outbox 同事务提交；评分绑定具体尝试指纹及规则修订，已测必须关联证据，未知数值为 NULL。投递失败保留重试与退避，不写原始异常/凭据到错误字段。
- `services/observations.py` 使用 OTLP 写入 Langfuse v4，user 来自原任务创建者、session 来自 run，事件与观察 ID 固定。旧批量 trace/span 入口在当前 v4 的 events_only 模式返回 400，已实际复现并适配，未切换平台到兼容旧数据模式。
- 真实 Langfuse v4 已读回正确 user/session；未测项表现为独立 TEXT `speaker_identity:status=not_measured`，本地数值仍为 NULL。Scores API v3 的 `value` 是多态字段，文本返回字符串，并非旧版 `stringValue`。
- 整片验收要求所有阶段完成、运算停止及质量审核通过；RTF 使用原始排队至技术验收结束。复用其他轮次音频（含 PCM 父链）不能冒充全新生产；允许复用的编辑轮次不计性能通过。
- 投稿账本要求完整生产轮次通过技术与性能门禁。同一成片只有一条投稿；上传超时进入回执未知，不自动重投。真实投稿回执与公开页面查证使用不同证据，分别登记，尚未发生实际投稿。

46 项独立临时数据库检查及 4 项真实 Supabase/本机 Langfuse 集成检查通过。设备、媒体审核和发布门禁在独立库使用合成夹具，不能当作真实设备空闲、媒体质量或 B 站发布通过。

协议依据：[Langfuse v4 迁移说明](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4)；实现同时对照仓库锁定的 Langfuse 源码与本机 SDK，最终以实际接口读回为准。

## 接续范围

来源至成片已有受限登记契约，本机 CPU 全流解码已接实际运行器；继续接下载/翻译/ASR/TTS/分离/渲染节点、设备与发布服务适配、工作台和完整业务 flow。音频/背景/成片正向登记仅在独立库使用设备/产物夹具验证，尚未完成真实模型生成或整片渲染。远端 worker 尚未有仅限单个尝试的授权签发，不能把完整用户 JWT 当作该功能已实现；丢失投稿凭据后的人工核对恢复也待接入。随后实际探测两 GPU/两 CPU，发起全新整片生产，验证完整墙钟 RTF≤0.5、内容/声音/字幕/时序/碰撞/解码，最后取得 B 站发布回执。未测项保留未测，不以局部测速或旧成片替代。

验收证据：`docs/evidence/deep-dub-foundation-20261018.json`、`docs/evidence/deep-dub-content-contracts-20261018.json`、`docs/evidence/deep-dub-execution-contracts-20261018.json`。旧证据保留当时结构、SHA 和测试范围，不改写为新一轮结果。

## 私有产物与业务 CLI（接入中）

`008_private_artifacts.sql` 在同一个 RustFS 服务上增加 `deep-dub` 私有逻辑桶，不增加实例。对象路径由空间、项目、尝试与 SHA256 组成；普通身份没有覆盖和删除权限。登记 RPC 校验当前尝试、原执行者、取消/设备租约状态及 Storage 记录字节数，支持并发幂等。Python 服务流式上传并完整回读核对摘要，后续读取同样验证摘要；数据库本身不能读取 RustFS 字节，不把客户端提交的摘要当作数据库已独立验真。

首次上传成功而登记失败时保留不可覆盖对象，重试会回读验真后重新登记；尚未提供孤立对象清理，不自动删除原数据。上传文件在本地散列、传输及回读所花时间都属于生产墙钟，不能从 RTF 中扣除。实际大视频上传上限及整片吞吐仍待媒体生产验证。

开发阶段可直接使用业务 CLI，不需要启动业务 Web/API；身份复用平台登录：

```powershell
uv run anp auth login
uv run python -m apps.deep_dub.cli projects list
uv run python -m apps.deep_dub.cli runs list <项目UUID>
uv run python -m apps.deep_dub.cli projects create "新译配项目" "https://example.com/video"
```

HTTP 开发入口与 CLI 使用同一组服务、Supabase 身份和工作空间：

```powershell
uv run uvicorn apps.deep_dub.api.application:app --host 127.0.0.1 --port 8283 --reload
uv run python -m apps.deep_dub.cli --transport http --endpoint http://127.0.0.1:8283 projects list
```

`projects quality-profile --input 文件.json` 创建不可变规则修订；`runs queue <项目UUID> --input 文件.json --request-key <幂等键>` 排队，输入包含 `quality_profile_id`、`flow_revision`、`expected_version`，默认 `mode=local,purpose=production,reuse_policy=none`。验收应显式使用 `purpose=acceptance`。`runs start/cancel` 当前管理运行状态，尚未驱动完整生产图；不要将状态切换当作媒体生产完成。

`upload-artifact <尝试UUID> <本地文件> --media-type application/json` 提交当前执行者的私有产物；HTTP 模式流式发送文件内容，不向服务器传宿主文件路径。`flush-observation` 每次投递一个 outbox 事件。工作台菜单、统一 `anp deep_dub` 注册及完整生产图仍待接入，不将上述独立开发入口称为整个平台业务验收完成。

## 来源、原文分段与译文候选（已部署、契约已验证）

`009_source_candidates.sql`、`010_translation_candidates.sql` 不增加表或存储服务，补齐执行尝试、上下文产物的复合外键和幂等约束。

- `services/candidates.py` 与 `POST /attempts/{id}/source|transcript|translation` 共用契约。业务 CLI 对应 `candidate source|transcript|translation <尝试UUID> --input 文件.json --request-key <幂等键>`；登记要求原执行者的当前运行中尝试，租约及取消状态重新检查。源和分段集的并发相同请求各返回同一版本。
- 来源绑定本尝试原片 artifact，媒体类型和时长必须匹配；来源 URL 从排队时冻结的任务输入读取，不能由候选请求替换。一个 run 只绑定一次来源；修改原片需新来源修订/轮次。
- 原文请求包含 source_id、strategy_revision、完整 cues 与 segments。cue 保留角色、原文、源时间窗；每段只提交 cue_numbers，段文本及时间窗由服务器派生。所有本次 cue 恰好覆盖一次，禁止漏段、重复、乱序和跨角色合并。已有来源修订的 cue 不能被覆盖或省略；重分段只新建封存分段集，不修改原始 cue。
- 译文包含 segment_id、context_artifact_id、chinese_text、model_id、prompt_revision。段必须属于本轮来源的封存分段集；上下文必须是本尝试的私有 JSON。输入摘要绑定原文、上下文摘要和模型/提示修订；改稿形成新 revision，不覆盖旧文本或自动生成审核通过。
- 数据库只验证关系与元数据，来源身份、原文覆盖和翻译语义仍需独立复核。`identity_evidence` 是来源描述及证据字段，不是“身份通过”的布尔开关。

61 项独立库联合检查及 7 项真实服务集成检查通过。实际来源测试使用专门的 `[契约验收]` 项目、`example.invalid` URL、2 秒无语音合成视频及人工原文/译文夹具，始终是 acceptance；它们不是 Yale 整片、ASR、翻译模型、媒体质量或 RTF 验收。候选 CLI 适配已实现，本轮候选专用路径直接实测范围为 ASGI/相同服务与真实 Supabase；完整业务 CLI/Web 操作仍待生产图与工作台联调。

最新证据：`docs/evidence/deep-dub-source-candidates-20261018.json`。

## 音色、情绪参考与音频候选（登记契约）

`011_audio_candidates.sql`、`ReferenceInput`、`AudioInput` 已接入共用服务、API 与业务 CLI 的 `candidate reference|audio`。

- 音色参考的 subject_id 是本轮来源中的 speaker ID，情绪参考的 subject_id 是 segment ID。情绪窗口必须精确等于原声段窗口，不能跟着可编辑的配音播放落点移动。原版和处理版产物都需属于本尝试；情绪参考也新增原版产物外键，不把它埋在无外键的 JSON 中。
- 原版音频时长应匹配源窗口（登记层容许 50 ms 元数据差异）；处理版保留独立产物与参数。这个容差不是自然度、身份、语义或对齐质量通过标准。
- 音频候选绑定具体译文、固定音色、情绪参考和本尝试 WAV/JSON 处理回执；角色/段/项目/空间由复合外键约束。生成与 PCM 派生分开，生成记录模型身份，PCM 不能换文本/参考或改写父版本的生成倍率。
- 音频登记要求声明实际 CPU/GPU 设备且租约有效。每次 PCM 倍率与累计倍率均需在 0.90–1.10；派生 WAV 元数据时长还须与父版本/倍率相符。多次小幅变速不能绕过累计限制。
- 默认规则与审核仍独立；登记不调用声学代理或自动把质量写成通过。模型参数、声学指标或处理回执也不证明已做人耳身份/自然度听审。

67 项独立数据库检查及 7 项真实集成检查通过。参考实测使用 1 秒静音 PCM、无语音视频和 acceptance 项目，证明关系和权限契约；音频成功/PCM 链使用独立库合成设备及元数据。真实 API 只验证无设备时拒绝音频登记，不能写成真实 TTS 生成已通过。模型服务仅做只读健康/模型列表探测，没有加载、卸载或生成请求；下一次领取前仍需重新核实活动作业与资源。

证据：`docs/evidence/deep-dub-audio-candidates-20261018.json`。

## 背景、成片与实际媒体原语

`012_render_candidates.sql`、`BackgroundInput` 和 `RenderInput` 补齐登记契约；CLI 的 `candidate background|render` 与相应 API 复用同一个服务。处理阶段需有效设备租约，产物必须由本次尝试产生且元数据覆盖全片。默认保留背景；诊断移除轨不能作为背景或加入成片。render 绑定本轮来源/质量规则的封存时间线，并保存对白、混音、字幕、视频和处理回执。输入/输出与参数变化形成新候选，不重置 run 的原始计时，不自动记质量或发布通过。

`nodes/media.py` 提供实际 ffprobe 探测与 FFmpeg 全部音视频流解码，分别执行。完整解码检查退出码、结束进度和媒体尾部覆盖，前后核对文件摘要，保留工具版本及容差；字幕结构、语义、身份、自然度和混音质量仍另行验收。原语本身不签发设备租约、不写审核通过；授权、设备心跳和证据登记由下节本地 CPU 运行器负责，GPU/远端执行仍待接入。

Windows 宿主为 psycopg 使用 SelectorEventLoop，不能直接调用 asyncio 子进程接口；媒体原语使用独立通信线程管理自己的子进程，不改全局策略。超时/取消只结束并回收本次子进程，实测保留另一个进程运行。该行为尚未覆盖远端模型任务的取消/接管。

`ArtifactService.download` 为节点下载私有工作副本：使用当前身份/RLS、逐块核对 SHA256 和字节数，核验后原子发布本地文件，拒绝覆盖既有文件，清理部分下载临时文件。不将宿主机路径写入公开媒体证据。

本轮 77 项联合检查包含 72 项独立数据库检查和 5 项实际媒体/进程检查，另有 7 项真实服务集成检查。媒体检查是 2 秒合成音视频，包含“元数据时长仍有效但媒体包被截断”的实际解码失败案例；背景/成片成功登记仍是独立库合成设备和元数据，真实 API 验证无设备时拒绝。没有实际背景分离、生产成片或整片性能结果。证据：`docs/evidence/deep-dub-render-media-20261018.json`。

## 实际本地 CPU 阶段（已运行验证）

`services/runtime.py` 负责阶段领取、设备租约、续租、取消、回执与已提交结果重放。`services/local_pool.py` 使用跨进程文件锁、启动前日志、实际子进程退出记录和独立历史文件；旧日志未收尾或损坏时拒绝自动接管。阻塞散列使用独立线程 Future，取消会等到线程真正结束，不能提前把后台工作标成已停止。

这是本工作区**单并发媒体 CPU 逻辑池**，FFmpeg 解码器线程数设为 1，不表示物理 CPU/其他应用完全空闲，也不是 GPU/远端资源调度。首次登记/刷新探测需要 `deep_dub.device.manage`，默认管理员可执行；普通业务用户可提供管理员已核验的 `device_id`，无权自动刷新设备。当前探测有效期和设备租约约束仍由数据库执行，不放宽权限。

```powershell
# 先按既有契约创建并启动 run，且媒体产物与 run 属于同一项目。
uv run python -m apps.deep_dub.cli runs decode <运行UUID> <产物UUID> --request-key <幂等键>
uv run python -m apps.deep_dub.cli --transport http --endpoint http://127.0.0.1:8283 runs decode <运行UUID> <产物UUID> --request-key <同一幂等键>
```

对应 `POST /runs/{run_id}/decode`。阶段会带当前身份下载并验真输入，在租约内实际解码、保存私有证据，冻结结果后记录带证据的 `full_decode` 技术评分。重放也需读回并核对证据与原输入，不能只凭成功状态记分。`run` 不因此自动技术完成，也不自动把字幕、语义、音色、自然度或整片性能判为通过。验收运行在 Langfuse 标为“验收运行”、environment=acceptance，session=run、user=原任务创建者；已实际读回 NUMERIC 技术评分和身份。

取消时先停止并等待本阶段进程，然后确认释放；权限撤销后，原用户的租约释放请求同样会被拒绝，需设备管理员核对。实测在单独测试账号撤权后停止 FFmpeg，拒绝新的成功回执，未确认租约保留；管理员等正常租期到期后凭退出证据核对恢复，测试结束该账号保持 viewer。没有修改其他用户角色，没有停用外部模型进程。异常启动间隙、崩溃遗留日志和远端模型任务的安全接管仍需继续实现，不能用本地取消测试替代。

最新联合检查为 81 项（72 独立数据库、9 文件锁/线程/媒体/进程）与 10 项真实集成。实际解码、取消和撤权使用 acceptance 运行及小型合成视频，不是整片生产；没有 TTS、背景分离或成片。证据：`docs/evidence/deep-dub-local-runtime-20261018.json`。

## 原声参考准备与详情试听

`runs prepare-reference <运行UUID> <段UUID> voice|emotion` 已连接默认本地 CLI、HTTP 和同一业务服务。实际 CPU 租约中准备 24 kHz PCM16、保存原/处理音频与技术证据；音色使用同角色段内 3–20 秒，情绪精确取本段原声。详情的音色 Sheet 显示真实波形、互斥试听、前后指标表及折叠证据；媒体失效后可重新按当前身份连接。21 项工具检查、1 项真实服务/两种 CLI 集成、类型与构建通过，浏览器两版音色重连恢复通过。Web 的提取按钮已连接但尚未正向写入验收；身份/听感保持未测，没有 TTS 或整片生产。详见 [原声参考准备](../../docs/deep_dub原声参考准备.md)。

## 真实 GPU 配音候选

`runs speech <运行UUID> <段UUID> <译稿UUID> <音色参考UUID> <情绪参考UUID>` 接本地/HTTP CLI 与同一业务服务，正式详情已实际点击生成、保存私有新 WAV、显示波形并同步试听。受控本机进程持有 GPU/旧路由锁与数据库租约，取消要确认实际退出再释放；不停止远端或未知模型。20 项工具、2 项真实 GPU 集成、类型与构建通过；Langfuse 实际读回 PCM 契约与三个未测质量项。私有 `[deep_dub].tts_config` 已配置，公开模板为 `tts.local.example.json`。当前每次冷启动，只有 Windows NVIDIA 本机默认设备管理员执行路径，未完成云/远端、安全驻留、独立质量、整片生产/RTF 或发布。详见 [真实配音生成](../../docs/deep_dub真实配音生成.md)。

## 独立回转录与声音版本证据

`runs review-audio <运行UUID> <声音版本UUID>` 接相同本地/HTTP 契约，详情的声音版本可实际运行独立 ASR 并查看识别文本、绑定译稿、CER 和模型摘要。期待译稿不进入 ASR 提示，符号/数字等差异不静默抹平；CER=0 也不自动签发事实、专名或听感审核。24 项工具检查、1 项真实新 WAV→ASR→三入口集成、类型与构建通过；浏览器操作和 Langfuse 代理分/未测状态及用户/session 读回通过。私有 `[deep_dub].asr_config` 已配置，公开模板为 `asr.local.example.json`。源整片 ASR、强制对齐、独立语义/听感、性能优化及完整生产/发布继续。详见 [独立回转录](../../docs/deep_dub独立回转录.md)。
