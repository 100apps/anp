"""组合锁定的上游配置；所有持久化服务位于 Docker，PG/Redis/CH/S3 各一个实例。"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import jwt
import yaml
from dotenv import dotenv_values
from anp import ROOT
from anp.applications.registry import applications

P = ROOT / 'platform'
G = ROOT / '.local/generated'


def command(args, *, capture=True, input=None, cwd=ROOT):
    p = subprocess.run(args, cwd=cwd, input=input, text=True, encoding='utf-8',
                       capture_output=capture)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or '命令执行失败')[-3000:])
    return p.stdout


def envfile(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(f'{k}={v}\n' for k, v in data.items()), encoding='utf-8')


def generate():
    G.mkdir(parents=True, exist_ok=True)
    config = ROOT / 'config'
    config.mkdir(exist_ok=True)
    secret_file = config / 'secrets.local.env'
    s = dict(dotenv_values(secret_file)) if secret_file.exists() else {}
    for name in ('POSTGRES_PASSWORD','JWT_SECRET','AGENT_DB_PASSWORD','LITELLM_DB_PASSWORD',
                 'LANGFUSE_DB_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','DBX_PASSWORD',
                 'ENGINE_TOKEN','WORKER_TOKEN','LITELLM_KEY','ADMIN_PASSWORD','DASHBOARD_PASSWORD',
                 'LF_AUTH','LF_SALT','LF_ENCRYPTION','CH_PASSWORD','LANGFUSE_SECRET_KEY',
                 'LITELLM_CH_WRITE_PASSWORD','LITELLM_CH_READ_PASSWORD'):
        s.setdefault(name, secrets.token_hex(24))
    s.setdefault('ADMIN_EMAIL', 'admin@anp.local')
    s.setdefault('S3_ACCESS_KEY', 'anp' + secrets.token_hex(8))
    s.setdefault('LANGFUSE_PUBLIC_KEY', 'pk-lf-' + secrets.token_hex(16))
    if not s['LANGFUSE_SECRET_KEY'].startswith('sk-lf-'):
        s['LANGFUSE_SECRET_KEY'] = 'sk-lf-' + s['LANGFUSE_SECRET_KEY']
    for name, role in [('ANON_KEY','anon'),('SERVICE_ROLE_KEY','service_role')]:
        s.setdefault(name, jwt.encode({'role':role,'iss':'supabase','iat':int(time.time()),
            'exp':int(time.time())+315360000}, s['JWT_SECRET'], algorithm='HS256'))
    envfile(secret_file, s)
    local = config / 'settings.local.toml'
    if not local.exists():
        shutil.copy(config / 'settings.example.toml', local)
    # 容器中的应用仅通过 HTTP 连接外部 Worker；按需挂载配置文件。
    from anp.config import settings, value
    settings.cache_clear()
    settings()
    sbdir = P / 'config/supabase'
    sb = dict(dotenv_values(sbdir / 'upstream.env.example'))
    sb.pop('COMPOSE_FILE', None)
    sb.update({
        'POSTGRES_PASSWORD':s['POSTGRES_PASSWORD'],'JWT_SECRET':s['JWT_SECRET'],
        'ANON_KEY':s['ANON_KEY'],'SERVICE_ROLE_KEY':s['SERVICE_ROLE_KEY'],
        'POSTGRES_HOST':'postgres','POSTGRES_DB':'postgres','POSTGRES_PORT':'5432',
        'DASHBOARD_PASSWORD':s['DASHBOARD_PASSWORD'],
        'SECRET_KEY_BASE':hashlib.sha512(s['JWT_SECRET'].encode()).hexdigest(),
        'REALTIME_DB_ENC_KEY':s['JWT_SECRET'][:16], 'VAULT_ENC_KEY':s['JWT_SECRET'][:32],
        'PG_META_CRYPTO_KEY':s['JWT_SECRET'],'OPENAI_API_KEY':'',
        'SUPABASE_PUBLIC_URL':value('supabase','url').replace('127.0.0.1','localhost'),
        'API_EXTERNAL_URL':value('supabase','url').replace('127.0.0.1','localhost')+'/auth/v1',
        'SITE_URL':value('workbench','web_url'),'ADDITIONAL_REDIRECT_URLS':value('workbench','web_url'),
        'DISABLE_SIGNUP':'true','ENABLE_EMAIL_AUTOCONFIRM':'true',
        'STUDIO_DEFAULT_ORGANIZATION':'ANP','STUDIO_DEFAULT_PROJECT':'本地工作台',
        'PGRST_DB_SCHEMAS':','.join(['public','platform']+['app_'+a for a in applications()]),
    })
    envfile(G / 'supabase.env', sb)
    resolved = json.loads(command(['docker','compose','--env-file',str(G/'supabase.env'),
        '-f',str(sbdir/'upstream.compose.yaml'),'config','--format','json']))
    services = resolved['services']
    for name in ('functions','supavisor','realtime'):
        services.pop(name, None)
    services['postgres'] = services.pop('db')
    volumes = {'pg_data':{},'pg_config':{},'rustfs_data':{},'rustfs_logs':{},'redis_data':{},
               'clickhouse_data':{},'clickhouse_logs':{},'dbx_data':{}}
    for name, service in services.items():
        service.pop('container_name', None)
        service.pop('networks', None)
        service.pop('ports', None)
        deps=service.get('depends_on', {})
        if 'db' in deps:
            deps['postgres'] = deps.pop('db')
        for mount in service.get('volumes',[]):
            if mount.get('type')=='bind':
                source=Path(mount['source'])
                if source.name=='data':
                    mount.update(type='volume',source='pg_data'); mount.pop('bind',None)
                elif source.name=='storage':
                    mount.update(type='volume',source='storage_unused'); mount.pop('bind',None)
                    volumes['storage_unused']={}
                elif not source.exists():
                    source.mkdir(parents=True,exist_ok=True)
            elif mount.get('source')=='db-config':
                mount['source']='pg_config'
    services['postgres']['ports']=['127.0.0.1:54322:5432']
    services['api-gw']['ports']=['127.0.0.1:8000:8000']
    services['postgres']['networks']={'default':{'aliases':['db']}}
    # 每个 database 使用独立账号；不创建第二个 PostgreSQL 守护进程。
    database_sql=''
    for user,password,database in [('agent',s['AGENT_DB_PASSWORD'],'agent_local'),
            ('litellm',s['LITELLM_DB_PASSWORD'],'litellm'),('langfuse',s['LANGFUSE_DB_PASSWORD'],'langfuse')]:
        database_sql += f"SELECT 'CREATE ROLE {user} LOGIN PASSWORD ''{password}''' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{user}') \\gexec\n"
        database_sql += f"SELECT 'CREATE DATABASE {database} OWNER {user}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='{database}') \\gexec\n"
        database_sql += f'REVOKE CONNECT ON DATABASE {database} FROM PUBLIC;\nGRANT CONNECT ON DATABASE {database} TO {user};\n'
    (G/'databases.sql').write_text(database_sql,encoding='utf-8')
    services['database-init']={'image':'postgres:17','restart':'no',
        'depends_on':{'postgres':{'condition':'service_healthy'}},
        'environment':{'PGPASSWORD':s['POSTGRES_PASSWORD']},
        'volumes':['../.local/generated/databases.sql:/init.sql:ro'],
        'command':['psql','-h','postgres','-U','supabase_admin','-d','postgres','-v','ON_ERROR_STOP=1','-f','/init.sql']}
    services['redis']={'image':'redis:7','restart':'unless-stopped',
        'ports':['127.0.0.1:6379:6379'], 'volumes':['redis_data:/data'],
        'command':['redis-server','--requirepass',s['REDIS_PASSWORD'],'--appendonly','yes','--maxmemory-policy','noeviction'],
        'environment':{'REDISCLI_AUTH':s['REDIS_PASSWORD']},
        'healthcheck':{'test':['CMD','redis-cli','ping'],'interval':'5s','timeout':'5s','retries':20}}
    services['clickhouse']={'image':'clickhouse/clickhouse-server:25.12','restart':'unless-stopped',
        'ports':['127.0.0.1:8123:8123'],'environment':{'CLICKHOUSE_USER':'anp','CLICKHOUSE_PASSWORD':s['CH_PASSWORD'],
        'CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT':'1'},
        'volumes':['clickhouse_data:/var/lib/clickhouse','clickhouse_logs:/var/log/clickhouse-server'],
        'healthcheck':{'test':['CMD-SHELL','wget -q -O - http://localhost:8123/ping'],'interval':'5s','timeout':'5s','retries':20}}
    services['rustfs-volume-init']={'image':'python:3.12-slim','restart':'no','user':'0:0',
        'volumes':['rustfs_data:/data','rustfs_logs:/logs'],
        'command':['python','-c',"import os; os.makedirs('/data/rustfs0',exist_ok=True); [os.chown(p,10001,10001) for p in ('/data','/data/rustfs0','/logs')]"]}
    services['rustfs']={'image':'rustfs/rustfs:latest','restart':'unless-stopped',
        'depends_on':{'rustfs-volume-init':{'condition':'service_completed_successfully'}},
        'ports':['127.0.0.1:9000:9000','127.0.0.1:9001:9001'],
        'volumes':['rustfs_data:/data','rustfs_logs:/logs'],
        'environment':{'RUSTFS_VOLUMES':'/data/rustfs0','RUSTFS_ADDRESS':'0.0.0.0:9000',
          'RUSTFS_CONSOLE_ADDRESS':'0.0.0.0:9001','RUSTFS_CONSOLE_ENABLE':'true',
          'RUSTFS_ACCESS_KEY':s['S3_ACCESS_KEY'],'RUSTFS_SECRET_KEY':s['S3_SECRET_KEY'],
          'RUSTFS_OBS_LOG_DIRECTORY':'/logs','RUSTFS_OBS_LOGGER_LEVEL':'warn'},
        'healthcheck':{'test':['CMD-SHELL','curl -fsS http://localhost:9000/health/ready'],
            'interval':'5s','timeout':'5s','retries':30,'start_period':'15s'}}
    services['storage-init']={'image':'anp/runtime:0.2.0','build':{'context':'..','dockerfile':'platform/docker/agent.Dockerfile'},
        'restart':'no','depends_on':{'rustfs':{'condition':'service_healthy'}},
        'environment':{'S3_ACCESS_KEY':s['S3_ACCESS_KEY'],'S3_SECRET_KEY':s['S3_SECRET_KEY']},
        'command':['python','/workspace/platform/init/storage/buckets.py']}
    services['storage']['environment'].update({'STORAGE_BACKEND':'s3','GLOBAL_S3_BUCKET':'supabase-storage',
        'GLOBAL_S3_ENDPOINT':'http://rustfs:9000','GLOBAL_S3_PROTOCOL':'http','GLOBAL_S3_FORCE_PATH_STYLE':'true',
        'AWS_ACCESS_KEY_ID':s['S3_ACCESS_KEY'],'AWS_SECRET_ACCESS_KEY':s['S3_SECRET_KEY'],'REGION':'us-east-1'})
    services['storage'].pop('volumes',None)
    services['storage']['depends_on']['storage-init']={'condition':'service_completed_successfully'}
    lfenv={'DATABASE_URL':f"postgresql://langfuse:{s['LANGFUSE_DB_PASSWORD']}@postgres:5432/langfuse",
        'SALT':s['LF_SALT'],'ENCRYPTION_KEY':hashlib.sha256(s['LF_ENCRYPTION'].encode()).hexdigest(),
        'NEXTAUTH_SECRET':s['LF_AUTH'],'CLICKHOUSE_URL':'http://clickhouse:8123',
        'CLICKHOUSE_MIGRATION_URL':'clickhouse://clickhouse:9000','CLICKHOUSE_USER':'anp',
        'CLICKHOUSE_PASSWORD':s['CH_PASSWORD'],'REDIS_AUTH':s['REDIS_PASSWORD'],
        'TELEMETRY_ENABLED':'false','NEXTAUTH_URL':'http://localhost:3000',
        'LANGFUSE_INIT_ORG_ID':'anp','LANGFUSE_INIT_ORG_NAME':'ANP 工作台',
        'LANGFUSE_INIT_PROJECT_ID':'anp','LANGFUSE_INIT_PROJECT_NAME':'业务运行',
        'LANGFUSE_INIT_PROJECT_PUBLIC_KEY':s['LANGFUSE_PUBLIC_KEY'],
        'LANGFUSE_INIT_PROJECT_SECRET_KEY':s['LANGFUSE_SECRET_KEY'],
        'LANGFUSE_INIT_USER_EMAIL':s['ADMIN_EMAIL'],'LANGFUSE_INIT_USER_NAME':'管理员',
        'LANGFUSE_INIT_USER_PASSWORD':s['ADMIN_PASSWORD']}
    for kind in ('EVENT_UPLOAD','MEDIA_UPLOAD','BATCH_EXPORT'):
        for key,val in {'ENDPOINT':'http://rustfs:9000','ACCESS_KEY_ID':s['S3_ACCESS_KEY'],
            'SECRET_ACCESS_KEY':s['S3_SECRET_KEY'],'REGION':'us-east-1'}.items():
            lfenv['LANGFUSE_S3_'+kind+'_'+key]=val
    envfile(G/'langfuse.env',lfenv)
    lf=json.loads(command(['docker','compose','--env-file',str(G/'langfuse.env'),'-f',
        str(P/'config/langfuse/upstream.compose.yaml'),'config','--format','json']))
    for name in ('langfuse-web','langfuse-worker'):
        service=lf['services'][name]
        service.pop('networks',None);service.pop('ports',None)
        service['depends_on']={'database-init':{'condition':'service_completed_successfully'},
            'storage-init':{'condition':'service_completed_successfully'},
            'redis':{'condition':'service_healthy'},'clickhouse':{'condition':'service_healthy'}}
        services[name]=service
    services['langfuse-web']['ports']=['127.0.0.1:3000:3000']
    services['langfuse-web']['environment'].update({'LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT':'http://localhost:9000',
        'LANGFUSE_S3_MEDIA_UPLOAD_INTERNAL_ENDPOINT':'http://rustfs:9000',
        'LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT':'http://localhost:9000'})
    services['litellm-tracing-init']={'image':'python:3.12-slim','restart':'no','profiles':['llm'],
        'depends_on':{'clickhouse':{'condition':'service_healthy'}},
        'environment':{key:s[key] for key in ('CH_PASSWORD','LITELLM_CH_WRITE_PASSWORD','LITELLM_CH_READ_PASSWORD')},
        'volumes':['./init/litellm_tracing.py:/init.py:ro'],'command':['python','/init.py']}
    services['litellm']={'image':'ghcr.io/berriai/litellm:main-stable','restart':'unless-stopped',
        'depends_on':{'database-init':{'condition':'service_completed_successfully'},
            'litellm-tracing-init':{'condition':'service_completed_successfully'}},
        'ports':['127.0.0.1:4000:4000'],
        'environment':{'DATABASE_URL':f"postgresql://litellm:{s['LITELLM_DB_PASSWORD']}@postgres:5432/litellm",
            'LITELLM_MASTER_KEY':s['LITELLM_KEY'],'STORE_MODEL_IN_DB':'True',
            'FREELLM_API_BASE':value('litellm','upstream_url','http://24.199.104.17:3001/v1'),
            'FREELLM_API_KEY':s.get('FREELLM_API_KEY',''),
            'CLICKHOUSE_DATABASE':'litellm',
            'CLICKHOUSE_URL':f"http://litellm_writer:{s['LITELLM_CH_WRITE_PASSWORD']}@clickhouse:8123",
            'CLICKHOUSE_READER_URL':f"http://litellm_reader:{s['LITELLM_CH_READ_PASSWORD']}@clickhouse:8123"},
        'volumes':['./config/litellm/models.yaml:/app/config.yaml:ro'],
        'command':['--config','/app/config.yaml','--port','4000']}
    services['dbx']={'image':'t8y2/dbx:latest','restart':'unless-stopped',
        'ports':['127.0.0.1:4224:4224'],'volumes':['dbx_data:/app/data'],
        'environment':{'DBX_PASSWORD':s['DBX_PASSWORD'],'DBX_DATA_DIR':'/app/data','DBX_DISABLE_PASSWORD_VIEWER':'true'}}
    agentservices={}
    for index,(app_id,manifest) in enumerate(applications().items()):
        (P/'config/agents').mkdir(parents=True,exist_ok=True)
        graph_config={'dependencies':['.'],'graphs':{app_id:f'apps.{app_id}.flow.deployment:graph'},
            'auth':{'path':'./src/anp/runtime/agent_auth.py:auth','disable_studio_auth':True}}
        (P/f'config/agents/{app_id}.generated.json').write_text(json.dumps(graph_config,indent=2)+'\n')
        env={**s,'ANP_ROOT':'/workspace','ANP_WORKBENCH_WORKER_URL':'http://host.docker.internal:8281',
            'ANP_SUPABASE_URL':'http://api-gw:8000','ANP_LANGFUSE_URL':'http://langfuse-web:3000',
            'LANGFUSE_BASE_URL':'http://langfuse-web:3000','LANGSMITH_TRACING':'false'}
        # 只把运行时需要的密钥传给引擎，排除数据库和管理员密码。
        allow={'ENGINE_TOKEN','WORKER_TOKEN','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY'}
        env={k:v for k,v in env.items() if k in allow or k.startswith('ANP_') or k in ('LANGFUSE_BASE_URL','LANGSMITH_TRACING')}
        service={'image':'anp/runtime:0.2.0','build':{'context':'..','dockerfile':'platform/docker/agent.Dockerfile'},
            'restart':'unless-stopped','environment':env,
            'ports':[f'127.0.0.1:{8124+index}:8000'],
            'command':['langgraph','dev','--host','0.0.0.0','--port','8000','--no-browser',
                '--config',f'/workspace/platform/config/agents/{app_id}.generated.json'],
            'volumes':[f'agent_{app_id}_state:/workspace/.langgraph_api'],
            'stop_grace_period':'60s'}
        volumes[f'agent_{app_id}_state']={}
        agentservices['agent-'+app_id]=service
    services.update(agentservices)
    # 管理后台和模型网关按需启动；默认业务入口不能依赖可选后台。
    services['api-gw'].get('depends_on', {}).pop('studio', None)
    services['studio'].setdefault('depends_on', {})['meta'] = {'condition':'service_started'}
    for name in ('studio','meta','dbx'):
        services[name]['profiles'] = ['admin']
        services[name]['restart'] = 'no'
    services['litellm']['profiles'] = ['llm']
    services['litellm']['restart'] = 'no'
    if s.get('LENS_WORKER_TOKEN'):
        services['lens-worker']={'image':services['litellm']['image'],'restart':'unless-stopped','profiles':['llm'],
            'depends_on':{'litellm':{'condition':'service_started'}},
            'entrypoint':['python','-m','litellm.proxy.lens.worker'],'command':[],
            'environment':{'LITELLM_URL':'http://litellm:4000','LENS_WORKER_TOKEN':s['LENS_WORKER_TOKEN']}}
    lockfile=P/'images.lock.json'
    lock=json.loads(lockfile.read_text()) if lockfile.exists() else {}
    for service in services.values():
        service['image']=lock.get(service['image'],service['image'])
    for name,volume in volumes.items():
        volume['name']='anp_v2_'+name
    document={'name':'anp2','services':services,'volumes':volumes}
    from anp.management.bindings import bind
    custom_connections=bind(document,settings(),s)
    (P/'compose.generated.yaml').write_text(yaml.safe_dump(document,allow_unicode=True,sort_keys=False),encoding='utf-8')
    (P/'compose.yaml').write_text('# 本文件是启动入口；生成文件不提交，配置由 anp init 重建。\ninclude:\n  - compose.generated.yaml\n',encoding='utf-8')
    # 调试配置挂载整个工作区，业务源码与解释器配置相同。
    overrides={'services':{}}
    for index,app_id in enumerate(applications()):
        overrides['services']['agent-'+app_id]={
            'environment':{'PYDEVD_USE_CYTHON':'NO','PYDEVD_USE_FRAME_EVAL':'NO'},
            'volumes':['../:/workspace'],
            'ports':[f'127.0.0.1:{5678+index}:5678'],
            'command':['langgraph','dev','--host','0.0.0.0','--port','8000','--no-browser',
                '--config',f'/workspace/platform/config/agents/{app_id}.generated.json','--debug-port','5678','--allow-blocking','--no-reload','--wait-for-client']}
    (P/'compose.debug.yaml').write_text(yaml.safe_dump(overrides,allow_unicode=True,sort_keys=False),encoding='utf-8')
    # 与 Web 相同的实际连接信息，初始化 DBX 时填入密钥。
    connections=[]
    for db in ('postgres','agent_local','litellm','langfuse'):
        user='supabase_admin' if db=='postgres' else ('agent' if db=='agent_local' else db)
        pw={'supabase_admin':s['POSTGRES_PASSWORD'],'agent':s['AGENT_DB_PASSWORD'],
            'litellm':s['LITELLM_DB_PASSWORD'],'langfuse':s['LANGFUSE_DB_PASSWORD']}[user]
        connections.append({'id':'anp-'+db,'name':'ANP / '+db,'dbType':'postgres','host':'postgres',
            'port':5432,'username':user,'password':pw,'database':db,'savePassword':True})
    connections += [
        {'id':'anp-redis','name':'ANP / Redis','dbType':'redis','host':'redis','port':6379,
          'username':'default','password':s['REDIS_PASSWORD'],'database':'0','savePassword':True},
        {'id':'anp-clickhouse','name':'ANP / ClickHouse','dbType':'clickhouse','host':'clickhouse','port':8123,
          'username':'anp','password':s['CH_PASSWORD'],'database':'default','savePassword':True}]
    if custom_connections: connections=custom_connections
    (G/'dbx-connections.private.json').write_text(json.dumps(connections),encoding='utf-8')
    for connection in connections:
        if 'dbType' in connection: connection['db_type']=connection.pop('dbType')
        if 'savePassword' in connection: connection['save_password']=connection.pop('savePassword')
        # 与 DBX Web 的 normalizeConnection 一致，避免保存凭据的目标指纹发生变化。
        connection['driver_profile']=connection.get('driver_profile') or connection['db_type']
        connection['show_database_links']=connection.get('show_database_links') is not False
    (G/'dbx-connections.private.json').write_text(json.dumps(connections),encoding='utf-8')
    (P/'config/dbx').mkdir(parents=True,exist_ok=True)
    public_connections=[{k:v for k,v in c.items() if k!='password'} for c in connections]
    (P/'config/dbx/connections.yaml').write_text(yaml.safe_dump(public_connections,allow_unicode=True),encoding='utf-8')
    (config/'local-credentials.md').write_text(
        '# 本机平台登录信息（私有，不提交）\n\n'
        '本文由 `uv run anp init` 生成，覆盖平台连接页全部系统和底层资源。'
        '下列密码是当前配置中的初始凭据；若在后台自行修改过密码，以修改后的密码为准。\n\n'
        '## 应用工作台\n\n'
        f"- 入口：[{value('workbench','web_url')}]({value('workbench','web_url')})\n"
        f"- 邮箱：`{s['ADMIN_EMAIL']}`\n- 密码：`{s['ADMIN_PASSWORD']}`\n"
        '- 身份系统：Supabase Auth，业务 Web 与 CLI 共用账号。\n\n'
        '## Supabase Studio\n\n'
        f"- 入口：[{value('supabase','url')}]({value('supabase','url')})\n"
        f"- 用户名：`{sb.get('DASHBOARD_USERNAME','supabase')}`\n- 密码：`{s['DASHBOARD_PASSWORD']}`\n"
        '- 这是管理后台的 HTTP Basic 登录，与业务用户账号分开。\n\n'
        '## LiteLLM\n\n'
        f"- 入口：[{value('litellm','url').removesuffix('/v1')}/ui]({value('litellm','url').removesuffix('/v1')}/ui)\n"
        f"- 用户名：`{services.get('litellm',{}).get('environment',{}).get('UI_USERNAME','admin')}`\n"
        f"- 密码：`{services.get('litellm',{}).get('environment',{}).get('UI_PASSWORD') or s['LITELLM_KEY']}`\n"
        f"- 管理 API / 模型调用密钥：`{s['LITELLM_KEY']}`\n"
        '- API 请求头：`Authorization: Bearer <上述密钥>`。\n'
        '- 上游模型：`auto`、`auto:fast`；上游地址由 `[litellm].upstream_url` 配置，'
        '上游密钥保存在 `config/secrets.local.env` 的 `FREELLM_API_KEY`。\n'
        '- Lens 分析模型：`auto`；Worker Token 保存在同一私有文件的 `LENS_WORKER_TOKEN`。\n'
        '- 未单独配置 UI_PASSWORD 时，后台密码使用管理密钥；'
        '[官方登录说明](https://docs.litellm.ai/docs/proxy/ui)。\n\n'
        '## Langfuse\n\n'
        f"- 入口：[{value('langfuse','url')}]({value('langfuse','url')})\n"
        f"- 邮箱：`{s['ADMIN_EMAIL']}`\n- 初始密码：`{s['ADMIN_PASSWORD']}`\n"
        '- 本机初始化组织：ANP 工作台；项目：业务运行。\n'
        f"- 项目 Public Key：`{s['LANGFUSE_PUBLIC_KEY']}`\n"
        f"- 项目 Secret Key：`{s['LANGFUSE_SECRET_KEY']}`\n"
        '- 项目密钥用于 SDK/API，不用于网页邮箱密码登录。\n\n'
        '## RustFS\n\n'
        '- 控制台入口：[http://localhost:9001](http://localhost:9001)\n'
        f"- S3 API：`{value('storage','endpoint')}`\n"
        f"- 用户名 / Access Key：`{s['S3_ACCESS_KEY']}`\n- 密码 / Secret Key：`{s['S3_SECRET_KEY']}`\n\n"
        '## DBX\n\n'
        f"- 入口：[{value('dbx','url')}]({value('dbx','url')})\n"
        f"- 密码：`{s['DBX_PASSWORD']}`\n"
        '- 登录页只需密码，不需要用户名；六个数据库连接已预配置。\n\n'
        '## Agent Server\n\n'
        f"- API 文档：[{value('agent','url')}/docs]({value('agent','url')}/docs)\n"
        '- 没有独立的网页用户名/密码登录页，业务运行由 API 调用。\n'
        f"- 引擎访问令牌：`{s['ENGINE_TOKEN']}`\n"
        '- 请求头：`Authorization: Bearer <引擎访问令牌>`。\n'
        '- 正常使用业务 API/CLI 时由平台自动携带，无需手工填写。\n\n'
        '## LangGraph Studio\n\n'
        f"- 入口：[打开 Studio](https://smith.langchain.com/studio/?baseUrl={value('agent','url')})\n"
        '- 使用你自己的 LangSmith 账号登录，没有本机生成的 Studio 用户名或密码。\n'
        '- 首次连接时，在 Server connection settings → Custom Headers 中填写：\n'
        '  - Header 名称：`Authorization`\n'
        f"  - Header 值：`Bearer {s['ENGINE_TOKEN']}`\n"
        f"  - Base URL：`{value('agent','url')}`\n"
        '- 然后点击 Connect。请求头保存在当前浏览器本地；换浏览器或清除站点数据后需要重新填写。\n'
        '- 工作台、Langfuse 和 Supabase 账号不能用于此登录；本机图调试也可直接使用 IDE 的 F5。\n\n'
        '## 底层数据库（也可直接通过 DBX 管理）\n\n'
        '| 资源 | 地址 / 数据库 | 用户名 | 密码 |\n|---|---|---|---|\n'
        f"| PostgreSQL 管理 | `{value('postgres','host')}:{value('postgres','port')}/postgres` | `supabase_admin` | `{s['POSTGRES_PASSWORD']}` |\n"
        f"| 本地图 checkpoint | 同一 PG 实例，`agent_local` | `agent` | `{s['AGENT_DB_PASSWORD']}` |\n"
        f"| LiteLLM 数据库 | 同一 PG 实例，`litellm` | `litellm` | `{s['LITELLM_DB_PASSWORD']}` |\n"
        f"| Langfuse 数据库 | 同一 PG 实例，`langfuse` | `langfuse` | `{s['LANGFUSE_DB_PASSWORD']}` |\n"
        f"| Redis | `{value('redis','url')}` | `default` | `{s['REDIS_PASSWORD']}` |\n"
        f"| ClickHouse | `{value('clickhouse','url')}` | `anp` | `{s['CH_PASSWORD']}` |\n"
        f"| Lens ClickHouse 写入 | 同一 ClickHouse，`litellm` | `litellm_writer` | `{s['LITELLM_CH_WRITE_PASSWORD']}` |\n"
        f"| Lens ClickHouse 只读 | 同一 ClickHouse，`litellm` | `litellm_reader` | `{s['LITELLM_CH_READ_PASSWORD']}` |\n\n"
        '完整服务密钥来源：`config/secrets.local.env`。本文件仅保存在本机，不应放进前端或发送给普通业务用户。\n',encoding='utf-8')
    return {'applications':list(applications()),'configuration':'platform/compose.generated.yaml'}


def compose(*args, capture=False):
    return command(['docker','compose','-f',str(P/'compose.generated.yaml'),*args],capture=capture,cwd=P)


def sql(text,database='postgres'):
    from anp.config import value,secret
    remote=value('postgres','migration_dsn_env')
    if remote:
        import psycopg
        with psycopg.connect(secret(remote),autocommit=True) as connection:
            connection.execute(text,prepare=False)
        return ''
    return command(['docker','compose','-f',str(P/'compose.generated.yaml'),'exec','-T','postgres',
        'psql','-U','supabase_admin','-d',database,'-v','ON_ERROR_STOP=1'],input=text,cwd=P)


def migrate():
    files=list(sorted((ROOT/'migrations').glob('*.sql')))
    for app_id in applications():
        files.extend(sorted((ROOT/'apps'/app_id/'migrations').glob('*.sql')))
    for file in files:
        name=file.relative_to(ROOT).as_posix().replace("'","''")
        sql('BEGIN;\n'+file.read_text(encoding='utf-8')+f"\nINSERT INTO platform.migrations(name) VALUES('{name}') ON CONFLICT(name) DO UPDATE SET applied_at=now();\nCOMMIT;")
    for app_id,manifest in applications().items():
        for action in manifest['permissions']['actions']:
            permission=app_id+'.'+action
            roles=['admin','developer']+(['viewer'] if action=='task.read' else [])
            for role in roles:
                sql(f"INSERT INTO platform.role_permissions VALUES('{role}','{permission}') ON CONFLICT DO NOTHING;")
    sql("NOTIFY pgrst,'reload schema';")
    return {'migrations':[str(f.relative_to(ROOT)) for f in files]}


def lock_images():
    doc=yaml.safe_load((P/'compose.generated.yaml').read_text(encoding='utf-8'))
    lock={}
    for svc in doc['services'].values():
        if 'build' in svc: continue
        image=svc['image']
        digests=json.loads(command(['docker','image','inspect',image,'--format','{{json .RepoDigests}}']))
        if digests: lock[image]=digests[0]
    (P/'images.lock.json').write_text(json.dumps(lock,indent=2)+'\n')
    return {'locked_images':len(lock)}
