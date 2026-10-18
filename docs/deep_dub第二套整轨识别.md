# deep_dub 第二套整轨识别

## 目的与证据边界

Qwen 原分块和接缝改窗仍来自相同声学模型，不能用同模型复测冒充独立内容审核。本阶段增加 Nemotron ASR，对同一份原始完整 PCM 做不同家族、不同权重的第二遍无正文提示识别，并分别保存与第一套识别、耶鲁官方参考的文本差异。

“第二套识别器”不等于“正文审核通过”。不同模型也可能同时漏听、误识专名或丢失否定；双方一致、相似率或差异计数均不能直接替代事实、角色、字幕和听感审核。原稿、原词时间、来源版本与待审运行不会被本阶段改写。

## 模型与运行时来源

本地已安装 audio.cpp 的 `nemotron_asr` 文档说明了离线识别、英语 locale `en-US`、GGUF 和模型派生的 token 上限。运行时文件保持现有固定摘要，不另执行未经核对的下载程序。

[NVIDIA 模型说明](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b) 记载了 Nemotron 的 FastConformer-RNNT 架构及英语支持；本次选择本地 audio.cpp 模型目录中指定的 [audio-cpp GGUF 转换仓库](https://huggingface.co/audio-cpp/audio.cpp-gguf/tree/661a224e9d9adbd49c01626f85e426f5f425d771/Nemotron-3.5-ASR-Streaming-0.6B-GGUF)。这里区分 NVIDIA 原模型与 audio.cpp 转换制品，不把后者称为 NVIDIA 原始权重文件。

- 转换仓库实际固定修订：`661a224e9d9adbd49c01626f85e426f5f425d771`。
- 文件：`Nemotron-3.5-ASR-Streaming-0.6B-GGUF/nemotron-3.5-asr-streaming-0.6b-q8_0.gguf`。
- 实际字节数：`930625888`；SHA-256：`c58b62c1bdd6c5d7b14b08126c5bd16c1289fee6e0d6ac0761dbd3aa6d714935`。
- 已从固定修订下载并逐块核对大小与摘要，7.375 秒；不是模型执行速度或整片 RTF。模型缓存、私有配置和带签名的临时下载地址均不提交。

## 实现与配置

业务代码仍在 `apps/deep_dub`。`model_pool` 新增 `nemotron_asr` 家族和 `independent_asr` 配置用途；原 Qwen、TTS 与强制对齐的用途不变。公共配置新增 `[deep_dub].independent_asr_config`，默认留空；本机私有配置指向已经核实执行器、权重摘要、物理 GPU UUID 和原跨应用协调锁的模型配置文件。

共用模型调用层针对 Nemotron 把英语/中文提示映射为 `en-US`/`zh-CN`，使用模型派生 token 上限 0；原稿和参考文字不放进请求正文。原 Qwen 请求和证据规则保持原样。请求身份、HTTP 客户端和生命周期仍独立，每次核对实际监听 PID，不缓存授权或归属结论。

`nodes/source_independent_asr.py` 使用第一套识别保存的完整 PCM，连续 30 秒分块，验证提交样本覆盖、每块 PCM 摘要、模型家族/权重、无文本提示和模型会话停止。`services/source_independent_asr.py` 冻结来源、原识别/PCM/参考证据、模型实际摘要与超时，登记唯一证据及分项评分；读取会重新校验这些绑定。

迁移 017 仅把第二套识别节点加入待审证据计算白名单，继续要求 GPU、当前来源、运行/审核权限和原待审门禁。正式候选登记仍被拒绝，不把运行临时改为生产中。迁移前五库已备份并逐库实际恢复通过，私有目录 `.local/backups/v3-postgres-before-cloud-20261018T195658Z`；不是跨四存储的原子灾难恢复。

## 三种入口

```powershell
uv run anp deep_dub runs recognize-source-independent RUN_ID SOURCE_ID --request-key STABLE_KEY
uv run anp --transport http deep_dub runs recognize-source-independent RUN_ID SOURCE_ID --request-key STABLE_KEY
uv run anp deep_dub projects source-independent-recognition PROJECT_ID SOURCE_ID
```

默认 CLI 直接调用同一服务，无需业务 Web；HTTP CLI 和工作台使用 `POST /runs/{id}/recognize-source-independent` 及 `GET /projects/{id}/sources/{id}/independent-recognition`。工作台展示第二套候选、实际停止状态与两类差异数量；同输入重放复用冻结结果，仍需当前身份。

## 已运行验证

- 49 项工具检查 8.29 秒通过：包含第二套证据的不同家族/权重、连续样本、无提示、会话及未测状态绑定；原 Qwen 与新增 Nemotron 的真实本机 HTTP 子进程夹具共同验证语言参数、空文本、上游错误、取消和输入变化。HTTP 夹具不是实际模型或内容质量。
- 9 项独立数据库契约检查 6.67 秒通过：迁移重复执行、指定 GPU 待审阶段、禁止正式原文登记，及既有接缝返修契约。设备为合成夹具，不冒充实际 GPU 租约。
- 前端 TypeScript 与冻结生产制品构建通过。

## 完整重试及正式制品验证

修复后的制品 `20261018200402-00558d34` 经 Compose 重建并健康启动，十一组件 ready。保留前述失败尝试，按同一阶段输入新建第 2 次真实尝试；没有把旧失败改成成功或复用未保存的前 44 块。

- 完整 `41,380,932` 个 PCM 样本、`2586.30825s` 音轨连续提交，87 块、87 次请求、86 次已加载检查，空文本 0；第二模型与原 Qwen 的权重和家族均不同。受控进程确认退出，模型服务退出码 1 原样保存。
- 与第一套文本 247 处差异、与官方参考 276 处差异。数字形式、标点、分块截词等均会影响计数，不能据此排名准确率、宣称哪方正确或自动通过内容审核。
- 实际完整执行、停止、来源/运行/队列不变、六项 Langfuse 评分及身份、默认本地/HTTP CLI 同输入重放与读取的整项验收 61.91 秒通过。第一次整轨计算由冻结 HTTP API 执行；两种 CLI 本轮验证的是重放与读取，不冒充再次完整计算。模型会话生命周期约 23.968 秒、阶段约 32.656 秒只是该阶段记录，不是整片译配 RTF。
- GPU 租约真实为 `released` 且 `operation_stopped=true`；本次 175 个登记操作都有退出回执。正式工作台实际显示完整第二套候选，点击确认后返回同一结果，日志摘要未变化，未重复加载模型。390px 窄屏实际文档宽度为 390。
- 修复后的最终工具检查 63 项 13.85 秒、独立数据库联合 45 项 20.68 秒、冻结生产联合 27 项 83.76 秒通过。联合回归包含旧模型调用夹具、原来源读取、待审门禁、CPU 委派、Agent 持久化与平台/业务接口。
- 原 448 条线程全部最终值和来源准备的完整父/子检查点保持；回归后共 450 条线程，27 个受保护容器 ID 不变。原待审运行没有自动继续或技术完成时间，也没有新源稿审核/正式分段。

实际运行镜像和业务 Python/SQL 与冻结清单一致。本机制品仍包含工作树既有核心未提交修改，未新发布/拉取 GHCR，未在本轮重新实测 IDE F5。公开证据：[执行、失败保留与保护检查](evidence/deep-dub-independent-20261019.json)、[工作台截图](evidence/deep-dub-independent-browser-20261019.png)。

## 保留的第一次失败与恢复

第一份制品 `20261018195718-b7dd3a6e` 实际执行了前 44 块 Nemotron 识别，在第 45 块 FFmpeg 启动前的日志原子替换发生 `WinError 5`。真实测试 26.40 秒后收到 HTTP 500。原失败尝试 `0475833c-dac1-4952-854c-677e38d08a4e` 保持失败，不能改记为成功；临时目录清理后不使用未封存的前 44 块充当完整识别产物。

确认本次模型进程已退出后，日志仍有尚未落成进程的 `pending_spawn` 保留，GPU 租约进入隔离流程。没有因为 HTTP 已返回就把“记录不明”当作可分配。

已用真实 Windows `CreateFileW` 读句柄复现禁止删除共享导致的原子替换失败；原始故障的具体占用句柄所属进程未定位，不能断言一定由某个观察器或杀毒软件造成。修复为每次独立临时文件、同进程串行写入及限定 Windows 错误码的两秒内重试，仍保留 fsync 和原子替换。持续占用或未知错误继续抛出，不关闭安全保护、不猜测写入成功。实际占用恢复、持续占用拒绝、磁盘错误及并发完整性检查通过。

恢复时先正常停止本工作区业务进程，核对旧 API 与模型 PID 均不存在，90 个已登记操作都有实际退出回执；异常栈证明剩下的是 `Popen` 之前失败的 FFmpeg 启动预留。保存原日志/错误备份、获取原跨应用及物理池文件锁后，才清理该预留。随后以当前设备管理员身份，通过既有 `deep_dub_reconcile_device` RPC 核对唯一失联租约、停止证据和设备版本并恢复可分配状态。旧失败、退出码、计时、原来源和待审状态均不改写。

该修复修改了共用 CPU/GPU 日志保存层，未覆盖同文件中另一会话既有的状态根目录修改。43 项针对性检查 13.58 秒通过，Windows 句柄测试修正为幂等关闭后另重跑四项 0.33 秒通过；最终合并工具检查和完整重试结果见上节。

## 未完成

正文独立审核、说话人身份与分段、字幕时钟映射、完整生产图、V100 真实执行、整片质量/RTF≤0.5 和 B 站回执仍须完成，不能把第二遍识别或局部速度作为最终交付。
