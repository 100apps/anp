# deep_dub 补充盲转录与原生词时间

状态：完整待审来源新增 Parakeet-TDT 盲转录与原生词时间候选，真实计算、API/两种 CLI、Langfuse 与 Web 统计已验证。**正文、人物、实际字幕边界仍未通过；不是正式整片生产、RTF 或发布验收。**

## 选择与固定工件

此前 Voxtral/Qwen2.5-Omni 的比较意见存在错误或相同引文报差异，原失败不改写。本轮增加不接收候选正文的声学识别器，不继续对同一比较模型重试直到得到期望结论。

原始模型为 [NVIDIA Parakeet-TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)，官方说明支持无额外提示识别与词级时间；实际使用的是 audio.cpp 项目发布的转换工件，不能称为 NVIDIA 原始格式，也不能以公开模型指标代替本片质量。它与 Qwen3-ASR 不同，但与 Nemotron 都在 NVIDIA 生态内，不能声称训练数据或错误统计独立。

| 项目 | 固定值 |
|---|---|
| 工件仓库 | `audio-cpp/audio.cpp-gguf` |
| 提交 | `661a224e9d9adbd49c01626f85e426f5f425d771` |
| 路径 | `Parakeet-TDT-0.6B-v3-GGUF/parakeet-tdt-0.6b-v3-f16.gguf` |
| 字节 | 1,255,384,320 |
| SHA-256 | `83622c44275694039e543e135ac91b40d94f48fc80a1bf187b5a1cbcd5dd8649` |
| 原生运行器源码修订 | `f16f23abf9732b1904fed1f15bddbe9909d21146` |

通过固定提交的 Hugging Face 元数据取得 LFS 大小和摘要，再按完整 64MiB Range 下载，逐块验证 206 与完整 Content-Range；只有整块完成才落盘，最终全文件 SHA 一致才启用。本次下载约 100.42 秒，不属于正式生产墙钟。只复用已有执行器，不升级或停止其他模型。

## 契约与旧证据保护

`ModelConfig` 新增 `parakeet_tdt`，独立私有配置键 `deep_dub.blind_asr_config`。原 Nemotron 配置和默认读取不变。以下命令必须显式选择新识别器：

```powershell
uv run anp deep_dub runs recognize-source-independent <run-id> <source-id> --recognizer parakeet --request-key <key>
uv run anp deep_dub projects source-independent-recognition <project-id> <source-id> --recognizer parakeet
```

HTTP 使用原补充识别契约增加 `recognizer=parakeet`，Web 使用模型选择框。新模型有独立输入摘要和规则 `source-secondary-parakeet-contiguous-words-v1`，不借旧请求键覆盖旧输入。读取也按模型选择，默认返回原 Nemotron，原跨模型队列不被新模型静默替换。

本轮仍使用受限的 `source_independent_asr` 阶段：只允许当前待审来源、GPU 和审核权限，只保存不可变证据；原迁移的禁止登记生产候选门禁保持。没有新增或放宽审核通过方法，也没有放行旧准备图。

识别请求 `text` 为空；给定英语语言，不提供原 ASR、官方参考、词表或期望正文。连续 30 秒 PCM 分块与同一完整原始 PCM 的样本/摘要绑定，整批使用一个受控模型会话。原生词数据来自 TDT 发射样本位置，校验整数样本边界、单调不重叠、完整文本覆盖和有限数值；保留 `word_timing_review=not_measured`。该格式校验不是实际发声边界审核。

## 已运行验证

- 15 项节点/输入/词时间检查通过；显式开启独立库后合计 18 项通过（3.31 秒），TypeScript 通过。首次元数据查询的本机命令存在语法错误，未执行网络或写入；改为保存脚本后成功，没有将失败记为通过。
- 本机预检约 13.4GiB 空闲，无外部模型进程；只占用配置的物理 GPU 锁/设备租约，不停止其他作业。
- 完整 **41,380,932 样本 @16kHz** 一次阶段处理 87 块、87 请求，warm 检查 86 次，空文本 0。输出 **5,660 项原生词时间**，模型生命周期约 26.11 秒，阶段 35.531 秒，实际集成测试墙钟 **73.86 秒**。
- 原生进程退出码 1 来自受控终止，租约 released/stopped。不能把阶段耗时或模型报告当作整片 RTF。
- 与第一套识别有 **140** 处标准化文本差异，与官方参考有 **176** 处差异。它们包括讲者标签、口头填充、词形及实词等候选；不判哪方正确，不把差异数当真实错误数。
- HTTP 401/403、同一成功阶段默认本地 CLI（不可用 HTTP endpoint）/HTTP CLI 重放通过。6 项 Langfuse 得分、当前阶段用户/session 真实读回，内容和字幕保持未测。
- Web 实测模型切换后显示 87 块、0 空文本、140/176 差异、5,660 词；没有再点运行。截图见 [补充盲转录卡片](evidence/deep-dub-parakeet-source-20261019.jpg)。

## 制品、保护与下一步

制品 `20261018235052-f795404b` 已重新构建并经 Compose 重建，平台镜像 `sha256:6d989a955b53bb9ca08e8b33a18ab07e6c8cd9b73a716bfe3960709d861d88bc`。六份后端/API/CLI 文件与冻结安装目录一致，6 项真实平台检查通过（3.16 秒），迁移前五库备份隔离恢复通过。

原 run 对象、原审阅队列摘要、默认 Nemotron 证据 ID 保持，正式分段仍为空；完整待审父子检查点、464 条线程全值、27 个受保护容器 ID 保持。没有新 GHCR 发布/拉取、IDE F5 或真实云库验收。

下一步是利用原生时间证据将差异定位到原音并可靠裁决，不能自动采用多数票或官方稿覆盖原音。正文门禁未解除；可信正式分段/角色/字幕时钟、正式整片生产与独立全质量、完整墙钟 RTF≤0.5、真实 B 站发布回执仍未完成。公开记录见 [本轮证据](evidence/deep-dub-parakeet-20261019.json)。
