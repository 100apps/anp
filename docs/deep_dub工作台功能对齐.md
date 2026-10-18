# deep_dub 工作台功能对齐与验收设计

状态：**已实现并部署 28 表及受限执行、设备租约、评分/outbox、发布账本契约；本机 Langfuse v4 的身份与未测评分已实际读回。完整工作台、生产图与整片任务仍未实施完成。** 本文不把页面代码存在、旧任务已有结果或设计对应关系当作新工作台已通过。

参考目录：`D:/github/dub-bench-zh/dubbing-studio-an`。源文件摘要与取证边界见 [源码证据](evidence/deep-dub-workbench-parity-20261018.json)。旧工作台未在本轮启动或执行写操作。

## 对应关系

以下新服务名称是待实现的业务契约；Web、HTTP CLI 和默认本地 CLI 都应调用它们。本地 CLI 直接执行服务及节点，仍复用用户权限和平台存储。

| 旧源码可见功能 | 源码位置 | 新业务设计 | 实施后的验收 |
| --- | --- | --- | --- |
| 全任务列表、搜索、状态筛选、来源系列分组 | `web/index.html:23`、`draw:35`、`/api/tasks` | 项目列表和全量统计；系列标签为来源元数据，不能代替项目 ID | 超过分页大小后统计仍正确；筛选与分组不改变生产记录；跨空间不可见 |
| 阶段进度、生产/交付墙钟、覆盖与未计量时间 | `web/index.html:33–40`、`workbench.py:_wall_coverage` | run 时间轴和阶段区间；同时展示等待、未计量及发布状态 | 不把阶段累计占比当完整墙钟，也不把有技术成片当已发布 |
| 高/中/低质量档位、用户应用档位 | `web/workbench.html:24`、`/api/quality-profile`、`quality_profiles.py` | 不可变 `deep_dub_quality_profiles` 修订；项目选择和 run 输入冻结 | 默认 high；未知档位拒绝，禁止自动降档换取 RTF；切换不删除返修账本或旧产物 |
| 静音画面与统一播放位置、空格播放暂停 | `bindTransportVideo:43`、`playTransport:60`、`pauseTransport:61` | 统一 transport 驱动画面和监听音轨，切换项目销毁旧媒体节点 | 画面始终静音；跳转/暂停/段切换后不出现叠播；可观察同步偏差 |
| 按说话人的原声/配音轨，原版/处理后背景，M/S | `allTrackKeys:46`、`isTrackAudible:48`、`syncMixer:56` | 多轨监听组件；可同时独听多个轨，默认原声与原版背景静音 | M/S 组合符合实际混音；监听选择不悄悄改变最终成片输入 |
| 时间线缩放、适应全片、聚焦当前段、滚轮与双击 | `web/workbench.html:26`、`renderTimeline:109`、`focusTimelineSegment:123` | 业务时间线组件与 shadcn/ui 控件；原始窗和配音落点分开显示 | 缩放保持锚点、正确横移与定位，长片能操作；不能用普通进度条代替 |
| 分段导航、原文、译文候选、问题与未测状态 | `draw:124`、`drawFocus:176` | 分段表与详情 Sheet；候选和独立审核状态分开 | 带入候选不自动通过审核；键盘/时间线选择同一稳定段 ID |
| 保存译稿、查看历史译稿/音频、设为当前 | `/api/translation`、`/api/select-version`、`renderVersions:167` | 不可变 translations/audio_attempts、原子 selections RPC | 旧版本保留；选择校验段、文本、参考与审核输入；旧审核不得套用到新版本 |
| 独立编辑配音播放起点 | `/api/playback`、`studio.py:set_playback` | timeline_revisions 与 timeline_clips；不改原文时间窗 | 默认对齐旧边界与碰撞规则；位移使组合审核失效，但不移动原声情绪参考窗口 |
| 当前段重新配音、0.90–1.10 时长系数、异步状态 | `/api/synthesize`、`/api/job/<id>`、`studio.py:synthesize` | stage/attempt 持久化作业；明确 duration_factor、seed、emotion_alpha 与实际模型身份 | Web/两种 CLI 约束一致；超界拒绝；重试幂等，重启后可查询；保留失败和派生 attempt |
| 说话人改名、参考候选原版/净化版试听与登记 | `/api/speaker-name`、`/api/voice-candidates`、`/api/register-voice` | 稳定 speaker ID；不可变 voice_reference 版本与源 artifact 关系 | 显示名不能改角色身份；源/处理版均可追溯；声学代理排序不冒充身份听审 |
| 当前段原声作为 TTS 情绪输入 | `drawFocus:181`、`studio.py:synthesize:663` | 独立 emotion_references，绑定源版本、段和准确原声窗口；audio_attempt 显式引用 | 段重切或源版本变化会失效；固定音色与逐段情绪参考不能混用；不支持该输入的模型不能宣称功能已对齐 |
| 翻译/音频来源、分项评分、参考处理前后指标 | `translationScoreMarkup:157`、`audioScoreMarkup:159`、`referenceReport:164`、`versionScoreMarkup:166` | 数据库 scores/audits 与 Langfuse 观测、证据对象和方法版本关联 | 未测自然度、身份或语义保持未测；分数能回溯到所选版本，失败与旧分数可见 |
| 全片结构验收、问题段与情境证据 | `/api/validate`、`drawFocus:189` | 版本绑定的审核作业；技术门禁与主观判断分开 | 生成中拒绝将不完整输入判为通过；输入变化重审；实际完整解码不能用元数据探测代替 |
| 多种配音策略的整片音频/波形对照 | `renderStrategyComparison:162` | renders 增加 strategy_key；对照记录绑定同一来源、分段和策略输入快照 | 比较展示真实产物、实际配置与得分；不能将别的 run 的最快产物当成本轮产出 |
| 源/配音/背景/参考音频的波形和媒体获取 | `/api/waveform`、`/media/*`、`getWave:150` | 私有 artifact 服务与按内容摘要缓存的波形 | 媒体 Range/续播、过期链接、跨空间拒绝实际验证；前端不暴露宿主文件路径或服务密钥 |
| 静态只读审阅视图 | `workbench.py:page_html(live=False)`、`offlineMedia` | 只读审阅能力需保留；离线媒体包是独立的授权导出能力，不能拿在线 viewer 权限测试冒充离线验收 | 不嵌入会话、密钥或长期公开链接；撤销/过期与离线副本边界明确，导出功能仍待实施验证 |

## 需要补齐的模型关系

原草案已覆盖来源、角色、分段、译文、音频、审核和选版，尚需补齐以下关系，详见 [关系草案](deep_dub设计草案.md)：

1. `deep_dub_quality_profiles`：档位 key、revision、规则、规则摘要、创建来源。规则采用不可变修订；run 和最终 render 引用准确修订。默认继承旧 catalog 的 high，不允许按速度目标自动降档。低档也不取消正文、事实、数字、否定、专名、可解码和越界/碰撞硬门禁。
2. `deep_dub_emotion_references`：segment、source 修订、原声窗口、artifact、生成/裁剪摘要。audio_attempt 同时引用 translation、固定 voice_reference 和逐段 emotion_reference，幂等键覆盖全部输入。移动配音播放位置不能改变原声引用。
3. `deep_dub_timeline_revisions` 与 `deep_dub_timeline_clips`：组合修订、segment 的 selection、准确播放落点和组合参数。来源的 start/end 不承担可编辑配音位置的职责；render 引用不可变 timeline 修订。
4. renders 记录 `strategy_key`、quality_profile 修订、timeline 修订和组合输入摘要。试听 mute/solo 属于监听状态；改变生产增益、裁剪、位置或选版必须形成新组合输入，不能复用旧成片审核。

上述表继续使用 workspace 复合外键/RLS。timeline clip 的 selection 必须同时属于同空间、同项目、同分段集及同段；仅检查 selection ID 存在仍不足够。选择、档位切换和时间线提交走乐观锁及原子 RPC，后台结果提交再次检查版本和权限。

旧播放位置规则允许的 500 ms 前移、50 ms 邻段容差和 0.90–1.10 时长系数是本次核对到的规则，不是通用质量结论。新实现默认保留其语义；若变更，应显式创建规则修订并重做对应审核，不能为了提速静默放宽。

## 实施门槛

平台最新云制品的本机拉取、启动及 DBX 性能已经补验。当前 28 表、执行与私有对象、来源/原文分段/译文候选契约已部署，81 项联合检查（72 独立库、9 文件锁/线程/媒体/进程）及 10 项真实集成检查通过；本文件上方的工作台功能仍为待实施/待验收。参考/音频/背景/成片登记契约已部署（正向生成/成片仍仅独立库夹具），本机CPU解码运行器已实际接通租约/取消/撤权清理，继续接其他实际执行、设备与发布服务适配、远端单次尝试授权，再实现媒体 transport/多轨时间线与业务服务。验收须覆盖两种 CLI 和 Web 的同一版本操作，随后才接入真实两 GPU/两 CPU 生产流程并验证整片 RTF≤0.5 与 B 站回执。独立库的合成设备、质量审核和投稿回执不是实际媒体或发布证据，2 秒无语音合成视频及人工原文/译文也只是 acceptance 契约夹具。

## 2026-10-18：shadcn/ui 任务详情改版

### 本轮范围与入口

已阅读「打开工作台」会话，并实际浏览 `http://127.0.0.1:8765/` 的任务列表和 `task-a0d1ec97dbd1` 详情。新页面使用 React、现有 shadcn/ui 组件和独立任务适配器，业务实现位于 `apps/deep_dub/ui/review/`。

在项目根目录执行：

```powershell
pnpm dev:deep-dub
```

打开 `http://127.0.0.1:8766/deep-dub.html?task=task-a0d1ec97dbd1`。需要原工作台 8765 已运行；可通过 `task` 查询参数切换旧任务，`segment` 参数定位语义段。不会启动或切换平台、数据库、模型生产任务，也不会替换原页面。

这是**本机旧任务兼容入口**，数据和写操作仍由原工作台处理；不是 V3/Supabase 权限迁移的验收结果。兼容桥只在 Vite 的 `deep-dub-review` 模式注册，启动命令绑定回环地址；不会注册到平台 API 或正式构建服务。后续平台接入必须通过 `TaskAdapter` 使用原用户身份与 V3 业务服务，不可将此本机桥作为平台权限旁路。

### 信息布局与交互

- 左侧为独立滚动的分段导航，支持原文、译文、段号、说话人搜索，以及返修、情境、未通过筛选；保留当前段和未保存草稿标识。
- 中间是静音同步画面、统一 transport 和多轨时间线。原声、配音和处理前后背景分别 M/S；多个轨道可同时独听。点击静音轨的 S 会自动解除该轨静音。监听设置不写入最终混音。
- 右侧保持当前段上下文，分为「译稿与配音」「版本」「诊断」。切段保留各段内存草稿；关闭页面前提示未保存内容。改稿和位置更新使用明确的保存按钮，历史译稿和音频保留。
- 当前原声/配音波形的播放和定位共用视频时钟；历史配音、音色和策略音频为独立互斥试听。原声窗口与配音落点分别显示。声音版本展示绑定的译稿、模型和分项评分。
- 全片适应、聚焦段、缩放、Ctrl+滚轮缩放、Shift+滚轮横移，以及空格播放/暂停、J/K 切段、左右方向键跳转均已实现。文本输入不触发全局播放快捷键。
- 波形来自实际接口；全片总览用片段块，放大后只请求视区附近的波形，避免一次载入整集。声音每帧同步，表单和文字按 20Hz 更新；波形与片段组件使用 memo 避免反复绘制。
- 说话人/固定音色、质量与交付、成片/策略对比通过 shadcn Sheet 展开。手机使用分段、画面、当前段三个视图；桌面使用三栏。

### 已运行验证

| 检查 | 结果 |
|---|---|
| TypeScript 类型检查 | `pnpm --dir console exec tsc -b` 通过 |
| 独立详情入口构建 | Vite `deep-dub-review` 构建通过，制品在 `.local/deep-dub-review-dist`；不声称该静态制品自带兼容桥或已发布 |
| 原任务载入 | 106 段真实数据、译稿 v1/v2、音频 v1、模型与历史时序评分可读 |
| 真实视频 | 浏览器 `readyState=4`、无媒体错误；实际播放和暂停，画面静音 |
| 波形定位 | 原声波形中点定位后，视频约 13.03s、段内约 12.25s，相差原窗起点 0.78s |
| 草稿 | 加入临时未保存文字，切到下一段再返回仍保留；恢复原文后保存按钮禁用。未向原任务保存该草稿 |
| 搜索 | `seg0107` 得到 1 段，清空后恢复 106 段 |
| M/S | 多轨 S 可同时选中，原声 S 自动解除静音；恢复后只监听中文配音与处理后背景 |
| 质量证据 | 历史 RTF 2.212、时序代理 83.3、情境取舍与输入摘要、语义须独审及听感未测原样显示 |
| 音色试听 | 原声/处理后参考实际载入，可播放；处理后音频 `duration≈11.736s`、无媒体错误，主画面暂停 |
| 波形懒加载 | 全新加载后聚焦当前段，只出现 6 个附近片段波形，不再在聚焦瞬间请求整集 212 条波形 |
| 响应式 | 1280px 默认桌面、1728px 三栏以及 390px 手机检查；390px、1728px 页面均无整体横向溢出。时间线自身允许横向滚动 |
| 音频 Range | 实际音频请求返回 206、256 字节和对应 Content-Range |
| 写入入口隔离 | 缺少来源、跨站来源请求均 403；有效本机来源的不存在说话人请求由原业务服务拒绝为 400 |
| 任务保护 | 只读回归前后原 task.json SHA-256 相同；正式译稿、选版和生产记录未在本轮被修改 |

可重复的只读接口回归：`node apps/deep_dub/dev/verify-review.mjs`。该脚本针对本机指定旧任务，唯一通过来源校验的 POST 使用不存在的说话人，必须被业务校验拒绝，不创建或修改版本。

本机证据保存在 `.local/deep-dub-review-api.json`、`.local/deep-dub-review-build.log`、`.local/deep-dub-task-review-evidence.json`、`.local/deep-dub-task-desktop.png`、`.local/deep-dub-task-mobile.png`。这些包含本机任务界面，不作为平台镜像或新成片证据。

### 已实现但未进行正向生产写入验收

保存译稿、更新落点、切换译稿/声音版本、登记音色、重新生成并轮询作业、更新档位和运行结构验收均已连接原有业务接口。本轮没有对这条正式任务执行这些正向写入，也没有触发实际 TTS 或重做成片；不得据此宣称整片生产、V3 同契约 CLI/Web、RLS、两 GPU/两 CPU 或 B 站发布已经通过。离线导出与 V3 数据迁移仍按上方清单继续验收。
