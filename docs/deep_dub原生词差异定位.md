# deep_dub 原生词差异定位

状态：已有 Parakeet 完整盲转录的差异现可定位到原始 PCM 的词候选窗口，API、默认本地/HTTP CLI、Web 实际播放与自动暂停已验证。**这是查听辅助，不是正文、词边界、字幕或听感审核通过；没有重新运行模型。**

## 兼容范围与接口

原默认队列保持 213 项，原加入 Nemotron 的队列保持 460 项，其摘要均与历史记录一致。新增显式 `include_parakeet=true`；与 `include_secondary=true` 同时使用时是 600 项，保留原项 ID、Nemotron 与 Parakeet 各自的证据 ID/完整证明摘要。

```powershell
uv run anp deep_dub projects source-review-queue <project-id> <source-id> --include-secondary --include-parakeet
uv run anp --transport http --endpoint http://127.0.0.1:8181 deep_dub projects source-review-queue <project-id> <source-id> --include-secondary --include-parakeet
```

HTTP 为原 `review-queue` GET 增加 `include_parakeet`，新增筛选 `kind=parakeet_difference`。未显式加入对应证据时拒绝该筛选。分页的 `expected_hash` 绑定完整证据快照，不能将旧队列页与新输入混用。每次复用用户/工作空间权限，不缓存授权结论。

## 原生候选时间的语义

新业务节点 `source_native_review.py` 先核对同一 source、原 ASR、原始 PCM、官方参考版本和完整 Parakeet 证明，然后依据 Parakeet token 索引关联其原生词样本位置。它不使用候选文本强制对齐，也不证明任一转录正确。

- 原生词字符覆盖、原始样本范围及词时间单调性继续严格检查。
- 字符串相同仍可能存在不同分词边界；token 序列不能严格一一对应时，该块定位退回原来的两侧块并集，不强行配准。
- 插入/删除的空侧保留相邻词锚点，全片首尾也受实际 PCM 样本范围限制。
- 同一原生词被分为多个文本 token 时允许共用同一个原始词区间，不因小数/连字符拆分而伪造多个词时间。
- 主试听窗口在候选两侧补 2 秒，原块粗定位范围同时保留。原始词时间不被裁改或写成通过。
- 新项类型是 `parakeet_difference`，与旧 Nemotron 项明确分开；模型一致、多数票或数字字面形式相似都不签发质量结论。

## 已运行验证

| 检查 | 实际结果 |
|---|---|
| 节点和旧队列回归 | 17 项通过（0.10 秒），涵盖数值、插入/删除、分词边界退回、输入/PCM/模型混用拒绝、旧项/绑定保留及显式参数门禁 |
| 类型检查 | TypeScript 通过 |
| 完整来源 API/两 CLI | 1 项真实专项通过（71.95 秒）；600 项分页 ID 唯一、来源窗口有界，缺身份 401、跨空间 404、缺显式选项 422、旧快照 409 |
| 原队列保护 | 213 项和 460 项的原摘要保持 |
| 新项 | 140 项 Parakeet 差异；总队列 64 项涉及数字，分类不代表真实错误数 |
| 可定位范围 | 137 项使用原生词候选，3 项退回粗定位；可定位项窗口中位数 64,000 ms → 4,480 ms |
| 数据保护 | 阶段、审核、分段集、译稿、运行对象及 GPU journal 摘要在接口/CLI 检查前后不变 |
| Web | 显式加入两套证据并筛选 140 项，展示双方原文和未判定状态；可见实际粗定位退回与原生词标签 |
| 原音播放 | 实际媒体 readyState=4、无错误；在 653.735701 秒观察到播放，最后在 654.880 秒自动暂停，窗口为 648.240–654.880 秒 |

`fifteenth` 与 `15 th` 等显示的是字面差异候选，不代表存在数值事实错误；诸如 `1866` 与其他识别形式也仍待原音裁决。不能根据本轮定位数量回填首次成功率或填写正文通过。截图见 [原生词差异与播放窗口](evidence/deep-dub-native-review-20261019.jpg)。

## 制品和完整目标

制品 `20261019000114-6855b2c5` 已重新构建并经 Compose 重建，镜像 `sha256:9f799d645d4152db2b9fe1a4e7f506f39b9f2baec9c184aea5575a4498369ed8`；四份后端节点/服务/API/CLI 文件与冻结安装目录逐字节一致。6 项真实平台检查通过（3.47 秒），五库备份隔离恢复通过。原 464 条线程全值、待审父子完整检查点、27 个受保护容器 ID 保持。

本轮没有新模型推理、正式分段、来源审核放行或生产输出；也没有 GHCR 发布/拉取、IDE F5 或真实云库复验。下一步仍需可靠裁决正文，再做可信分段/人物/字幕、正式整片生产与独立全质量、完整墙钟 RTF≤0.5 和真实 B 站回执。公开摘要见 [验收记录](evidence/deep-dub-native-review-20261019.json)。
