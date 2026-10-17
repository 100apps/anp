param(
    [ValidateSet('up','stop','status')][string]$Action='up',
    [string[]]$Profiles=@('local'),
    [string]$EnvFile='config/secrets.local.env'
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$prefix=if($env:ANP_STORAGE_PREFIX){$env:ANP_STORAGE_PREFIX}else{'anp_v3'}
if($prefix -notmatch '^[a-zA-Z0-9_-]+$'){throw '存储卷前缀格式无效'}
if($Action -eq 'up' -and ($Profiles -contains 'local' -or $Profiles -contains 'rustfs')) {
    # 初始化仍复用 RustFS 官方镜像；没有第五个存储镜像或常驻初始化容器。
    foreach($suffix in @('rustfs_data','rustfs_logs')) {
        & docker volume create ($prefix+'_'+$suffix) | Out-Null
        if($LASTEXITCODE -ne 0){throw '创建 RustFS 数据卷失败'}
    }
    & docker run --rm --user 0:0 --entrypoint /bin/sh `
        --mount "type=volume,source=${prefix}_rustfs_data,target=/data" `
        --mount "type=volume,source=${prefix}_rustfs_logs,target=/logs" `
        'rustfs/rustfs@sha256:7e948dad4748bda48f8985fda7195f8cf2e4259427011f6120acc2c9acf51587' `
        -ec 'chown 10001:10001 /data /logs'
    if($LASTEXITCODE -ne 0){throw '初始化 RustFS 目录权限失败'}
}
$compose=@('compose','--env-file',$EnvFile,'-f','deploy/storage/compose.yaml')
foreach($profile in $Profiles){$compose+=@('--profile',$profile)}
switch($Action){
    'up' {$compose+=@('up','-d','--wait','--wait-timeout','180')}
    'stop' {$compose+=@('stop')}
    'status' {$compose+=@('ps')}
}
& docker @compose
if($LASTEXITCODE -ne 0){throw '存储层操作失败，请检查日志；没有删除数据卷'}
