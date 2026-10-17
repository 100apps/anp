$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
foreach ($port in 8180,8281) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw '请先在 IDE 停止 ANP 联调。验收需要证明没有业务 Web 服务时默认 CLI 仍可运行。'
    }
}
uv run anp platform up --admin --llm
if ($LASTEXITCODE -ne 0) {throw '验收所需的可选服务启动失败'}
uv run anp platform check
if ($LASTEXITCODE -ne 0) {throw '平台连接失败'}
uv run pytest tests -q --tb=short
if ($LASTEXITCODE -ne 0) {throw '集成验收失败'}
pnpm --dir console build
if ($LASTEXITCODE -ne 0) {throw '前端生产构建失败'}
