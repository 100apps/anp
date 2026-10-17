$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
uv run anp platform down
if ($LASTEXITCODE -ne 0) { throw '平台停止失败' }
