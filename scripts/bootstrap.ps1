$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖同步失败' }
pnpm install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw '前端依赖同步失败' }
uv run anp app sync
if ($LASTEXITCODE -ne 0) { throw '应用注册失败' }
uv run anp platform up
if ($LASTEXITCODE -ne 0) { throw '平台启动或初始化失败' }
Write-Host '初始化完成。登录信息：config/local-credentials.md；打开工作区后选择 ANP 联合调试并按 F5。'
