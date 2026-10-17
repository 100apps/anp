param(
    [ValidateSet('up','stop','status')][string]$Action='up',
    [string[]]$Profiles=@('local'),
    [string]$EnvFile='config/secrets.local.env'
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$compose=@('compose','--env-file',$EnvFile,'-f','deploy/storage/compose.yaml')
foreach($profile in $Profiles){$compose+=@('--profile',$profile)}
if($Action -eq 'up' -and ($Profiles -contains 'local' -or $Profiles -contains 'rustfs')) {
    # 以 Compose 的解析结果为准，兼容 EnvFile 与进程环境的正常优先级；不输出私有配置。
    $resolvedJson=& docker @compose config --format json
    if($LASTEXITCODE -ne 0){throw '解析存储配置失败'}
    $resolved=$resolvedJson | ConvertFrom-Json
    $rustfsDataVolume=$resolved.volumes.rustfs_data.name
    $rustfsLogsVolume=$resolved.volumes.rustfs_logs.name
    foreach($volume in @($rustfsDataVolume,$rustfsLogsVolume)) {
        if($volume -notmatch '^[a-zA-Z0-9][a-zA-Z0-9_-]+$'){throw 'RustFS 数据卷名称格式无效'}
    }
    # 初始化仍复用 RustFS 官方镜像；没有第五个存储镜像或常驻初始化容器。
    foreach($volume in @($rustfsDataVolume,$rustfsLogsVolume)) {
        & docker volume create $volume | Out-Null
        if($LASTEXITCODE -ne 0){throw '创建 RustFS 数据卷失败'}
    }
    & docker run --rm --user 0:0 --entrypoint /bin/sh `
        --mount "type=volume,source=${rustfsDataVolume},target=/data" `
        --mount "type=volume,source=${rustfsLogsVolume},target=/logs" `
        'rustfs/rustfs@sha256:7e948dad4748bda48f8985fda7195f8cf2e4259427011f6120acc2c9acf51587' `
        -ec 'chown 10001:10001 /data /logs'
    if($LASTEXITCODE -ne 0){throw '初始化 RustFS 目录权限失败'}
}
switch($Action){
    'up' {$compose+=@('up','-d','--wait','--wait-timeout','180')}
    'stop' {$compose+=@('stop')}
    'status' {$compose+=@('ps')}
}
& docker @compose
if($LASTEXITCODE -ne 0){throw '存储层操作失败，请检查日志；没有删除数据卷'}
