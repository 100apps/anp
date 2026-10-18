# deep_dub 来源准备编排

状态：**来源准备子图已在正式平台真实运行至待复核；两种CLI待审重放、工作台读取、完整检查点重启保留已验证。完整生产图、独立质量、译配/成片、整片RTF及B站回执仍未完成。**

## 业务边界

流程为：获取新原片 → 完整解码 → 整轨识别 → 完整词时间 → 可选官方参考 → 来源待复核。

- 图位于 `apps/deep_dub/flow/source_preparation.py`，业务位于 `services/source_jobs.py` 和已有阶段服务，Worker入口为 `api/source_worker.py`。平台只加载业务挂载目录，业务代码不进入平台镜像。
- 原解码图仍支持 `run_id + attempt_id`；新增来源准备分支使用 `run_id + source_preparation_id`，两种输入互斥。引擎只持有ID、轻量产物回执和复核摘要，不持有用户会话、模型路径或整轨文本。
- 来源准备授权存于业务主机ACL保护目录，绑定原用户、工作空间、run冻结输入、下载上下文、模型身份、原片尝试及参数。默认600秒、最大1200秒，且不超过原登录到期时间。每个Worker请求重新验证会话、角色、当前运行、输入及前置阶段；取消/撤权/过期拒绝，执行中到期等待实际停止后收尾。不后台自行延长授权。
- 稳定请求键决定准备流程ID，用同一键接续。成功阶段重放原回执，不因客户端重试重置排队、加载或失败记录；不能换键接管其他准备流程的原片。
- `015_source_preparation_wait.sql` 仅将有当前执行者冻结待审回执的run置为waiting_review，不改首次时间、冻结输入、源或technical_verified_at。平台通用协调器跳过本业务，避免把中断误写成完成/失败。平台tasks记录仍承载未完成业务，具体等待状态由deep_dub_runs与阶段回执表达。
- 复核节点持续interrupt。客户端的resume/approve只触发后端重查，不能产生原文、字幕、质量审核或整片成功。当前版本没有“批准后直接成片”分支。

## 本机与平台入口

默认直接本机执行：

```powershell
uv run anp deep_dub runs prepare-source 运行UUID --reference-url https://oyc.yale.edu/history/hist-119/lecture-1 --request-key 稳定请求键
uv run anp --transport http deep_dub runs prepare-source 运行UUID --reference-url https://oyc.yale.edu/history/hist-119/lecture-1 --request-key 稳定请求键
```

默认CLI在本机运行相同LangGraph节点，直接调用业务代码，不要求启动业务API、Worker或Agent；平台Auth、对象与数据库服务仍需可用。HTTP模式由API执行相同直接服务。已提交阶段存于业务数据库和私有对象存储，后续重放仍需同一用户、请求键、输入和模型。默认本机图本身不声称有跨进程Agent检查点；平台持久化图是以下显式入口。

```powershell
uv run anp deep_dub runs launch-source-preparation 运行UUID --reference-url https://oyc.yale.edu/history/hist-119/lecture-1 --request-key 稳定请求键
uv run anp deep_dub runs source-preparation 运行UUID 准备流程UUID
```

平台入口要求Agent与业务Worker。线程ID由run与流程ID稳定派生；服务器持有引擎凭据，浏览器和图输入不携带它。工作台提供平台启动、进度、错误后重新授权接续、线程标识和待复核提示。无权限、已绑定其他来源、运行取消或待审时禁用新启动。

HTTP契约：`POST /runs/{id}/prepare-source` 为直接准备，`POST /runs/{id}/source-preparation` 为平台编排；`POST /runs/{id}/source-preparation-job` 只授权不启动；`GET /runs/{id}/source-preparation` 读取平台流程，带流程ID的GET读取指定线程。均位于 `/api/apps/deep_dub`，沿用调用者身份与工作空间。

## 已验证与修复记录

- 24项工具检查通过（3.39秒），覆盖授权MAC/范围/到期、撤权拒绝、真实LangGraph子图检查点、本机无HTTP/无检查点执行、客户端approve不能通过、IPv4/IPv6归属和真实Windows进程树清理。
- 待审RPC真实数据库契约1项通过（2.48秒）：JSONB输出、冻结当前尝试、viewer拒绝、待审禁止新阶段、取消后拒绝。使用2秒合成视频，仅证明状态/权限契约。
- 三次完整平台验收未通过的原日志和运行均保留：首次业务run_id落在原生Agent保留元数据范围，改用business_run_id；第二次待审RPC误用cardinality(jsonb)，改为jsonb_array_length并补真实数据库检查；第三次来源准备已正确中断，但观察器把原生调用success误当成图完成，已改按线程和检查点中断标记判断，同时保留native_run_status。没有把失败回填为通过或修改取消运行。
- 子图中断时阶段结果留在子图检查点，父图values尚未合并；读取启用subgraphs并读取真实子状态，不伪造父图进度。
- 工具检查曾有固定子孙数断言失败：系统Job实际回执为3而非2，所有活动进程均为0。检查改为至少包含根与工具且全部已停止，不假定固定进程数；实际计数仍保留。

## 正式平台结果

- 新获取完整Yale原片，真实完整解码、44块ASR、44块强制对齐、官方参考比对依次完成；5654词，仍有1个异常时间块、17个严格覆盖差异块、160ms跨块重叠和152处参考差异。未建立原文分段、字幕、质量通过或成片。
- 六个阶段中前五个成功，复核阶段waiting_review；业务run也为waiting_review、technical_verified_at为空。原生调用为success，但原生线程与检查点为interrupted；应用按中断证据显示等待，保留native_run_status，不混淆调用结束和业务完成。
- 完整流程验收在272.38秒时因脚本SELECT敏感租约列而失败；实际阶段、approve恢复门禁及CLI重放此前已完成。保持这同一运行及检查点，不重跑来源/模型，修正为公开列后追加恢复验收18.15秒通过。原测试仍记失败，不改写成“整条测试通过”。五份租约后续以公开列核实全部released且operation_stopped=true，没有扩大数据库权限。
- 默认本地CLI在业务API、Worker与Agent地址均指向不可达端点时直接重放待审业务；HTTP CLI调用同一直接服务重放。平台图的真实新执行走正式Agent/Worker；本轮未额外用默认CLI从下载开始再跑一次全片，不把重放当作另一条新生产。
- 客户端approve再次提交后仍停回待审，未增加阶段尝试或模型作业。21项Langfuse评分实际读回，user为原执行者，session为稳定业务run；未测项维持not_measured。
- 实际工作流运行制品为`20261018184555-1e80b0f1`。补齐取消状态显示及查询刷新后的最终UI制品`20261018185748-42a8b892`经Compose重建，九个Python/迁移文件与工作流运行制品、当前实现摘要完全一致。父图与子图完整检查点重建前后逐项相同，原生线程仍interrupted，无自动续跑；新页面实读五个完成阶段、待独立复核及禁用启动。
- 29项联合回归70.23秒通过，包含旧解码委派、授权到期、撤权清理、持久化、平台、业务和来源结果/图读取。原438条Agent最终值全部保留，结束444条；重建前442条也逐项保持；27个受保护容器ID不变。

证据：[来源准备与持久化](evidence/deep-dub-source-preparation-20261019.json)、[正式工作台](evidence/deep-dub-source-preparation-browser-20261019.png)。完整私有测量及父子检查点保存在`.local/deep-dub-source-preparation-final.json`及对应before/after-restart文件，没有把整轨文本、会话或原始媒体地址提交到版本库。

该验收运行保留在waiting_review，全部设备已释放，供继续查看证据；这不是暂停或完成长期交付目标。完整翻译/声音/字幕/成片图、独立审核、V100实际执行、整片RTF≤0.5及B站回执继续实施。新本机制品没有新GHCR发布/拉取证据；本轮未重新做两种IDE F5验收。
