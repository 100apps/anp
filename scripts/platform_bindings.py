"""将统一存储 URL 绑定到平台组件；不在日志中输出展开后的私有值。"""
import re
from urllib.parse import quote,unquote,urlsplit,urlunsplit,parse_qs


def expand(value,secrets):
    if isinstance(value,str):
        def substitute(match):
            if match[1] not in secrets:raise ValueError('缺少配置变量：'+match[1])
            return secrets[match[1]]
        return re.sub(r'\$\{([A-Z0-9_]+)\}',substitute,value)
    if isinstance(value,list):return [expand(item,secrets) for item in value]
    if isinstance(value,dict):return {key:expand(item,secrets) for key,item in value.items()}
    return value


def address(url):
    parsed=urlsplit(url)
    if not parsed.hostname:raise ValueError('资源 URL 必须包含主机地址')
    host='['+parsed.hostname+']' if ':' in parsed.hostname else parsed.hostname
    return host+(':'+str(parsed.port) if parsed.port else '')


def storage_management(resources):
    """宿主探测与浏览器管理地址；云存储的控制台和健康接口需要显式提供。"""
    local='rustfs' in resources.get('local',[])
    endpoint=resources.get('s3_host_url','http://127.0.0.1:9000')
    console=resources.get('s3_console_url')
    health=resources.get('s3_health_url')
    if local:
        parsed=urlsplit(endpoint)
        if not parsed.hostname:raise ValueError('对象存储 URL 缺少主机地址')
        host='['+parsed.hostname+']' if ':' in parsed.hostname else parsed.hostname
        console=console or urlunsplit((parsed.scheme,
            host+':'+str(resources.get('s3_console_port',(parsed.port or 9000)+1)),'/rustfs/console/','',''))
        health=health or endpoint.rstrip('/')+'/health'
    for url in [endpoint,console,health]:
        if not url:continue
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('对象存储管理地址必须是无内嵌凭据的 HTTP(S) URL')
        if parsed.port is not None and not 1<=parsed.port<=65535:
            raise ValueError('对象存储管理地址端口必须为 1–65535')
    return {'endpoint':endpoint,'console_url':console or '', 'health_url':health or '',
        'kind':'rustfs' if local else 's3'}


def relocate(original,base):
    old,new=urlsplit(original),urlsplit(base)
    credentials=quote(unquote(old.username or ''),safe='')+':'+quote(unquote(old.password or ''),safe='')
    return urlunsplit((new.scheme,credentials+'@'+address(base),old.path,new.query,''))


def connection(config,url):
    parsed=urlsplit(url)
    config.update(host=parsed.hostname,port=parsed.port or (443 if parsed.scheme=='https' else {'postgres':5432,'redis':6379,'clickhouse':8123}[config['db_type']]),
        username=unquote(parsed.username or ''),password=unquote(parsed.password or ''),
        database=parsed.path.lstrip('/') or config.get('database'),url_params=parsed.query or None)
    sslmode=parse_qs(parsed.query).get('sslmode',['disable'])[0]
    config['ssl']=parsed.scheme in ('https','rediss') or sslmode not in ('disable','allow')


def bind(configuration,document,secrets,target='container'):
    document=expand(document,secrets)
    resources=document.get('storage',{})
    services=configuration['services']
    storage=configuration['storage']
    def selected(name):return resources.get(name+'_'+target+'_url')
    pg=selected('postgres')
    overrides=document.get('postgres',{}).get('urls',{})
    if pg:
        if urlsplit(pg).scheme not in ('postgres','postgresql'):raise ValueError('PostgreSQL URL 协议无效')
        storage['postgres_url']=pg
        parsed=urlsplit(pg)
        for name,spec in services.items():
            for key in ['DATABASE_URL','GOTRUE_DB_DATABASE_URL','PGRST_DB_URI']:
                if key in spec['environment']:
                    alias='langfuse' if name.startswith('langfuse-') else name
                    spec['environment'][key]=overrides.get(alias) or relocate(spec['environment'][key],pg)
        services['meta']['environment']['PG_META_DB_URL']=pg
        services['meta']['environment']['PG_META_DB_SSL_MODE']=parse_qs(parsed.query).get('sslmode',['disable'])[0]
        services['meta']['environment'].update(PG_META_DB_HOST=parsed.hostname,PG_META_DB_PORT=str(parsed.port or 5432),
            PG_META_DB_USER=unquote(parsed.username or ''),PG_META_DB_PASSWORD=unquote(parsed.password or ''),
            PG_META_DB_NAME=parsed.path.lstrip('/') or 'postgres')
        services['studio']['environment'].update(POSTGRES_HOST=parsed.hostname,POSTGRES_PORT=str(parsed.port or 5432),
            POSTGRES_PASSWORD=unquote(parsed.password or ''),POSTGRES_DB=parsed.path.lstrip('/') or 'postgres')
        for config in configuration['dbx_connections']:
            if config['db_type']!='postgres':continue
            alias={'postgres':'postgres','agent_local':'agent','langfuse':'langfuse','litellm':'litellm'}[config['database']]
            original='postgresql://'+quote(config['username'],safe='')+':'+quote(config['password'],safe='')+'@postgres/'+config['database']
            connection(config,pg if alias=='postgres' else overrides.get(alias) or relocate(original,pg))
    redis=selected('redis')
    if redis:
        if urlsplit(redis).scheme not in ('redis','rediss'):raise ValueError('Redis URL 协议无效')
        parsed=urlsplit(redis)
        for name in ['langfuse-web','langfuse-worker']:
            services[name]['environment'].update(REDIS_CONNECTION_STRING=redis,REDIS_HOST=parsed.hostname,
                REDIS_PORT=str(parsed.port or 6379),REDIS_AUTH=unquote(parsed.password or ''),
                REDIS_TLS_ENABLED=str(parsed.scheme=='rediss').lower())
        for config in configuration['dbx_connections']:
            if config['db_type']=='redis':connection(config,redis)
    clickhouse=selected('clickhouse')
    if clickhouse:
        parsed=urlsplit(clickhouse)
        if parsed.scheme not in ('http','https'):raise ValueError('ClickHouse URL 必须是 HTTP(S) 接口')
        user=unquote(parsed.username or 'anp');password=unquote(parsed.password or secrets['CH_PASSWORD'])
        plain=urlunsplit((parsed.scheme,address(clickhouse),parsed.path,parsed.query,''))
        migration=resources.get('clickhouse_'+target+'_migration_url') or resources.get('clickhouse_migration_url')
        if not migration:raise ValueError('替换 ClickHouse 时需明确配置原生协议 clickhouse_migration_url')
        for name in ['langfuse-web','langfuse-worker']:
            services[name]['environment'].update(CLICKHOUSE_URL=plain,CLICKHOUSE_MIGRATION_URL=migration,
                CLICKHOUSE_USER=user,CLICKHOUSE_PASSWORD=password)
        authenticated=urlunsplit((parsed.scheme,quote(user,safe='')+':'+quote(password,safe='')+'@'+address(clickhouse),parsed.path,parsed.query,''))
        services['lens']['environment']['CLICKHOUSE_URL']=authenticated
        for config in configuration['dbx_connections']:
            if config['db_type']=='clickhouse':connection(config,authenticated)
    s3=selected('s3')
    if s3:
        if urlsplit(s3).scheme not in ('http','https'):raise ValueError('S3 URL 协议无效')
        storage.update(s3_url=s3,s3_access_key=resources.get('s3_access_key',storage['s3_access_key']),
            s3_secret_key=resources.get('s3_secret_key',storage['s3_secret_key']),s3_region=resources.get('s3_region','us-east-1'))
        services['storage']['environment'].update(GLOBAL_S3_ENDPOINT=s3,GLOBAL_S3_PROTOCOL=urlsplit(s3).scheme,
            AWS_ACCESS_KEY_ID=storage['s3_access_key'],AWS_SECRET_ACCESS_KEY=storage['s3_secret_key'],REGION=storage['s3_region'])
        for name in ['langfuse-web','langfuse-worker']:
            for kind in ['EVENT_UPLOAD','MEDIA_UPLOAD','BATCH_EXPORT']:
                for key,value in {'ENDPOINT':s3,'ACCESS_KEY_ID':storage['s3_access_key'],
                    'SECRET_ACCESS_KEY':storage['s3_secret_key'],'REGION':storage['s3_region']}.items():
                    services[name]['environment'][f'LANGFUSE_S3_{kind}_{key}']=value
    configuration['initialize_databases']=resources.get('initialize_databases',True)
    configuration['initialize_buckets']=resources.get('initialize_buckets',True)
    for name,spec in document.get('services',{}).items():
        if name not in services:raise ValueError('未知平台组件：'+name)
        services[name]['environment'].update(spec.get('environment',{}))
