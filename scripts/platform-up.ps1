$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
uv run anp platform up
if ($LASTEXITCODE -ne 0) { throw '平台启动失败' }
