$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
uv run python -m anp.dev.prepare
if ($LASTEXITCODE -ne 0) { throw '开发环境准备失败' }
