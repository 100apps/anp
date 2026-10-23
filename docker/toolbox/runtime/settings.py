"""统一数据目录、服务专属身份及按资源分配的可审核参数。"""
import base64
import hashlib
import hmac
import json
import os
import platform
from pathlib import Path
import secrets
from urllib.parse import quote
from xml.sax.saxutils import escape

DATA = Path(os.getenv('ANP_DATA_DIR','/data'))
NAMES = ['postgres','redis','clickhouse','rustfs','dbx','langfuse-web','langfuse-worker','litellm',
         'supabase-studio','supabase-meta','supabase-auth','supabase-rest','supabase-storage','supabase-gateway','supabase-realtime']
UIDS = {name: number for number,name in enumerate(NAMES,20001)}
PG = '/nix/var/nix/profiles/default/bin/'
NODE = '/opt/anp/runtimes/node24/bin/node'
NODE22 = '/opt/anp/runtimes/node22/bin/node'
COMPAT = ''


def atomic(path,content,uid=0,mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(content,encoding='utf-8')
    temporary.chmod(mode)
    os.chown(temporary,uid,uid)
    temporary.replace(path)


def jwt(role,secret):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).rstrip(b'=')
    body=encode({'alg':'HS256','typ':'JWT'})+b'.'+encode({'role':role,'iss':'supabase','iat':0,'exp':2147483647})
    return (body+b'.'+base64.urlsafe_b64encode(hmac.new(secret.encode(),body,hashlib.sha256).digest()).rstrip(b'=')).decode()


def credentials():
    path=DATA/'config/credentials.json'
    if path.exists():
        values=json.loads(path.read_text())
        for key in ('POSTGRES_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','JWT_SECRET','CLICKHOUSE_PASSWORD'):
            if os.getenv(key) and os.environ[key]!=values[key]:
                raise ValueError(f'{key} 与已有持久化凭据不同，拒绝隐式覆盖；请执行显式凭据轮换')
        changed=False
        for key,size in (('REALTIME_DB_PASSWORD',32),('REALTIME_SECRET_KEY_BASE',48),('REALTIME_DB_ENC_KEY',8)):
            if key not in values:
                values[key]=secrets.token_hex(size); changed=True
        if 'REALTIME_DB_ENC_KEY_GCM' not in values:
            values['REALTIME_DB_ENC_KEY_GCM']=secrets.token_urlsafe(24); changed=True
        if changed: atomic(path,json.dumps(values,indent=2)+'\n')
        return values
    keys=('POSTGRES_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','JWT_SECRET','CLICKHOUSE_PASSWORD','LANGFUSE_DB_PASSWORD',
          'LITELLM_DB_PASSWORD','AUTH_DB_PASSWORD','REST_DB_PASSWORD','STORAGE_DB_PASSWORD',
          'LANGFUSE_SALT','LANGFUSE_ENCRYPTION_KEY','LANGFUSE_AUTH_SECRET','DBX_PASSWORD','LITELLM_MASTER_KEY')
    values={key:os.getenv(key) or secrets.token_hex(32) for key in keys}
    if not values['LITELLM_MASTER_KEY'].startswith('sk-'):
        values['LITELLM_MASTER_KEY']='sk-'+values['LITELLM_MASTER_KEY']
    values['S3_ACCESS_KEY']=os.getenv('S3_ACCESS_KEY','anpadmin')
    values['REALTIME_DB_PASSWORD']=secrets.token_hex(32)
    values['REALTIME_SECRET_KEY_BASE']=secrets.token_hex(48)
    values['REALTIME_DB_ENC_KEY']=secrets.token_urlsafe(12)
    values['REALTIME_DB_ENC_KEY_GCM']=secrets.token_urlsafe(24)
    values['ANON_KEY']=jwt('anon',values['JWT_SECRET'])
    values['SERVICE_KEY']=jwt('service_role',values['JWT_SECRET'])
    atomic(path,json.dumps(values,indent=2)+'\n')
    return values


def tuning():
    path=DATA/'config/settings.json'
    if not path.exists():
        atomic(path,json.dumps({'postgres_connections':100,'postgres_work_mem_mb':4,'log_max_mb':10,
                               'log_backups':3,'memory_mb':16384,'cpu_threads':8},indent=2)+'\n')
    values=json.loads(path.read_text())
    memory=int(os.getenv('ANP_MEMORY_MB',values['memory_mb']))
    limit=Path('/sys/fs/cgroup/memory.max')
    if limit.exists() and limit.read_text().strip()!='max':
        memory=min(memory,int(limit.read_text())//1048576)
    cpu=max(1,int(os.getenv('ANP_CPU_THREADS',values['cpu_threads'])))
    values.update(memory_mb=memory,cpu_threads=cpu,postgres_shared_mb=max(128,min(1024,memory//12)),
                  postgres_cache_mb=max(256,memory//3),clickhouse_mb=max(256,min(4096,memory//4)),
                  redis_mb=max(64,min(512,memory//32)),node_web_mb=max(256,min(1536,memory//10)),
                  node_worker_mb=max(256,min(1024,memory//16)),node_studio_mb=max(256,min(768,memory//24)))
    atomic(DATA/'config/effective/tuning.json',json.dumps(values,indent=2)+'\n',mode=0o644)
    return values


def prepare():
    DATA.mkdir(parents=True,exist_ok=True)
    for name in NAMES:
        for path in (DATA/name,DATA/'run'/name,DATA/'logs'/name):
            path.mkdir(parents=True,exist_ok=True)
            os.chown(path,UIDS[name],UIDS[name]); path.chmod(0o750)
    values=credentials(); limits=tuning()
    conf=DATA/'config/effective'
    # 配置目录可遍历，具体凭据/配置文件仅服务专属用户可读。
    (DATA/'config').chmod(0o755); conf.mkdir(exist_ok=True); conf.chmod(0o755)
    password=values['POSTGRES_PASSWORD']
    atomic(conf/'postgres.password',password+'\n',UIDS['postgres'])
    atomic(conf/'pg_hba.conf','local all all peer\nhost all all 0.0.0.0/0 scram-sha-256\nhost all all ::/0 scram-sha-256\n',UIDS['postgres'])
    atomic(conf/'postgresql.conf',f"""listen_addresses='0.0.0.0'
port=5432
hba_file='{conf}/pg_hba.conf'
unix_socket_directories='{DATA}/run/postgres'
max_connections={limits['postgres_connections']}
shared_buffers='{limits['postgres_shared_mb']}MB'
effective_cache_size='{limits['postgres_cache_mb']}MB'
work_mem='{limits['postgres_work_mem_mb']}MB'
maintenance_work_mem='256MB'
wal_level=logical
max_replication_slots=10
max_wal_senders=10
max_slot_wal_keep_size='1GB'
wal_compression=on
max_wal_size='2GB'
min_wal_size='256MB'
checkpoint_completion_target=0.9
max_worker_processes={limits['cpu_threads']}
max_parallel_workers={max(1,limits['cpu_threads']//2)}
max_parallel_workers_per_gather=2
shared_preload_libraries='pg_stat_statements'
password_encryption='scram-sha-256'
log_min_duration_statement=1000
log_checkpoints=on
log_connections=off
log_timezone='UTC'
timezone='UTC'
""",UIDS['postgres'])
    atomic(conf/'redis.conf',f"""bind 0.0.0.0
port 6379
protected-mode yes
requirepass {values['REDIS_PASSWORD']}
dir {DATA}/redis
appendonly yes
appendfsync everysec
maxmemory {limits['redis_mb']}mb
maxmemory-policy noeviction
timeout 0
tcp-keepalive 60
save 900 1
save 300 10
""",UIDS['redis'])
    atomic(conf/'clickhouse.xml',f"""<clickhouse>
<logger><level>warning</level><console>1</console></logger>
<listen_host>0.0.0.0</listen_host><http_port>8123</http_port><tcp_port>9000</tcp_port>
<path>{DATA}/clickhouse/</path><tmp_path>{DATA}/clickhouse/tmp/</tmp_path>
<user_files_path>{DATA}/clickhouse/user_files/</user_files_path>
<format_schema_path>{DATA}/clickhouse/format_schemas/</format_schema_path>
<access_control_path>{DATA}/clickhouse/access/</access_control_path>
<max_server_memory_usage>{limits['clickhouse_mb']*1048576}</max_server_memory_usage>
<max_connections>256</max_connections><keep_alive_timeout>30</keep_alive_timeout>
<background_pool_size>16</background_pool_size><background_schedule_pool_size>8</background_schedule_pool_size>
<background_message_broker_schedule_pool_size>2</background_message_broker_schedule_pool_size>
<background_distributed_schedule_pool_size>2</background_distributed_schedule_pool_size>
<merge_tree><number_of_free_entries_in_pool_to_execute_mutation>2</number_of_free_entries_in_pool_to_execute_mutation>
<number_of_free_entries_in_pool_to_execute_optimize_entire_partition>2</number_of_free_entries_in_pool_to_execute_optimize_entire_partition>
<number_of_free_entries_in_pool_to_lower_max_size_of_merge>2</number_of_free_entries_in_pool_to_lower_max_size_of_merge></merge_tree>
<mark_cache_size>134217728</mark_cache_size><uncompressed_cache_size>67108864</uncompressed_cache_size>
<profiles><default><max_threads>{min(4,limits['cpu_threads'])}</max_threads><max_memory_usage>1073741824</max_memory_usage>
<load_balancing>random</load_balancing><log_queries>0</log_queries></default></profiles>
<users><default><password>{escape(password)}</password><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></default>
<anp><password>{escape(values['CLICKHOUSE_PASSWORD'])}</password><networks><ip>::/0</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></anp></users>
<quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas>
<builtin_dictionaries_reload_interval>3600</builtin_dictionaries_reload_interval>
<max_concurrent_queries>32</max_concurrent_queries>
</clickhouse>
""",UIDS['clickhouse'])
    envoy='''static_resources:
  listeners:
    - name: supabase
      address: {socket_address: {address: 0.0.0.0, port_value: 8000}}
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                '@type': type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: supabase
                upgrade_configs: [{upgrade_type: websocket}]
                stream_idle_timeout: 0s
                route_config:
                  name: supabase
                  virtual_hosts:
                    - name: api
                      domains: ['*']
                      routes:
                        - match: {prefix: /realtime/v1/api/broadcast}
                          route: {cluster: realtime, prefix_rewrite: /api/broadcast, host_rewrite_literal: realtime-dev.localhost, timeout: 0s}
                        - match: {prefix: /realtime/v1/websocket}
                          route: {cluster: realtime, prefix_rewrite: /socket/websocket, host_rewrite_literal: realtime-dev.localhost, timeout: 0s}
                        - match: {prefix: /realtime/v1/longpoll}
                          route: {cluster: realtime, prefix_rewrite: /socket/longpoll, host_rewrite_literal: realtime-dev.localhost, timeout: 0s}
                        - match: {prefix: /auth/v1/}
                          route: {cluster: auth, prefix_rewrite: /}
                        - match: {prefix: /rest/v1/}
                          route: {cluster: rest, prefix_rewrite: /}
                        - match: {prefix: /storage/v1/}
                          route: {cluster: storage, prefix_rewrite: /, timeout: 300s}
                http_filters:
                  - name: envoy.filters.http.router
                    typed_config: {'@type': type.googleapis.com/envoy.extensions.http.router.v3.Router}
  clusters:
'''.replace('envoy.extensions.http.router','envoy.extensions.filters.http.router')
    for name,port in [('auth',9999),('rest',3300),('storage',5000),('realtime',4100)]:
        endpoints=json.dumps([{'lb_endpoints':[{'endpoint':{'address':{'socket_address':{'address':'127.0.0.1','port_value':port}}}}]}])
        envoy+=f'''    - name: {name}
      type: STATIC
      connect_timeout: 3s
      load_assignment:
        cluster_name: {name}
        endpoints: {endpoints}
'''
    atomic(conf/'envoy.yaml',envoy,UIDS['supabase-gateway'])
    atomic(conf/'litellm.yaml','model_list: []\ngeneral_settings:\n  master_key: os.environ/LITELLM_MASTER_KEY\n  database_connection_pool_limit: 5\n',UIDS['litellm'])
    # 操作员可维护此文件接入供应商。已存在时不覆盖。
    custom=DATA/'config/litellm.yaml'
    if not custom.exists():
        atomic(custom,(conf/'litellm.yaml').read_text(),UIDS['litellm'])
    return values,limits


def definitions(secret,limits):
    conf=DATA/'config/effective'
    def url(user,key,database='postgres'):
        return f'postgresql://{user}:{quote(secret[key],safe="")}@127.0.0.1:5432/{database}'
    engine = 'linux-arm64-openssl-3.0.x' if platform.machine() == 'aarch64' else 'debian-openssl-3.0.x'
    public=os.getenv('ANP_PUBLIC_URL','http://127.0.0.1:51760').rstrip('/')
    pgurl=url('postgres','POSTGRES_PASSWORD')
    common={'NODE_ENV':'production','TZ':'UTC','PATH':'/opt/anp/runtimes/node24/bin:/opt/anp/bin:/usr/bin:/bin',
            'LD_LIBRARY_PATH':COMPAT,'NODE_OPTIONS':'--max-old-space-size=512','HOSTNAME':'0.0.0.0'}
    lf={'DATABASE_URL':url('langfuse','LANGFUSE_DB_PASSWORD','langfuse')+'?connection_limit=5&pool_timeout=30',
        'DIRECT_URL':url('langfuse','LANGFUSE_DB_PASSWORD','langfuse'),
        'SALT':secret['LANGFUSE_SALT'],'ENCRYPTION_KEY':secret['LANGFUSE_ENCRYPTION_KEY'],
        'NEXTAUTH_SECRET':secret['LANGFUSE_AUTH_SECRET'],'NEXTAUTH_URL':public+'/langfuse/api/auth',
        'NEXT_PUBLIC_BASE_PATH':'/langfuse','TELEMETRY_ENABLED':'false','LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES':'false',
        'CLICKHOUSE_URL':'http://127.0.0.1:8123','CLICKHOUSE_MIGRATION_URL':'clickhouse://127.0.0.1:9000',
        'CLICKHOUSE_USER':'anp','CLICKHOUSE_PASSWORD':secret['CLICKHOUSE_PASSWORD'],
        'CLICKHOUSE_CLUSTER_ENABLED':'false','REDIS_HOST':'127.0.0.1','REDIS_PORT':'6379','REDIS_AUTH':secret['REDIS_PASSWORD'],
        'LANGFUSE_S3_EVENT_UPLOAD_BUCKET':'langfuse','LANGFUSE_S3_EVENT_UPLOAD_REGION':'us-east-1',
        'LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT':'http://127.0.0.1:9002','LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE':'true',
        'LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID':secret['S3_ACCESS_KEY'],'LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY':secret['S3_SECRET_KEY'],
        'LANGFUSE_S3_MEDIA_UPLOAD_BUCKET':'langfuse','LANGFUSE_S3_MEDIA_UPLOAD_REGION':'us-east-1',
        'LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT':os.getenv('ANP_S3_PUBLIC_URL','http://127.0.0.1:'+os.getenv('S3_API_PORT','59402')),
        'LANGFUSE_S3_MEDIA_UPLOAD_INTERNAL_ENDPOINT':'http://127.0.0.1:9002','LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE':'true',
        'LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID':secret['S3_ACCESS_KEY'],'LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY':secret['S3_SECRET_KEY'],
        'PRISMA_SCHEMA_ENGINE_BINARY':f'/opt/langfuse-tools/lib/node_modules/prisma/node_modules/@prisma/engines/schema-engine-{engine}',
        'PRISMA_QUERY_ENGINE_LIBRARY':f'/opt/langfuse-tools/lib/node_modules/prisma/node_modules/@prisma/engines/libquery_engine-{engine}.so.node'}
    studio={'STUDIO_PG_META_URL':'http://127.0.0.1:8080','POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':'5432',
            'POSTGRES_DB':'postgres','POSTGRES_PASSWORD':secret['POSTGRES_PASSWORD'],'POSTGRES_USER':'postgres',
            'POSTGRES_USER_READ_WRITE':'postgres','POSTGRES_USER_READ_ONLY':'anp_studio_reader',
            'PG_META_CRYPTO_KEY':hashlib.sha256((secret['JWT_SECRET']+':studio-pg-meta').encode()).hexdigest(),
            'SUPABASE_URL':'http://127.0.0.1:8000','SUPABASE_PUBLIC_URL':public+'/supabase',
            'SUPABASE_ANON_KEY':secret['ANON_KEY'],'SUPABASE_SERVICE_KEY':secret['SERVICE_KEY'],
            'AUTH_JWT_SECRET':secret['JWT_SECRET'],'NEXT_PUBLIC_BASE_PATH':'/supabase','ENABLED_FEATURES_LOGS_ALL':'false',
            'DEFAULT_PROJECT_NAME':'ANP 本机平台','DEFAULT_ORGANIZATION_NAME':'ANP','PORT':'3200',
            'NODE_OPTIONS':f"--max-old-space-size={limits['node_studio_mb']}"}
    result={}
    def add(name,command,port,env=None,deps=(),path=None,cwd='/opt/anp',timeout=300):
        result[name]={'name':name,'command':command,'port':port,'env':{**common,**(env or {})},
                      'deps':list(deps),'health':path,'cwd':cwd,'timeout':timeout,'uid':UIDS[name]}
    add('postgres',[PG+'postgres','-D',str(DATA/'postgres'),'-c','config_file='+str(conf/'postgresql.conf')],5432,
        {'PATH':PG+':/usr/bin:/bin','LD_LIBRARY_PATH':'','HOME':str(DATA/'postgres')})
    add('redis',['/opt/anp/bin/redis-server',str(conf/'redis.conf')],6379)
    add('clickhouse',['/opt/anp/bin/clickhouse','server','--config-file='+str(conf/'clickhouse.xml')],8123,path='/ping')
    add('rustfs',['/opt/anp/bin/rustfs',str(DATA/'rustfs')],9002,{'RUSTFS_ACCESS_KEY':secret['S3_ACCESS_KEY'],
        'RUSTFS_SECRET_KEY':secret['S3_SECRET_KEY'],'RUSTFS_ADDRESS':'0.0.0.0:9002','RUSTFS_CONSOLE_ADDRESS':'0.0.0.0:9003',
        'RUSTFS_CONSOLE_ENABLE':'true','RUSTFS_LOGS_DIR':str(DATA/'logs/rustfs'),'RUST_LOG':'warn','LD_LIBRARY_PATH':''})
    add('dbx',['/opt/anp/bin/dbx-web'],4224,{'DBX_PASSWORD':secret['DBX_PASSWORD'],'DBX_PUBLIC_BASE_PATH':'/dbx',
        'DBX_DATA_DIR':str(DATA/'dbx'),'DBX_BACKUP_ROOT':str(DATA/'dbx/backups'),
        'DBX_JAVA_BIN':'/usr/bin/java'},path='/dbx/')
    add('langfuse-web',['/bin/sh','web/entrypoint.sh',NODE,'web/server.js','--keepAliveTimeout','110000'],3100,
        {**lf,'PORT':'3100','PATH':'/opt/langfuse-tools/bin:'+common['PATH'],
         'NODE_OPTIONS':f"--max-old-space-size={limits['node_web_mb']}"},
        ('postgres','clickhouse','redis','rustfs'),'/langfuse/api/public/health','/opt/anp/services/langfuse-web',600)
    add('langfuse-worker',['/bin/sh','worker/entrypoint.sh',NODE,'worker/dist/index.js'],3130,
        {**lf,'PORT':'3130','NODE_OPTIONS':f"--max-old-space-size={limits['node_worker_mb']}"},
        ('langfuse-web',),'/api/health','/opt/anp/services/langfuse-worker',600)
    add('litellm',['/app/.venv/bin/litellm','--config',str(DATA/'config/litellm.yaml'),'--port','4000'],4000,
        {'DATABASE_URL':url('litellm','LITELLM_DB_PASSWORD','litellm'),'LITELLM_MASTER_KEY':secret['LITELLM_MASTER_KEY'],
         'LITELLM_LOCAL_MODEL_COST_MAP':'true','STORE_MODEL_IN_DB':'true','SERVER_ROOT_PATH':'/litellm',
         'PROXY_BASE_URL':public+'/litellm','PATH':'/app/.venv/bin:'+common['PATH'],
         'PRISMA_BINARY_CACHE_DIR':'/opt/prisma/binaries','PRISMA_CLI_PATH':'/opt/prisma/binaries/node_modules/.bin/prisma',
         'PRISMA_CLI_QUERY_ENGINE_TYPE':'binary','PRISMA_OFFLINE_MODE':'true',
         'HOME':str(DATA/'litellm'),'LITELLM_LOG':'WARNING'},('postgres',),'/health/readiness','/app')
    add('supabase-meta',[NODE22,'node_modules/@supabase/postgres-meta/dist/server/server.js'],8080,{'PG_META_DB_HOST':'127.0.0.1','PG_META_DB_PORT':'5432',
        'PG_META_DB_NAME':'postgres','PG_META_DB_USER':'postgres','PG_META_DB_PASSWORD':secret['POSTGRES_PASSWORD'],
        'CRYPTO_KEY':studio['PG_META_CRYPTO_KEY'],
        'PG_META_PORT':'8080','PORT':'8080'},('postgres',),'/health','/opt/anp/services/supabase-meta')
    add('supabase-auth',['/opt/anp/bin/supabase-auth'],9999,{'GOTRUE_API_HOST':'0.0.0.0','GOTRUE_API_PORT':'9999',
        'API_EXTERNAL_URL':public+'/supabase/auth/v1','GOTRUE_SITE_URL':public+'/workbench/',
        'GOTRUE_DB_DRIVER':'postgres','GOTRUE_DB_DATABASE_URL':url('supabase_auth_admin','AUTH_DB_PASSWORD'),
        'GOTRUE_JWT_SECRET':secret['JWT_SECRET'],'GOTRUE_JWT_AUD':'authenticated',
        'GOTRUE_JWT_ADMIN_ROLES':'service_role','GOTRUE_JWT_DEFAULT_GROUP_NAME':'authenticated',
        'GOTRUE_EXTERNAL_EMAIL_ENABLED':'true','GOTRUE_MAILER_AUTOCONFIRM':'true',
        'GOTRUE_DB_MIGRATIONS_PATH':'/opt/anp/services/supabase-auth/migrations'},('postgres',),'/health')
    add('supabase-rest',['/opt/anp/bin/postgrest'],3300,{'PGRST_DB_URI':url('authenticator','REST_DB_PASSWORD'),
        'PGRST_DB_SCHEMAS':'public','PGRST_DB_EXTRA_SEARCH_PATH':'public,extensions','PGRST_DB_ANON_ROLE':'anon',
        'PGRST_JWT_SECRET':secret['JWT_SECRET'],'PGRST_SERVER_PORT':'3300','PGRST_DB_POOL':'5',
        'PGRST_DB_MAX_ROWS':'1000'},('postgres',),'/')
    add('supabase-storage',[NODE,'dist/start/server.js'],5000,
        {'LD_LIBRARY_PATH':'',
         'SERVER_PORT':'5000','PORT':'5000','HOST':'0.0.0.0','ANON_KEY':secret['ANON_KEY'],
         'SERVICE_KEY':secret['SERVICE_KEY'],'AUTH_JWT_SECRET':secret['JWT_SECRET'],
         'POSTGREST_URL':'http://127.0.0.1:3300','DATABASE_URL':url('supabase_storage_admin','STORAGE_DB_PASSWORD'),
         'STORAGE_BACKEND':'s3','GLOBAL_S3_BUCKET':'supabase','GLOBAL_S3_ENDPOINT':'http://127.0.0.1:9002',
         'GLOBAL_S3_PROTOCOL':'http','GLOBAL_S3_FORCE_PATH_STYLE':'true','AWS_ACCESS_KEY_ID':secret['S3_ACCESS_KEY'],
         'AWS_SECRET_ACCESS_KEY':secret['S3_SECRET_KEY'],'REGION':'us-east-1','TENANT_ID':'anp','FILE_SIZE_LIMIT':'52428800',
         'ENABLE_IMAGE_TRANSFORMATION':'false','STORAGE_PUBLIC_URL':public+'/supabase',
         'REQUEST_ALLOW_X_FORWARDED_PATH':'true'},('supabase-auth','supabase-rest','rustfs'),'/status','/opt/anp/services/supabase-storage')
    add('supabase-realtime',['/usr/bin/python3','/opt/anp/runtime/realtime.py'],4100,
        {'PORT':'4100','DB_HOST':'127.0.0.1','DB_PORT':'5432','DB_NAME':'postgres',
         'DB_USER':'supabase_admin','DB_PASSWORD':secret['REALTIME_DB_PASSWORD'],
         'DB_AFTER_CONNECT_QUERY':'SET search_path TO _realtime','DB_SSL':'false',
         'DB_IP_VERSION':'ipv4','REALTIME_IP_VERSION':'ipv4','DB_POOL_SIZE':'3','DB_REPLICA_POOL_SIZE':'1',
         'DB_ENC_KEY':secret['REALTIME_DB_ENC_KEY'],
         'DB_ENC_KEY_GCM':secret['REALTIME_DB_ENC_KEY_GCM'],'DB_ENC_WRITE_GCM':'true','SECRET_KEY_BASE':secret['REALTIME_SECRET_KEY_BASE'],
         'API_JWT_SECRET':secret['JWT_SECRET'],'METRICS_JWT_SECRET':secret['JWT_SECRET'],
         'APP_NAME':'realtime','SELF_HOST_TENANT_NAME':'realtime-dev','RUN_JANITOR':'true',
         'DISABLE_HEALTHCHECK_LOGGING':'true','LOG_LEVEL':'warning','ERL_AFLAGS':'-proto_dist inet_tcp',
         'ERL_FLAGS':'+S 2:2',
         # libcluster_postgres 以 Cookie 作为 LISTEN 频道名，必须小于 64 字节。
         'RELEASE_COOKIE':hashlib.sha256(secret['REALTIME_SECRET_KEY_BASE'].encode()).hexdigest()[:48],
         'RELEASE_TMP':str(DATA/'run/supabase-realtime'),
         'ERL_CRASH_DUMP':str(DATA/'logs/supabase-realtime/erl_crash.dump')},
        ('supabase-auth',),'/api/tenants/realtime-dev/health','/opt/anp/services/supabase-realtime',600)
    add('supabase-gateway',['/opt/anp/bin/envoy','-c',str(conf/'envoy.yaml'),'--concurrency','2','--log-level','warning',
        '--base-id','3'],8000,deps=('supabase-auth','supabase-rest','supabase-storage','supabase-realtime'),path='/auth/v1/health')
    add('supabase-studio',[NODE22,'apps/studio/server.js'],3200,studio,
        ('supabase-meta','supabase-gateway'),'/supabase/api/platform/profile','/opt/anp/services/supabase-studio')
    override=DATA/'config/services.json'
    if override.exists():
        for name,env in json.loads(override.read_text()).items():
            if name not in result or not isinstance(env,dict):
                raise ValueError('services.json 包含无效的服务配置')
            result[name]['env'].update({k:str(v) for k,v in env.items()})
    return result
