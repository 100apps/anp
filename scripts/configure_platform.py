"""生成 V3 私有运行配置；地址可替换为云资源，不更改业务源码。"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import tomllib
import os
from urllib.parse import quote,urlsplit
from platform_bindings import bind,expand,relocate
from anp.applications.registry import applications
import yaml
from dotenv import dotenv_values
from anp import ROOT
from anp.management.platform import envfile


def generate(public='http://localhost:8080',smoke=False,deployment=None,target='container'):
    private=ROOT/'config/secrets.local.env'
    s=dict(dotenv_values(private))
    for key in ['LENS_ADMIN_TOKEN','LITELLM_LENS_SERVICE_TOKEN','LENS_GATEWAY_SECRET']:
        s.setdefault(key,secrets.token_hex(32))
    s.setdefault('LENS_V3_ANALYSIS_KEY','sk-anp-lens-'+secrets.token_hex(24))
    envfile(private,s)
    host='postgres'
    schemas='public,platform'+''.join(',app_'+app_id for app_id in applications())
    pg_port,ch_port,s3_port,redis_port=5432,8123,9000,6379
    def dsn(db,user,password):return f'postgresql://{quote(user,safe="")}:{quote(password,safe="")}@{host}:{pg_port}/{db}'
    storage={'postgres_url':dsn('postgres','supabase_admin',s['POSTGRES_PASSWORD']),
        's3_url':f'http://rustfs:{s3_port}','s3_access_key':s['S3_ACCESS_KEY'],'s3_secret_key':s['S3_SECRET_KEY']}
    services={}
    def add(name,port,path,environment,health,dependencies=(),strip=False,command=None):
        services[name]={'required':True,'environment':environment,'depends_on':list(dependencies),
            'health_url':f'http://127.0.0.1:{port}{health}' if health else None}
        if command:services[name]['command']=command
        if path:routes.append({'path':path,'target':f'127.0.0.1:{port}','strip_prefix':strip})
    routes=[]
    add('auth',19999,'/supabase/auth/v1/',{
        'GOTRUE_API_HOST':'127.0.0.1','GOTRUE_API_PORT':'19999',
        'GOTRUE_DB_DRIVER':'postgres','GOTRUE_DB_DATABASE_URL':dsn('postgres','supabase_auth_admin',s['POSTGRES_PASSWORD']),
        'API_EXTERNAL_URL':public+'/supabase/auth/v1','GOTRUE_SITE_URL':public,
        'GOTRUE_URI_ALLOW_LIST':public+'/**','GOTRUE_DISABLE_SIGNUP':'true',
        'GOTRUE_JWT_ADMIN_ROLES':'service_role','GOTRUE_JWT_AUD':'authenticated',
        'GOTRUE_JWT_DEFAULT_GROUP_NAME':'authenticated','GOTRUE_JWT_EXP':'3600','GOTRUE_JWT_SECRET':s['JWT_SECRET'],
        'GOTRUE_EXTERNAL_EMAIL_ENABLED':'true','GOTRUE_MAILER_AUTOCONFIRM':'true'},'/health',strip=True)
    add('storage',15000,'/supabase/storage/v1/',{
        'HOST':'127.0.0.1','PORT':'15000','ANON_KEY':s['ANON_KEY'],'SERVICE_KEY':s['SERVICE_ROLE_KEY'],
        'JWT_SECRET':s['JWT_SECRET'],'DATABASE_URL':dsn('postgres','supabase_storage_admin',s['POSTGRES_PASSWORD']),
        'PGOPTIONS':'-c search_path=storage,public','STORAGE_BACKEND':'s3','TENANT_ID':'stub','REGION':'us-east-1',
        'GLOBAL_S3_BUCKET':'supabase-storage','GLOBAL_S3_ENDPOINT':storage['s3_url'],'GLOBAL_S3_PROTOCOL':'http',
        'GLOBAL_S3_FORCE_PATH_STYLE':'true','AWS_ACCESS_KEY_ID':s['S3_ACCESS_KEY'],
        'AWS_SECRET_ACCESS_KEY':s['S3_SECRET_KEY'],'POSTGREST_URL':'http://127.0.0.1:13002',
        'PGRST_JWT_SECRET':s['JWT_SECRET'],'AUTH_JWT_SECRET':s['JWT_SECRET'],'FILE_SIZE_LIMIT':'10737418240'},
        '/status',dependencies=['auth'],strip=True)
    add('postgrest',13002,'/supabase/rest/v1/',{
        'PGRST_DB_URI':dsn('postgres','authenticator',s['POSTGRES_PASSWORD']),
        'PGRST_DB_SCHEMAS':schemas,'PGRST_DB_ANON_ROLE':'anon','PGRST_JWT_SECRET':s['JWT_SECRET'],
        'PGRST_SERVER_PORT':'13002','PGRST_SERVER_HOST':'127.0.0.1','PGRST_ADMIN_SERVER_PORT':'13003',
        'PGRST_DB_POOL':'12','PGRST_DB_POOL_ACQUISITION_TIMEOUT':'10'},'/',dependencies=['storage'],strip=True)
    services['postgrest']['health_url']='http://127.0.0.1:13003/ready'
    services['postgrest']['before_start']='schema'
    add('meta',18080,None,{'PG_META_PORT':'18080','PG_META_DB_HOST':host,'PG_META_DB_PORT':str(pg_port),
        'PG_META_DB_NAME':'postgres','PG_META_DB_USER':'supabase_admin','PG_META_DB_PASSWORD':s['POSTGRES_PASSWORD'],
        'CRYPTO_KEY':s['JWT_SECRET']},'/health')
    add('studio',13000,'/supabase/',{'HOSTNAME':'127.0.0.1','PORT':'13000','NEXT_PUBLIC_BASE_PATH':'/supabase',
        'STUDIO_PG_META_URL':'http://127.0.0.1:18080','POSTGRES_PASSWORD':s['POSTGRES_PASSWORD'],
        'POSTGRES_HOST':host,'POSTGRES_PORT':str(pg_port),'POSTGRES_DB':'postgres',
        'SUPABASE_URL':'http://127.0.0.1:8080/supabase','SUPABASE_PUBLIC_URL':public+'/supabase',
        'SUPABASE_ANON_KEY':s['ANON_KEY'],'SUPABASE_SERVICE_KEY':s['SERVICE_ROLE_KEY'],
        'AUTH_JWT_SECRET':s['JWT_SECRET'],'PG_META_CRYPTO_KEY':s['JWT_SECRET'],
        'DEFAULT_ORGANIZATION_NAME':'ANP','DEFAULT_PROJECT_NAME':'本地平台','PGRST_DB_SCHEMAS':'public,platform'},
        '/supabase/api/platform/profile',dependencies=['meta'])
    routes[-1]['basic_auth']=True
    services['studio']['execution']='native'
    services['studio']['command']=['node','apps/studio/server.js']
    add('dbx',14224,'/dbx/',{'DBX_PORT':'14224','DBX_PUBLIC_BASE_PATH':'/dbx','DBX_PASSWORD':s['DBX_PASSWORD'],
        'DBX_DATA_DIR':'/state/dbx','DBX_DISABLE_PASSWORD_VIEWER':'true'},'/dbx/api/auth/check')
    services['dbx']['configure']='dbx'
    dbx_connections=[]
    for db,user,key in [('postgres','supabase_admin','POSTGRES_PASSWORD'),('agent_local','agent','AGENT_DB_PASSWORD'),
        ('litellm','litellm','LITELLM_DB_PASSWORD'),('langfuse','langfuse','LANGFUSE_DB_PASSWORD')]:
        dbx_connections.append({'id':'anp-'+db,'name':'ANP / '+db,'db_type':'postgres','driver_profile':'postgres',
            'host':host,'port':pg_port,'database':db,'username':user,'password':s[key],
            'save_password':True,'show_database_links':True})
    for driver,server,port,db,user,key in [('redis','redis',redis_port,'0','default','REDIS_PASSWORD'),
        ('clickhouse','clickhouse',ch_port,'default','anp','CH_PASSWORD')]:
        dbx_connections.append({'id':'anp-'+driver,'name':'ANP / '+driver,'db_type':driver,'driver_profile':driver,
            'host':server,'port':port,'database':db,'username':user,'password':s[key],
            'save_password':True,'show_database_links':True})
    lf={'DATABASE_URL':dsn('langfuse','langfuse',s['LANGFUSE_DB_PASSWORD']),
        'SALT':s['LF_SALT'],'ENCRYPTION_KEY':hashlib.sha256(s['LF_ENCRYPTION'].encode()).hexdigest(),
        'NEXTAUTH_SECRET':s['LF_AUTH'],'NEXTAUTH_URL':public+'/langfuse','NEXT_PUBLIC_BASE_PATH':'/langfuse',
        'CLICKHOUSE_URL':f'http://clickhouse:{ch_port}','CLICKHOUSE_MIGRATION_URL':'clickhouse://clickhouse:9000',
        'CLICKHOUSE_USER':'anp','CLICKHOUSE_PASSWORD':s['CH_PASSWORD'],'CLICKHOUSE_DB':'default',
        'CLICKHOUSE_CLUSTER_ENABLED':'false',
        'REDIS_HOST':'redis','REDIS_PORT':str(redis_port),'REDIS_AUTH':s['REDIS_PASSWORD'],
        'TELEMETRY_ENABLED':'false','HOSTNAME':'127.0.0.1',
        'LANGFUSE_INIT_ORG_ID':'anp','LANGFUSE_INIT_PROJECT_ID':'anp','LANGFUSE_INIT_PROJECT_NAME':'业务运行',
        'LANGFUSE_INIT_PROJECT_PUBLIC_KEY':s['LANGFUSE_PUBLIC_KEY'],'LANGFUSE_INIT_PROJECT_SECRET_KEY':s['LANGFUSE_SECRET_KEY'],
        'LANGFUSE_INIT_USER_EMAIL':s['ADMIN_EMAIL'],'LANGFUSE_INIT_USER_PASSWORD':s['ADMIN_PASSWORD']}
    for kind in ['EVENT_UPLOAD','MEDIA_UPLOAD','BATCH_EXPORT']:
        for key,value in {'BUCKET':'langfuse','ENDPOINT':storage['s3_url'],'ACCESS_KEY_ID':s['S3_ACCESS_KEY'],
            'SECRET_ACCESS_KEY':s['S3_SECRET_KEY'],'REGION':'us-east-1','FORCE_PATH_STYLE':'true'}.items():
            lf[f'LANGFUSE_S3_{kind}_{key}']=value
    # 原生 ClickHouse 协议另走同一个服务的 9000 端口；隔离验收通过网络别名直连。
    if smoke:lf['CLICKHOUSE_MIGRATION_URL']='clickhouse://anp-storage-clickhouse-1:9000'
    add('langfuse-web',13001,'/langfuse/',{**lf,'PORT':'13001'},'/langfuse/api/public/health')
    add('langfuse-worker',13030,None,{**lf,'PORT':'13030'},'/api/health',dependencies=['langfuse-web'])
    for component,entry in [('langfuse-web','web'),('langfuse-worker','worker')]:
        services[component]['execution']='native'
        services[component]['native_loader']='/usr/lib64/ld-linux-x86-64.so.2'
        services[component]['native_prisma_engine']='rhel-openssl-3.0.x'
        services[component]['native_prisma_directory']='app/node_modules/.pnpm' if entry=='web' else 'app/worker/node_modules/.pnpm'
        services[component]['initialization_command']=['/bin/sh',f'./{entry}/entrypoint.sh','/bin/true']
        services[component]['command']=['node', 'web/server.js' if entry=='web' else 'worker/dist/index.js']
    add('lens',14318,'/lens/',{'LITELLM_LENS_LISTEN':'127.0.0.1:14318','LENS_ADMIN_TOKEN':s['LENS_ADMIN_TOKEN'],
        'LENS_PUBLIC_URL':public,'LITELLM_LENS_PUBLIC_URL':public+'/lens',
        'LITELLM_LENS_SERVICE_TOKEN':s['LITELLM_LENS_SERVICE_TOKEN'],'LENS_GATEWAY_SECRET':s['LENS_GATEWAY_SECRET'],
        'LENS_GATEWAY_URL':'http://127.0.0.1:14000','CLICKHOUSE_URL':f'http://anp:{s["CH_PASSWORD"]}@clickhouse:{ch_port}',
        'CLICKHOUSE_DATABASE':'lens','LENS_ANALYSIS_API_KEY':s['LENS_V3_ANALYSIS_KEY'],
        'LENS_ANALYSIS_MODELS':json.dumps([{'name':'auto','model':'auto','provider':'openai_compatible',
            'api_key_env':'LENS_ANALYSIS_API_KEY','api_base':'http://127.0.0.1:14000/v1',
            'input_cost_per_token':0,'output_cost_per_token':0,
            'output_limits':{'max_tokens':4096}}])},'/health/ready',strip=True)
    routes[-1]['rewrite_relative_redirects']=True
    add('litellm',14000,'/litellm/',{'DATABASE_URL':dsn('litellm','litellm',s['LITELLM_DB_PASSWORD']),
        'LITELLM_MASTER_KEY':s['LITELLM_KEY'],'STORE_MODEL_IN_DB':'True','SERVER_ROOT_PATH':'/litellm',
        'FREELLM_API_BASE':'http://24.199.104.17:3001/v1','FREELLM_API_KEY':s.get('FREELLM_API_KEY',''),
        'LITELLM_LENS_URL':'http://127.0.0.1:14318','LITELLM_LENS_PUBLIC_URL':public+'/lens',
        'LITELLM_LENS_SERVICE_TOKEN':s['LITELLM_LENS_SERVICE_TOKEN'],'LENS_GATEWAY_SECRET':s['LENS_GATEWAY_SECRET']},
        '/health/liveliness',dependencies=['lens'],strip=False,
        command=['litellm','--config','/app/config.yaml','--host','127.0.0.1','--port','14000'])
    services['litellm']['configure']='litellm'
    add('agent',18124,'/agent/',{
        'ANP_ROOT':'/opt/anp','ANP_APPLICATIONS_ROOT':'/opt/anp/extensions/apps',
        'ANP_WORKBENCH_WORKER_URL':'http://host.docker.internal:8282' if smoke else 'http://host.docker.internal:8281',
        'ANP_LANGFUSE_URL':'http://127.0.0.1:13001/langfuse','LANGFUSE_BASE_URL':'http://127.0.0.1:13001/langfuse',
        'LANGFUSE_PUBLIC_KEY':s['LANGFUSE_PUBLIC_KEY'],'LANGFUSE_SECRET_KEY':s['LANGFUSE_SECRET_KEY'],
        'ENGINE_TOKEN':s['ENGINE_TOKEN'],'WORKER_TOKEN':s['WORKER_TOKEN'],
        'LANGSMITH_TRACING':'false'},'/agent/ok',dependencies=['postgrest'])
    services['agent']['execution']='builtin'
    configuration={'port':8080,'public_url':public,'storage':storage,'services':services,'routes':routes,
        'agent_state_archive':'/run/anp/handoff/agent.tar','agent_state_directory':'/state/agent',
        'application_schemas':['app_'+app_id for app_id in applications()],
        'dbx_connections':dbx_connections,
        'bootstrap_admin':{'email':s['ADMIN_EMAIL'],'password':s['ADMIN_PASSWORD'],'service_key':s['SERVICE_ROLE_KEY']},
        'studio_basic_auth':{'username':'supabase','password':s['DASHBOARD_PASSWORD']},
        'files':{'litellm':{'/run/anp/litellm.yaml':'/app/config.yaml'}},
        'databases':{name:{'role':role,'password':s[key]} for name,role,key in [
            ('langfuse','langfuse','LANGFUSE_DB_PASSWORD'),('litellm','litellm','LITELLM_DB_PASSWORD'),
            ('agent_local','agent','AGENT_DB_PASSWORD')]}}
    document={}
    if deployment:
        document=tomllib.loads(Path(deployment).read_text(encoding='utf8'))
        bind(configuration,document,{**s,**os.environ},target)
    expanded=expand(document,{**s,**os.environ})
    business=expanded.get('business',{})
    api_port,worker_port,ui_port=(8181,8282,5174) if smoke else (8180,8281,5173)
    api=business.get('api_host_url',f'http://127.0.0.1:{api_port}')
    worker=business.get('worker_host_url',f'http://127.0.0.1:{worker_port}')
    ui=business.get('ui_host_url',f'http://127.0.0.1:{ui_port}')
    api_port,worker_port,ui_port=[urlsplit(url).port or (443 if urlsplit(url).scheme=='https' else 80) for url in [api,worker,ui]]
    services['agent']['environment']['ANP_WORKBENCH_WORKER_URL']=business.get('worker_container_url',f'http://host.docker.internal:{worker_port}')
    routes.extend([{'path':'/api/','target':business.get('api_container_url',f'http://host.docker.internal:{api_port}').removeprefix('http://')},
        {'path':'/workbench/','target':business.get('ui_container_url',f'http://host.docker.internal:{ui_port}').removeprefix('http://')}])
    configuration['application_routes']=[{'path':f'/{app_id}/','redirect':f'/workbench/apps/{app_id}'} for app_id in applications()]
    output=ROOT/'.local/generated/platform-v3';output.mkdir(parents=True,exist_ok=True)
    (output/'handoff').mkdir(exist_ok=True)
    host_pg=expanded.get('storage',{}).get('postgres_host_url') or dsn('postgres','supabase_admin',s['POSTGRES_PASSWORD']).replace('postgres:5432',f'127.0.0.1:{54323 if smoke else 54322}')
    agent_dsn=expanded.get('postgres',{}).get('urls',{}).get('agent') or relocate(dsn('agent_local','agent',s['AGENT_DB_PASSWORD']),host_pg)
    environment={'ANP_PROFILE':'v3','ANP_SUPABASE_URL':public+'/supabase','ANP_AGENT_URL':public+'/agent',
        'ANP_AGENT_SHARED':'true','ANP_LANGFUSE_URL':public+'/langfuse','ANP_LITELLM_URL':public+'/litellm/v1',
        'ANP_DBX_URL':public+'/dbx','ANP_WORKBENCH_API_URL':public,'ANP_WORKBENCH_WORKER_URL':worker,
        'ANP_WORKBENCH_WEB_URL':public+'/workbench/','ANP_DEVELOPMENT_API_PORT':str(api_port),
        'ANP_DEVELOPMENT_WORKER_PORT':str(worker_port),'ANP_UI_PORT':str(ui_port),'ANP_API_TARGET':api,
        'VITE_BASE_PATH':'/workbench/','VITE_PUBLIC_PORT':str(urlsplit(public).port or (443 if urlsplit(public).scheme=='https' else 80)),
        'ANP_POSTGRES_AGENT_DSN_ENV':'ANP_V3_AGENT_DSN','ANP_V3_AGENT_DSN':agent_dsn,
        'ANP_POSTGRES_MIGRATION_DSN_ENV':'ANP_V3_MIGRATION_DSN','ANP_V3_MIGRATION_DSN':host_pg}
    envfile(output/'business.env',environment)
    (output/'config.json').write_text(json.dumps(configuration,ensure_ascii=False,indent=2),encoding='utf8')
    model_config=yaml.safe_load((ROOT/'platform/config/litellm/models.yaml').read_text(encoding='utf8'))
    model_config['general_settings']['tracing']={'store':{'type':'lens'}}
    (output/'litellm.yaml').write_text(yaml.safe_dump(model_config,allow_unicode=True),encoding='utf8')
    credentials=ROOT/'config/local-credentials.md'
    start,end='<!-- anp-v3:start -->','<!-- anp-v3:end -->'
    block=f'''{start}
## V3 单容器集成（开发验证）

平台入口：{public}。当前仍需完成整体任务、F5 与发布验收。

| 系统 | 入口 | 本机登录信息 |
| --- | --- | --- |
| Supabase Studio | {public}/supabase/ | 用户名 `supabase`，密码 `{s['DASHBOARD_PASSWORD']}` |
| 业务认证 | {public}/supabase/auth/v1/ | 邮箱 `{s['ADMIN_EMAIL']}`，密码 `{s['ADMIN_PASSWORD']}` |
| Langfuse | {public}/langfuse/ | 邮箱 `{s['ADMIN_EMAIL']}`，密码 `{s['ADMIN_PASSWORD']}` |
| LiteLLM | {public}/litellm/ui/ | 用户名 `admin`，密码 `{s['LITELLM_KEY']}` |
| DBX | {public}/dbx/ | 密码 `{s['DBX_PASSWORD']}` |
| Lens 独立后台 | {public}/lens/ui/ | Admin Token `{s['LENS_ADMIN_TOKEN']}` |
| Agent API | {public}/agent/ | 请求头 `Authorization: Bearer {s['ENGINE_TOKEN']}` |
| LangGraph Studio | https://smith.langchain.com/studio/?baseUrl={quote(public+'/agent',safe='')} | 使用已有 LangSmith 账号；连接设置使用上行 Authorization 请求头 |

Lens 的受限分析 Key 为 `{s['LENS_V3_ANALYSIS_KEY']}`，只允许 auto；不要把管理 Token 当作模型网关 Key。
已有管理员不会被初始化程序重置密码；若曾主动改过密码，请使用改后的密码。
{end}'''
    text=credentials.read_text(encoding='utf8') if credentials.exists() else '# 本机登录信息\n'
    if start in text and end in text:
        text=text[:text.index(start)]+block+text[text.index(end)+len(end):]
    else:text=text.rstrip()+'\n\n'+block+'\n'
    credentials.write_text(text,encoding='utf8')
    print(json.dumps({'config':str(output/'config.json'),'services':len(services),'public_url':public},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-url',default='http://localhost:8080')
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--deployment',type=Path,help='存储/云资源绑定 TOML；秘密通过 ${变量名} 引用')
    parser.add_argument('--target',choices=['host','container'],default='container')
    options=parser.parse_args();generate(options.public_url,options.smoke,options.deployment,options.target)
