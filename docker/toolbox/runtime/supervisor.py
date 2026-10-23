"""单容器服务生命周期：固定命令、依赖就绪、独立 UID、信号退出及有限重试。"""
from collections import OrderedDict
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from settings import DATA, NAMES, PG, UIDS, atomic, definitions, prepare

GROUPS={'storage':['postgres','redis','clickhouse','rustfs'],
        'platform':[name for name in NAMES if name not in ('postgres','redis','clickhouse','rustfs')],
        'supabase':[name for name in NAMES if name.startswith('supabase-')],
        'langfuse':['langfuse-web','langfuse-worker'],'all':NAMES}
STOP=threading.Event()
STATE={'status':'starting','services':{},'selected':[]}
PROCESSES=OrderedDict()
LOGGERS={}
SECRET={}
LIMITS={}
ACTIVE_SERVICES={}
HTTP=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def emit(message,**data):
    print(json.dumps({'time':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'message':message,**data},ensure_ascii=False),flush=True)


def selection(text,services,auto=True):
    requested=[]
    for item in text.split(','):
        item=item.strip()
        if item in GROUPS:
            requested.extend(GROUPS[item])
        elif item in services:
            requested.append(item)
        else:
            raise ValueError('未知服务或服务组：'+item)
    ordered=[]
    visiting=set()
    def add(name):
        if name in ordered:
            return
        if name in visiting:
            raise ValueError('服务依赖形成循环')
        visiting.add(name)
        if auto:
            for dep in services[name]['deps']:
                add(dep)
        visiting.remove(name); ordered.append(name)
    for name in requested:
        add(name)
    return ordered


def environment(service):
    # 不将管理器收到的其他服务密码传给不相关的子进程。
    return {'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONUNBUFFERED':'1',
            'HOME':str(DATA/service['name']),**service['env']}


def log_output(name,stream):
    logger=LOGGERS.get(name)
    if not logger:
        logger=logging.getLogger('anp.'+name); logger.setLevel(logging.INFO); logger.propagate=False
        handler=RotatingFileHandler(DATA/'logs'/name/'current.log',maxBytes=int(LIMITS['log_max_mb'])*1048576,
                                    backupCount=int(LIMITS['log_backups']),encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s')); logger.addHandler(handler); LOGGERS[name]=logger
    for line in iter(stream.readline,b''):
        value=line.decode('utf-8',errors='replace').rstrip()
        for secret in SECRET.values():
            if len(secret)>12:
                value=value.replace(secret,'<已隐藏>')
        logger.info(value)
    stream.close()


def run_command(service,command,input=None):
    result=subprocess.run(command,input=input,text=True,cwd=service['cwd'],env=environment(service),
                          user=service['uid'],group=service['uid'],extra_groups=[],capture_output=True,timeout=180)
    if result.returncode:
        # 初始化 SQL/命令可能含凭据，完整 stderr 只写私有文件，不打印到健康接口。
        error=result.stderr
        for value in SECRET.values():
            if len(value)>12:
                error=error.replace(value,'<已隐藏>')
        atomic(DATA/'logs'/service['name']/'initialization-error.log',error)
        raise RuntimeError(service['name']+' 初始化命令失败，详见该服务私有日志')
    return result.stdout


def initialize_postgres(service):
    path=DATA/'postgres/PG_VERSION'
    if path.exists():
        if path.read_text().strip()!='17':
            raise RuntimeError('数据目录不是 PostgreSQL 17，必须先执行显式主版本迁移')
        return
    run_command(service,[PG+'initdb','-D',str(DATA/'postgres'),'-U','postgres','--encoding=UTF8',
                         '--locale=C.UTF-8','--auth-local=peer','--auth-host=scram-sha-256',
                         '--pwfile='+str(DATA/'config/effective/postgres.password')])
    emit('PostgreSQL 独立数据目录已初始化')


def bootstrap_postgres(service):
    def literal(value):
        return "'"+value.replace("'","''")+"'"
    roles={'langfuse':'LANGFUSE_DB_PASSWORD','litellm':'LITELLM_DB_PASSWORD',
           'supabase_auth_admin':'AUTH_DB_PASSWORD','supabase_storage_admin':'STORAGE_DB_PASSWORD',
           'authenticator':'REST_DB_PASSWORD','anp_studio_reader':'POSTGRES_PASSWORD','supabase_admin':'REALTIME_DB_PASSWORD'}
    sql=''
    for role,key in roles.items():
        sql+=f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} LOGIN; END IF; END $$;\n"
        sql+=f'ALTER ROLE {role} PASSWORD {literal(SECRET[key])};\n'
    for role in ('anon','authenticated','service_role','dashboard_user'):
        sql+=f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF; END $$;\n"
    sql+='ALTER ROLE supabase_admin LOGIN SUPERUSER REPLICATION;\n'
    # PG17 的 GRANTED BY 要求指定授予者自身持有 ADMIN OPTION。
    sql+='GRANT anon,authenticated,service_role TO supabase_admin WITH ADMIN OPTION;\n'
    sql+='CREATE SCHEMA IF NOT EXISTS _realtime AUTHORIZATION supabase_admin;\n'
    sql+="DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_publication WHERE pubname='supabase_realtime') THEN CREATE PUBLICATION supabase_realtime; END IF; END $$;\n"
    sql+='ALTER ROLE service_role BYPASSRLS; GRANT anon,authenticated,service_role TO authenticator,supabase_storage_admin;\n'
    sql+='GRANT pg_read_all_data,pg_read_all_stats TO anp_studio_reader; ALTER ROLE anp_studio_reader SET default_transaction_read_only=on;\n'
    sql+='CREATE SCHEMA IF NOT EXISTS extensions; CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;\n'
    sql+='CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA extensions; CREATE EXTENSION IF NOT EXISTS pg_stat_statements;\n'
    sql+='CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION supabase_auth_admin; CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION supabase_storage_admin;\n'
    sql+='GRANT USAGE ON SCHEMA auth,extensions TO authenticated,anon,service_role,supabase_storage_admin;\n'
    sql+='GRANT USAGE ON SCHEMA storage TO authenticated,anon,service_role;\n'
    sql+='GRANT CREATE ON DATABASE postgres TO supabase_auth_admin,supabase_storage_admin;\n'
    sql+='ALTER ROLE supabase_auth_admin SET search_path=auth,public,extensions;\n'
    sql+='ALTER ROLE supabase_storage_admin SET search_path=storage,public,extensions;\n'
    sql+="CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS 'SELECT coalesce(nullif(current_setting(''request.jwt.claim.sub'',true),''''),current_setting(''request.jwt.claims'',true)::jsonb->>''sub'')::uuid';\n"
    sql+="CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS 'SELECT coalesce(nullif(current_setting(''request.jwt.claim.role'',true),''''),current_setting(''request.jwt.claims'',true)::jsonb->>''role'')';\n"
    sql+="CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS 'SELECT coalesce(nullif(current_setting(''request.jwt.claims'',true),''''),''{}'')::jsonb';\n"
    # Auth 首次迁移会替换这些函数，必须由迁移角色拥有它们。
    for name in ('uid','role','jwt'):
        sql+=f'GRANT EXECUTE ON FUNCTION auth.{name}() TO authenticated,anon,service_role;\n'
        sql+=f'ALTER FUNCTION auth.{name}() OWNER TO supabase_auth_admin;\n'
    for database in ('langfuse','litellm'):
        sql+=f"SELECT 'CREATE DATABASE {database} OWNER {database}' WHERE NOT EXISTS(SELECT FROM pg_database WHERE datname='{database}')\\gexec\n"
        sql+=f'REVOKE ALL ON DATABASE {database} FROM PUBLIC;\n'
    local={**service,'env':{**service['env'],'PGPASSWORD':SECRET['POSTGRES_PASSWORD']}}
    run_command(local,[PG+'psql','-X','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U','postgres','-d','postgres'],input=sql)
    emit('平台独立数据库、角色与基础扩展已初始化')


def bootstrap_auth_permissions():
    if 'postgres' not in STATE['selected']:
        return
    service=ACTIVE_SERVICES['postgres']
    local={**service,'env':{**service['env'],'PGPASSWORD':SECRET['POSTGRES_PASSWORD']}}
    sql='GRANT SELECT,REFERENCES ON TABLE auth.users TO supabase_storage_admin;\n'
    run_command(local,[PG+'psql','-X','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U','postgres','-d','postgres'],input=sql)


def bootstrap_s3():
    sys.path[:0]=glob.glob('/app/.venv/lib/python*/site-packages')
    import boto3
    from botocore.config import Config
    client=boto3.client('s3',endpoint_url='http://127.0.0.1:9002',region_name='us-east-1',
                        aws_access_key_id=SECRET['S3_ACCESS_KEY'],aws_secret_access_key=SECRET['S3_SECRET_KEY'],
                        config=Config(signature_version='s3v4',connect_timeout=3,read_timeout=5,retries={'max_attempts':1}))
    for attempt in range(30):
        try:
            names={item['Name'] for item in client.list_buckets()['Buckets']}
            for bucket in ('langfuse','supabase'):
                if bucket not in names:
                    client.create_bucket(Bucket=bucket)
            emit('独立 S3 桶已就绪'); return
        except Exception:
            if STOP.wait(1):
                raise RuntimeError('初始化已取消')
    raise RuntimeError('RustFS S3 API 尚未就绪')


def bootstrap_storage_permissions():
    if 'postgres' not in STATE['selected']: return
    service=ACTIVE_SERVICES['postgres']
    local={**service,'env':{**service['env'],'PGPASSWORD':SECRET['POSTGRES_PASSWORD']}}
    # 业务读写仍受上游已启用的RLS控制；不给anon/authenticated管理迁移表的权限。
    sql='ALTER TABLE storage.buckets ENABLE ROW LEVEL SECURITY;\n'
    sql+='GRANT SELECT,INSERT,UPDATE,DELETE ON storage.buckets,storage.objects TO anon,authenticated,service_role;\n'
    run_command(local,[PG+'psql','-X','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-U','postgres','-d','postgres'],input=sql)


def probe(service):
    try:
        if service['name']=='postgres':
            return subprocess.run([PG+'pg_isready','-h','127.0.0.1','-U','postgres'],
                                  env=environment(service),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                  timeout=3).returncode==0
        if service['name']=='supabase-realtime' and not (DATA/'run/supabase-realtime/initialized').exists():
            return False
        if service.get('health'):
            headers={'X-ClickHouse-User':'anp','X-ClickHouse-Key':SECRET['CLICKHOUSE_PASSWORD']} if service['name']=='clickhouse' else {}
            if service['name']=='supabase-realtime': headers={'Authorization':'Bearer '+SECRET['ANON_KEY']}
            request=urllib.request.Request(f"http://127.0.0.1:{service['port']}{service['health']}",headers=headers)
            with HTTP.open(request,timeout=3) as r:
                return r.status<400
        with socket.create_connection(('127.0.0.1',service['port']),timeout=2):
            return True
    except (OSError,urllib.error.URLError):
        return False


def start(service):
    name=service['name']
    if name=='postgres':
        initialize_postgres(service)
    proc=subprocess.Popen(service['command'],cwd=service['cwd'],env=environment(service),
                          user=service['uid'],group=service['uid'],extra_groups=[],start_new_session=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    PROCESSES[name]=proc
    threading.Thread(target=log_output,args=(name,proc.stdout),daemon=True).start()
    STATE['services'][name]={'state':'starting','pid':proc.pid,'port':service['port']}
    emit('启动服务',service=name,pid=proc.pid)
    deadline=time.monotonic()+service['timeout']
    while time.monotonic()<deadline and not STOP.is_set():
        if proc.poll() is not None:
            raise RuntimeError(name+f' 启动退出，代码 {proc.returncode}；详见 /data/logs/{name}/current.log')
        if probe(service):
            if name=='postgres':
                bootstrap_postgres(service)
            if name=='rustfs':
                bootstrap_s3()
            if name=='supabase-auth':
                bootstrap_auth_permissions()
            if name=='supabase-storage':
                bootstrap_storage_permissions()
            STATE['services'][name]['state']='ready'
            emit('服务就绪',service=name); return
        STOP.wait(1)
    raise RuntimeError(name+' 启动超时或已取消')


def status_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            if self.path not in ('/health','/status'):
                self.send_error(404); return
            data=json.dumps(STATE,ensure_ascii=False).encode()
            self.send_response(200 if STATE['status']=='ready' else 503)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    server=ThreadingHTTPServer(('0.0.0.0',9750),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server


def shutdown():
    STATE['status']='stopping'
    # 先结束全部平台，再关闭存储；阶段共享截止时间，避免累加超出容器宽限期。
    storage={'postgres','clickhouse','rustfs','redis'}
    for selected,budget in ((False,30),(True,60)):
        processes=[(name,proc) for name,proc in reversed(PROCESSES.items()) if (name in storage)==selected]
        deadline=time.monotonic()+budget
        for name,proc in processes:
            if proc.poll() is not None: continue
            emit('停止服务',service=name)
            try:
                os.killpg(proc.pid,signal.SIGINT if name=='postgres' else signal.SIGTERM)
            except ProcessLookupError: pass
        for name,proc in processes:
            if proc.poll() is None:
                try: proc.wait(timeout=max(0.1,deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    try: os.killpg(proc.pid,signal.SIGKILL); proc.wait(timeout=5)
                    except ProcessLookupError: pass
            STATE['services'].get(name,{})['state']='stopped'


def main():
    global SECRET,LIMITS,ACTIVE_SERVICES
    SECRET,LIMITS=prepare()
    services=definitions(SECRET,LIMITS)
    ACTIVE_SERVICES=services
    ordered=selection(os.getenv('ANP_SERVICES','all'),services,os.getenv('ANP_AUTO_DEPS','1')!='0')
    STATE['selected']=ordered
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_: STOP.set())
    server=status_server()
    failed=False
    try:
        retries={name:0 for name in ordered}
        def attempt(name):
            retries[name]+=1
            try:
                start(services[name])
            except Exception as error:
                proc=PROCESSES.get(name)
                if proc and proc.poll() is None:
                    try:
                        os.killpg(proc.pid,signal.SIGTERM); proc.wait(timeout=10)
                    except (ProcessLookupError,subprocess.TimeoutExpired):
                        if proc.poll() is None: os.killpg(proc.pid,signal.SIGKILL); proc.wait(timeout=5)
                STATE['services'][name]={'state':'failed','attempts':retries[name],'detail':str(error)}
                emit('服务启动失败',service=name,attempt=retries[name],detail=str(error))
        while not STOP.is_set():
            for name in ordered:
                if STOP.is_set(): break
                proc=PROCESSES.get(name)
                dependencies=services[name]['deps'] if os.getenv('ANP_AUTO_DEPS','1')!='0' else []
                if proc is None or proc.poll() is not None:
                    if retries[name]>=3: continue
                    if not all(STATE['services'].get(dep,{}).get('state')=='ready' for dep in dependencies):
                        STATE['services'][name]={'state':'blocked','dependencies':dependencies}; continue
                    attempt(name)
                else:
                    STATE['services'][name]['state']='ready' if probe(services[name]) else 'unhealthy'
            STATE['status']='ready' if len(STATE['services'])==len(ordered) and all(v['state']=='ready' for v in STATE['services'].values()) else 'unhealthy'
            atomic(DATA/'run/status.json',json.dumps(STATE,ensure_ascii=False,indent=2)+'\n',mode=0o644)
            STOP.wait(5)
    except Exception as error:
        STATE['status']='failed'; failed=True
        emit('服务管理失败',detail=str(error)); STOP.set()
    finally:
        shutdown(); server.shutdown()
    return 1 if failed else 0


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] in ('check','status'):
        try:
            with HTTP.open('http://127.0.0.1:9750/health',timeout=3) as response:
                if sys.argv[1]=='status':
                    print(response.read().decode())
            raise SystemExit(0)
        except urllib.error.HTTPError as error:
            if sys.argv[1]=='status': print(error.read().decode())
            raise SystemExit(1)
        except (OSError,urllib.error.URLError):
            raise SystemExit(1)
    raise SystemExit(main())
