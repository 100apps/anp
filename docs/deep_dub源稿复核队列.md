# deep_dub 源稿复核队列

## 目的与边界

来源准备子图真实停在待独立复核。原页面只给出参考差异附近的音轨块号，无法直接查听，固定分块接缝和词时间异常也没有统一入口。本次把这几种输入组成可定位、可分页、可回溯的复核队列，继续为源稿审核、角色确认和正式分段准备证据。

队列不是审核结论，不更新来源文本、原始词时间、cue、语义段或运行状态，也不记录人工已听或独立模型通过。当前等待中的运行仍保留原最早起点，不能用本功能的计算时间冒充整片 RTF。

## 实现

- `apps/deep_dub/nodes/source_review.py` 从已校验的完整 ASR、词时间和官方参考重算队列。保留数字差异、严格字符覆盖差异、异常原始词时间以及每一个音轨块接缝。
- `apps/deep_dub/services/source_review.py` 每次核对当前用户读取权限、来源项目归属和三个证据的 ASR/PCM 版本关系。不同识别版本不能混用；不缓存授权结论。
- 只有整块 ASR 词序列与对齐器词序列完全一致且相关词时间不倒置时，使用词时间候选定位；否则明确退回整个音轨块。试听窗前后留两秒，并限定在真实 PCM 范围。这个限制不改变测量证据里的越界值。
- 队列摘要绑定来源、PCM、三个证据 ID、规则版本及完整内容。后续分页可传 `expected_hash`；证据变化返回 409，避免把旧页审核套到新输入。筛选不改变问题稳定 ID。
- 默认 CLI 直接调用同一业务服务，无需业务 Web。HTTP CLI 和页面调用同一 API，沿用工作空间 RLS。
- shadcn/ui 页面按需展开队列，支持数字、差异、异常和接缝分类、分页与问题切换。参考/ASR 并排显示且均不自动判正确；上下文和原始异常按需展开。只挂载当前问题的私有 PCM 播放器，切题即停止旧音频，监听限定在所示窗口。

## 命令与接口

```powershell
uv run anp deep_dub projects source-review-queue PROJECT_ID SOURCE_ID
uv run anp --transport http deep_dub projects source-review-queue PROJECT_ID SOURCE_ID
uv run anp deep_dub projects source-review-queue PROJECT_ID SOURCE_ID --kind numbers
uv run anp deep_dub projects source-review-queue PROJECT_ID SOURCE_ID --offset 30 --limit 30 --expected-hash PACKET_SHA256
```

```text
GET /api/apps/deep_dub/projects/{project_id}/sources/{source_id}/review-queue
    ?offset=0&limit=30&kind=all&expected_hash=...
```

`kind` 支持 `all`、`numbers`、`reference_difference`、`invalid_word_time`、`word_coverage`、`chunk_boundary`、`empty_asr`。响应含完整队列总量、当前筛选数量、分类计数、来源证据绑定、当前页及 `has_more`。分类可重叠，不相加作为质量通过率。

## 已运行验证

- 32 项针对性工具检查通过，包括数字/否定差异保留、越界原值保留、数字词不一致降为粗定位、空识别与全接缝覆盖、版本变化拒绝、分页及逐次撤权；前端 TypeScript 检查通过。
- 当前整片私有证据离线重算：213 条定位项，包括 152 处参考差异、1 个词时间异常、17 个严格覆盖差异及 43 个接缝。其中 20 处数字差异；参考差异中 86 项可用词候选定位，66 项退回块定位。未改稿或回填质量。

## 正式接口与持久化验证

第一份冻结制品 `20261018192406-f4763e0e` 已经 Compose 重建并实际启动：

- 真实整片 API 与两种 CLI 的队列响应完全一致，默认本地 CLI 的业务端点明确设为不可用地址；读取不启动模型或下载原片。
- 213 项分页无重复 ID，数字筛选为 20 项；未知分类、非法分页、旧输入摘要、匿名和跨空间读取分别被拒绝。
- 当前用户短期 HttpOnly 播放会话读到本来源 PCM，Range 返回 206、文件头为 RIFF。
- 原阶段尝试、审核、分段、译文与运行记录在验证前后完全相同，运行保持 `waiting_review`。真实专项检查 1 项 16.89 秒通过；平台/业务/Agent 持久化/来源读取等联合回归 30 项 84.72 秒通过。
- 原 444 条线程的全部最终值不变；回归产生 2 条测试线程，结束 446 条。27 个受保护容器 ID 不变。待审运行的完整父/子检查点 JSON 与之前封存对象相同，未自动恢复图执行。
- 正式工作台实际打开 213 项队列；原音 `readyState=4`、总长 `2586.30825s`、无媒体错误，初始按异常项定位到 `1018s`，键盘播放后时间实际前进，随后暂停。没有把这次播放写成内容或听感通过。

代码复查补充播放器卸载时显式暂停；最终制品 `20261018192716-766f54f1` 已重新构建、Compose 重建并健康启动，十一组件均 ready：

- 最终冻结 API/两种 CLI/权限/媒体专项重新运行，1 项 18.31 秒通过。32 项工具和 30 项联合回归分别发生在前述首次制品；两个制品的队列 Python 代码与当前源文件逐字节一致，最后补丁只调整前端卸载清理，不把旧回归称为补丁后重跑。
- 实际播放异常项后切到数字问题，保留旧媒体对象只读检查：`isConnected=false`、`paused=true`，原时间 `1024.212625s`；新媒体暂停并定位 `765.440s`，无叠播。
- 新媒体实际播放至 `770.000s` 后自动暂停，`readyState=4`、无错误。这里只验证窗口播放行为，没有人耳质量判定。
- 数字分类显示 20 项，下一页显示 `11–20 / 20`；390px 窄屏实际 `document.scrollWidth=390`。
- 最终切换前后 446 条线程全部最终值一致，原 444 条全部保留；27 个受保护容器 ID 保持，完整父/子检查点仍一致，未自动继续生产。

公开证据：[接口、制品与保护检查](evidence/deep-dub-source-review-20261019.json)、[正式工作台截图](evidence/deep-dub-source-review-browser-20261019.png)。这是本机 Make 构建制品，包含工作树既有未提交核心更改；没有新 GHCR 发布或拉取，也没有在本轮重新实测两种 IDE F5。

## 尚未完成

源稿独立听核、说话人确认、语义分段、字幕时钟映射和待审门禁放行仍未完成。完整生产、真实远端资源利用、整片质量/RTF≤0.5 与 B 站回执继续按总验收清单推进。
