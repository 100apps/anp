# 源码构建与持续集成

本轮为独立本地目录anp3，尚未配置GitHub remote，没有修改原anp仓库和流水线。保留的CI只执行锁定依赖、离线业务/代理/运行时测试与前端生产构建，真实数据库权限测试会明确跳过；不冒充线上验收。

组件生产构建定义在 `compose.build.yaml`，先 `python scripts/source_recipes.py`，再构建九个目标。上游多阶段配方包含锁定依赖、静态资产、生产裁剪；必要前缀/公开包源适配记录在 `docker/source-recipes.json`，没有修改submodule源码。统一镜像仅复制运行产物，保留ABI运行库隔离与LICENSE。手工构建完成后运行 `python scripts/lock_versions.py --require-images` 再 `make build`，将真实镜像身份写进统一镜像。

完整平台构建需要较大内存、磁盘和时间，默认GitHub免费runner不适合作为全栈构建与验收机器。部署前应在明确规格的runner构建、做漏洞扫描并按镜像digest发布。该工作区验收不依赖GitHub执行成功。
