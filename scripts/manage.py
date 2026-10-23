"""ANP 快捷命令：只管理本工作区、固定项目及一个工具容器。"""
import argparse
import json
import os
import platform
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def read_env(path):
    if not Path(path).exists(): return {}
    values={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
        key,value=line.split('=',1); value=value.strip()
        if len(value)>1 and value[0] in ('"',"'") and value[-1]==value[0]: value=value[1:-1]
        values[key.strip()]=value
    return values


PROJECT=os.getenv('ANP_COMPOSE_PROJECT') or read_env(ROOT/'.env').get('ANP_COMPOSE_PROJECT','anp-next')


def run(args,cwd=ROOT,env=None):
    subprocess.run([str(arg) for arg in args],cwd=cwd,env=env,check=True)


def default_cpu_threads():
    try:
        result=subprocess.run(['docker','info','--format','{{.NCPU}}'],capture_output=True,text=True,check=True,timeout=5)
        return str(min(8,max(1,int(result.stdout.strip()))))
    except (OSError,ValueError,subprocess.SubprocessError):
        return '8'


def init():
    path=ROOT/'.env'; values=read_env(path)
    for name in ('POSTGRES_PASSWORD','REDIS_PASSWORD','CLICKHOUSE_PASSWORD','S3_SECRET_KEY','JWT_SECRET'):
        if not values.get(name): values[name]=secrets.token_hex(32)
    defaults={'ANP_IMAGE':'ghcr.io/100apps/anp:latest','ANP_SERVICES':'all','ANP_AUTO_DEPS':'1',
              'ANP_MEMORY_MB':'16384','ANP_CPU_THREADS':values.get('ANP_CPU_THREADS') or default_cpu_threads(),'ANP_PUBLIC_URL':'http://127.0.0.1:51760','BUSINESS_PORT':'20260',
              'ANP_DATA_ROOT':'/var/lib/anp-next/data' if os.name=='nt' else str(ROOT/'var/data'),
              'S3_ACCESS_KEY':'anpadmin','PG_PORT':'57432','REDIS_PORT':'57379','CH_PORT':'59223',
              'CH_NATIVE_PORT':'59400','S3_API_PORT':'59402','S3_CONSOLE_PORT':'59403','DBX_PORT':'54424',
              'LANGFUSE_PORT':'53400','LITELLM_PORT':'54400','STUDIO_PORT':'53401','SUPABASE_API_PORT':'58400','HEALTH_PORT':'57501'}
    if values.get('ANP_CONFIG_VERSION') not in (None,'1'):
        raise ValueError('不支持的ANP配置版本，请显式迁移配置')
    values.setdefault('ANP_CONFIG_VERSION','1')
    values.setdefault('ANP_WORKSPACE_ID',str(uuid4()))
    for name,value in defaults.items(): values.setdefault(name,value)
    path.write_text('\n'.join(f'{key}={value}' for key,value in values.items())+'\n',encoding='utf-8')
    for name in ('run','logs/dev','evidence','cache','exports'):
        (ROOT/'var'/name).mkdir(parents=True,exist_ok=True)
    business=ROOT/'business/.env'; local=read_env(business)
    local.setdefault('ANP_MODEL_MODE','demo'); local.setdefault('LANGSMITH_TRACING','false'); local.setdefault('BUSINESS_PORT',values['BUSINESS_PORT'])
    business.write_text('\n'.join(f'{key}={value}' for key,value in local.items())+'\n',encoding='utf-8')
    print('ANP 配置就绪；统一数据根：'+values['ANP_DATA_ROOT'],flush=True)


def ensure_owned_project(workspace_id):
    ids=subprocess.check_output(['docker','ps','-aq','--filter','label=com.docker.compose.project='+PROJECT],text=True).split()
    if not ids:return
    containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
    if any(c['Config'].get('Labels',{}).get('dev.anp.workspace')!=workspace_id for c in containers):
        raise RuntimeError('Compose实例 '+PROJECT+' 不属于本工作区，拒绝操作；请设置独立的ANP_COMPOSE_PROJECT')


def compose(*args,env=None):
    init()
    ensure_owned_project(read_env(ROOT/'.env')['ANP_WORKSPACE_ID'])
    run(['docker','compose','--project-name',PROJECT,'--project-directory',ROOT,'--env-file',ROOT/'.env',
         '-f',ROOT/'compose.yaml',*args],env=env)


def install():
    init(); run(['uv','sync','--project','business','--frozen'])
    run([shutil.which('pnpm.cmd') or shutil.which('pnpm') or 'pnpm','install','--frozen-lockfile'],ROOT/'business/ui')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command'); parser.add_argument('args',nargs='*'); parser.add_argument('--services')
    parser.add_argument('--platforms', default='linux/arm64' if platform.machine().lower() in ('arm64','aarch64') else 'linux/amd64')
    parser.add_argument('--image')
    parser.add_argument('--target',default='toolbox')
    parser.add_argument('--push',action='store_true')
    options=parser.parse_args(); command=options.command
    if command=='init': init()
    elif command=='install': install()
    elif command in ('up','storage'):
        env=os.environ.copy()
        if options.services or command=='storage': env['ANP_SERVICES']=options.services or 'storage'
        compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox',env=env)
    elif command=='build':
        init()
        if options.push and not options.image:raise ValueError('发布镜像必须显式指定IMAGE=仓库/镜像:标签')
        run([sys.executable,ROOT/'scripts/validate_sources.py'])
        image=options.image or 'anp/toolbox:local'
        if not re.fullmatch(r'[A-Za-z0-9_./:-]+',image): raise ValueError('镜像名称包含无效字符')
        builder=[]
        if ',' in options.platforms:
            name='anp-scaffold'
            if subprocess.run(['docker','buildx','inspect',name],capture_output=True).returncode:
                run(['docker','buildx','create','--name',name,'--driver','docker-container'])
            builder=['--builder',name]
        proxy_args=[]
        for key in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY'):
            if value:=os.getenv('ANP_BUILD_'+key):proxy_args.extend(['--build-arg',key+'='+value])
        run(['docker','buildx','build',*builder,*proxy_args,'--platform',options.platforms,'-f',ROOT/'docker/toolbox/Dockerfile',
             '--target',options.target,'--output',f'type=image,name={image},compression=zstd,compression-level=15,force-compression=true'+(',push=true' if options.push else ''),
             *options.args,ROOT])
    elif command=='pull': compose('pull','toolbox')
    elif command=='image-size':
        image=options.image or os.getenv('ANP_IMAGE') or read_env(ROOT/'.env').get('ANP_IMAGE','ghcr.io/100apps/anp:latest')
        run(['docker','image','ls','--filter','reference='+image])
    elif command=='restart': compose('restart','toolbox'); compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox')
    elif command=='down': compose('down')
    elif command in ('ps','logs'): compose(command,*options.args)
    elif command in ('dev','prepare','frontend','stop'):
        if command in ('dev','prepare'):
            install(); compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox')
        if command=='dev':
            run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/dev.py','stop'])
        if command!='prepare': run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/dev.py',command])
    elif command=='test':
        run(['uv','run','--project','business','--frozen','pytest','business/tests','tests/integration','tests/runtime','-q'])
        run([shutil.which('pnpm.cmd') or 'pnpm','build'],ROOT/'business/ui')
    elif command in ('check','smoke'):
        run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/check.py',*(['--health'] if command=='check' else []),*options.args])
    elif command=='acceptance': run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/acceptance.py',*options.args])
    elif command=='status': compose('exec','-T','toolbox','/usr/bin/python3.13','/opt/anp/runtime/supervisor.py','status')
    elif command=='config': compose('config','--quiet')
    else: parser.error('不支持的命令：'+command)


if __name__=='__main__': main()
