# deep_dub 新来源获取与完整解码

状态：**来源获取阶段已实现并真实运行；完整生产图、译配与质量闭环尚未完成。**

## 已实现

- `nodes/source_fetch.py`：只接 HTTPS YouTube 单视频，严格拒绝播放列表、重复ID、凭据、端口、非官方主机及本地路径。下载URL从run冻结输入派生，客户端不能另传URL换源。
- `services/source_acquisition.py`：同一用户/RLS与CPU租约下下载，按摘要保存私有原片和白名单JSON证据，再登记不可变source并绑定run。同输入成功回执可重放；已绑定其他来源时拒绝替换。媒体存在、解码完成、内容审核、整片技术通过分别处理。
- `services/owned_tree.py`：隔离Python等待stdin闸门，先归入本次Windows Job Object再启动下载器，Node/FFmpeg等子孙继承约束。取消/超时终止本次树，确认全部退出后才记进程停止、释放租约；不终止外部进程。不使用管理员提权、PID通配清理或breakaway。
- API：`POST /api/apps/deep_dub/runs/{id}/acquire-source`，输入 `timeout_seconds`（60–7200，默认3600）及可选管理员已核验的 `device_id`；结果 `GET /api/apps/deep_dub/runs/{id}/source-acquisition`。
- CLI：`uv run anp deep_dub runs acquire-source <运行UUID>`；`uv run anp --transport http deep_dub runs acquire-source <运行UUID>`；读取为 `runs source-acquisition <运行UUID>`。默认本地模式不访问业务Web，仍使用相同平台身份、权限和存储。HTTP下载调用等待期覆盖配置阶段超时及提交余量。
- shadcn/ui工作台增加来源获取区域、确认/返回、错误反馈、不可变证据与停止回执；取消或已有来源时动作禁用，可继续只读查看。

本机配置为 `[deep_dub] source_fetch_config`，模板在 `apps/deep_dub/source-fetch.local.example.json`。执行器摘要、Firefox配置、UA、Node和FFmpeg路径由管理员核实；运行参数不允许网页用户指定命令或任意程序。配置保留在本机，不把会话、Cookie或签名媒体地址带入图状态、证据、公开浏览器配置或版本库。

下载使用最高可用视频和音轨 `bv*+ba/b`、排序 `res,fps`，无画质/码率上限，无损合并MKV。读取所选Firefox实时Cookie，不复制配置、不导出Cookie文件；不自动换账号、代理、指纹或降画质。限流/本人验证明确拒绝，未完成的功能不通过重试绕过。当前只支持Windows本机受控下载，尚未实现其他站点、Linux或远端下载执行器。

原片只在本次新建的UUID临时目录中暂存；完成上传或失败清理前核对绝对路径及父目录，最终原片放Docker私有对象存储。对外JSON只有白名单来源元数据和SHA/进程回执，原始info.json及工具日志不上传。工具失败只返回分类，不暴露可能包含签名URL的原始stderr。

## 已运行验证

1. URI限制、元数据音视频/ID/直播/播放列表拒绝、去除签名地址与Cookie字段、真实Windows树正常/失败/超时/取消，以及监听归属相关检查共19项通过，2.60秒。树取消保留旁边无关进程；这是夹具，不当作下载成功证据。
2. 全新来源验收运行，实际下载 `https://www.youtube.com/watch?v=QXXp1bHd6gI`。线上来源为 YaleCourses，ID与请求一致，解析选择`396+251`。FFprobe实际为640×360、AV1、30000/1001 fps、Opus 48kHz双声道，2586.328秒；58123938字节，SHA256 `205f7913dad672ed772a02ea1bdd3bb4d21c8390c7bac8d39b86e7d2a9577d37`。
3. 下载进程树累计13进程，结束剩余0；下载器阶段41.531秒。随后对新保存原片实际完整解码，解码退出0、结束及尾部覆盖证据保存。下载、API权限、私有读回、默认本地与HTTP CLI重放、完整解码整项验收94.37秒；包含命令展示等待等，不扣成所谓生产RTF。
4. 同输入三入口没有追加执行尝试或再次下载；viewer只读，匿名和跨空间拒绝。四项Langfuse分数已真实读回，user为原执行者、session为稳定run ID；内容准确性和字幕保持not_measured。没有创建源审核、分段、翻译、时间线或成片。
5. 新制品 `20261018181021-d5167865` 已构建并经Compose重建，平台健康，冻结业务实现摘要一致。TypeScript与Vite构建通过；持久化/平台/业务/既有来源结果读取及新来源读取共24项冻结生产回归42.15秒通过。真实下载使用工作树独立API夹具；生产回归读取其证据，没有声称在正式入口再次下载。
6. 正式8089工作台实际展示来源及停止回执；验收run取消收尾后保留证据、按钮禁用。原436条Agent最终值全部保留，结束438条；27个受保护容器ID不变。

证据：[来源获取与解码](evidence/deep-dub-source-acquisition-20261019.json)、[正式工作台截图](evidence/deep-dub-source-acquisition-browser-20261019.png)。私有原回执在 `.local/deep-dub-source-acquisition-final.json`；公开证据不含Firefox路径、会话或媒体签名地址。

## 未完成

本轮仍是acceptance运行。未声明整个run技术完成，也未开始正式整片译配计时。新来源的正文/角色确认、可靠分段、翻译和独立复核、音色/配音、背景、字幕/混音/成片门禁、整片RTF≤0.5及B站回执继续实施。已有词时间的越界与严格覆盖差异仍保留；不能把新下载成功当作这些问题已解决。

完整Agent生产图仍须整合各业务阶段、持久化等待/恢复与门禁；当前仍只有解码子图。新本机制品没有新GHCR发布/拉取证据；两种IDE F5未在本轮重复验收。

本轮使用 [video-download技能](C:/Users/guangfeng/.codex/skills/video-download/SKILL.md) 的本机下载约定。按Firefox158推导UA，没有读取实时UA；未发现所选配置显式代理覆盖且系统代理未启用，没有改变网络设置，不能因此宣称实际出口或浏览器指纹完全一致。未使用dub2回退，也没有遇到或验证登录恢复。
