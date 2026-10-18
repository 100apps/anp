# deep_dub 词级时序测量与详情页验收

本轮在既有 shadcn/ui 任务详情页的声音版本中增加真实词对齐、PCM 能量边界和指定落点测量。输入、模型、译稿和 WAV 摘要绑定到不可变阶段证据；测量不会保存落点、选版、时间线或质量审核。

## 已运行验证

- 37 项工具测试通过，包含越界、倒序、零长、重叠、空词、PCM 截断、取消及原生子进程退出。
- 真实新 WAV → 独立 Qwen3-ASR → Qwen3-ForcedAligner F16 链路：1 项集成测试通过，57.08 秒。该时长不是全片 RTF；输入是既有原片新截 26 秒及人工短译稿，不能视作完整翻译或质量验收。
- 默认本地 CLI、HTTP CLI 和 API 重放同一阶段回执；只读成员可读证据、不能执行，匿名及外工作空间被拒绝。九项代理分数绑定唯一证据，未创建审核、选版或时间线；项目版本未被分析操作修改。
- 浏览器在新生成声音版本点击“测量词级时序”→确认→显示结果；取消验收运行后执行入口禁用、证据仍可读取。Langfuse 九项分数、user 和 session 实际读回一致。
- TypeScript 与 Vite 生产构建通过；存在主应用分包体积提示，未据此宣称加载性能通过。

## 页面交互与结果含义

声音版本绑定自己的译稿和实际波形，测量紧邻版本，避免把其他音频的结果误用到当前候选。默认显示摘要，详细词表按需展开，每次显示最多新增 40 词。

真实样例有 22 个词，词时间戳结构无异常、给定文本覆盖齐全。WAV 为 6.664 秒，原声窗口为 24.520 秒；能量起点差 +0.260 秒、末端差 −17.930 秒。给定短中文没有覆盖完整原文，故不能把词覆盖齐全或字符差异为零当作内容通过。能量阈值是 10ms RMS 技术代理，不等于语音活动检测。

界面明确展示相对原声窗口的正负方向，词表从此 WAV 开头计时；转换为全片时间需加分析落点。没有裁剪或修复模型的越界、倒序、零长或重叠输出；异常会保留并显示。听感、语义、跨段碰撞与时序可接受性保持未测。

## 业务接入

- 服务：`apps/deep_dub/services/audio_timing.py`；节点：`nodes/transcribe.py`、`nodes/alignment.py`。
- 写入：`POST /api/apps/deep_dub/runs/{run_id}/align-audio`，支持幂等键、`audio_attempt_id` 和可选 `playback_start_ms`。
- 读取：`GET /api/apps/deep_dub/projects/{project_id}/audio/{audio_id}/timing`，返回当前音频最近一次成功的冻结证据。
- 本地 CLI：`uv run anp deep_dub runs align-audio RUN_ID AUDIO_ID --request-key KEY`；HTTP 加 `--transport http`。两个入口复用同一业务服务及权限。
- 本机配置：`[deep_dub].aligner_config` 指向私有 JSON，格式见 `apps/deep_dub/aligner.local.example.json`。独立进程占用物理 GPU 文件锁、既有路由锁和数据库租约；实际确认退出后才释放。

## 中断与尚未关闭的问题

第一轮真实对齐时 Docker 引擎停止、平台失联，不能算通过。三个受控子进程退出码为 0、0、1，日志已完整收尾；恢复平台十一组件后，使用这些证据回收唯一过期租约，并取消原验收运行。该回收属于管理员核对，不冒充正常工作者释放。随后另起全新运行通过上述链路。

恢复后的 Agent 校验发现：420 条线程 ID 均在，403 条 values 摘要与上一轮相同；另 17 条近期测试线程的元数据和运行记录仍在，但 history 为空、values 为空对象。最早保护的 284 条状态及 27 个受保护容器 ID 一致。**全部 420 条状态保留不通过**；根因和恢复尚待调查，未重跑或补写来冒充原始状态。现状检查点与日志已保存到本机 `.local/backups/agent-state-anomaly-20261018T1554Z/`。本轮没有修改平台运行器或重新发布平台镜像。

完整源文生产、语义/声音/字幕/碰撞质量、整片 RTF≤0.5 与 B 站回执仍未完成。长期目标继续保持进行中。

## 证据

- [结构化回执](evidence/deep-dub-word-timing-20261018.json)
- [详情页整体画面](evidence/deep-dub-word-timing-browser-20261018.png)
- [词级测量展开画面](evidence/deep-dub-word-timing-details-20261018.png)
