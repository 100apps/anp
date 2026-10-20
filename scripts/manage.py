"""ANP3 快捷命令：只管理本工作区、固定项目及一个工具容器。"""
import argparse
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
PROJECT='anp3'


def read_env(path):
    if not Path(path).exists(): return {}
    values={}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
        key,value=line.split('=',1); value=value.strip()
        if len(value)>1 and value[0] in ('"',"'") and value[-1]==value[0]: value=value[1:-1]
        values[key.strip()]=value
    return values


def run(args,cwd=ROOT,env=None):
    subprocess.run([str(arg) for arg in args],cwd=cwd,env=env,check=True)


def init():
    path=ROOT/'.env'; values=read_env(path)
    for name in ('POSTGRES_PASSWORD','REDIS_PASSWORD','CLICKHOUSE_PASSWORD','S3_SECRET_KEY','JWT_SECRET'):
        if not values.get(name): values[name]=secrets.token_hex(32)
    defaults={'ANP3_IMAGE':'anp3/toolbox:local','ANP3_SERVICES':'all','ANP3_AUTO_DEPS':'1',
              'ANP3_MEMORY_MB':'16384','ANP3_CPU_THREADS':'8','ANP3_PUBLIC_URL':'http://127.0.0.1:51760','BUSINESS_PORT':'20260',
              'ANP_DATA_ROOT':'/var/lib/anp3/data' if os.name=='nt' else str(ROOT/'var/data'),
              'S3_ACCESS_KEY':'anp3admin','PG_PORT':'57432','REDIS_PORT':'57379','CH_PORT':'59223',
              'CH_NATIVE_PORT':'59400','S3_API_PORT':'59402','S3_CONSOLE_PORT':'59403','DBX_PORT':'54424',
              'LANGFUSE_PORT':'53400','LITELLM_PORT':'54400','STUDIO_PORT':'53401','SUPABASE_API_PORT':'58400','HEALTH_PORT':'57501'}
    if values.get('ANP3_CONFIG_VERSION') not in (None,'1'):
        raise ValueError('不支持的ANP3配置版本，请显式迁移配置')
    values.setdefault('ANP3_CONFIG_VERSION','1')
    for name,value in defaults.items(): values.setdefault(name,value)
    path.write_text('\n'.join(f'{key}={value}' for key,value in values.items())+'\n',encoding='utf-8')
    for name in ('run','logs/dev','evidence','dubbing/inputs','config/dubbing'):
        (ROOT/'var'/name).mkdir(parents=True,exist_ok=True)
    business=ROOT/'business/.env'; local=read_env(business)
    local.setdefault('ANP_MODEL_MODE','demo'); local['LANGSMITH_TRACING']='false'; local['BUSINESS_PORT']='20260'
    local.setdefault('ANP_DUB_DATABASE_URL',f"postgresql://postgres:{values['POSTGRES_PASSWORD']}@127.0.0.1:{values['PG_PORT']}/postgres")
    local.setdefault('ANP_DUB_ACTOR_ID',str(uuid4())); local.setdefault('ANP_DUB_WORKSPACE_ID',str(uuid4()))
    local.setdefault('ANP_DUB_ROOT',(ROOT/'var/dubbing').as_posix())
    local.setdefault('ANP_DUB_INPUT_ROOTS',(ROOT/'var/dubbing/inputs').as_posix())
    local.setdefault('ANP_DUB_S3_ENDPOINT',f"http://127.0.0.1:{values['S3_API_PORT']}")
    local.setdefault('ANP_DUB_S3_ACCESS_KEY',values['S3_ACCESS_KEY']); local.setdefault('ANP_DUB_S3_SECRET_KEY',values['S3_SECRET_KEY'])
    local.setdefault('ANP_DUB_S3_BUCKET','anp3-dubbing')
    business.write_text('\n'.join(f'{key}={value}' for key,value in local.items())+'\n',encoding='utf-8')
    print('ANP3 配置就绪；统一数据根：'+values['ANP_DATA_ROOT'],flush=True)


def compose(*args,env=None):
    init()
    run(['docker','compose','--project-name',PROJECT,'--project-directory',ROOT,'--env-file',ROOT/'.env',
         '-f',ROOT/'compose.yaml',*args],env=env)


def install():
    init(); run(['uv','sync','--project','business','--frozen'])
    run([shutil.which('pnpm.cmd') or shutil.which('pnpm') or 'pnpm','install','--frozen-lockfile'],ROOT/'business/ui')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command'); parser.add_argument('args',nargs='*'); parser.add_argument('--services')
    options=parser.parse_args(); command=options.command
    if command=='init': init()
    elif command=='install': install()
    elif command in ('up','storage'):
        env=os.environ.copy()
        if options.services or command=='storage': env['ANP3_SERVICES']=options.services or 'storage'
        compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox',env=env)
    elif command=='build':
        init()
        image=read_env(ROOT/'.env').get('ANP3_IMAGE','anp3/toolbox:local')
        if not re.fullmatch(r'[A-Za-z0-9_./:-]+',image): raise ValueError('镜像名称包含无效字符')
        run(['docker','buildx','build','--platform','linux/amd64','-f',ROOT/'docker/toolbox/Dockerfile',
             '--target','toolbox','--output',f'type=image,name={image},compression=zstd,compression-level=15,force-compression=true',
             *options.args,ROOT])
    elif command=='build-components':
        init(); run([sys.executable,ROOT/'scripts/source_recipes.py'])
        run(['docker','compose','-p',PROJECT,'--project-directory',ROOT,'-f',ROOT/'compose.build.yaml','build',*options.args])
    elif command=='restart': compose('restart','toolbox'); compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox')
    elif command=='down': compose('down')
    elif command in ('ps','logs'): compose(command,*options.args)
    elif command in ('dev','prepare','frontend','stop'):
        if command in ('dev','prepare'):
            install(); compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox')
            run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/dubbing.py','migrate'])
        if command!='prepare': run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/dev.py',command])
    elif command=='test':
        run(['uv','run','--project','business','--frozen','pytest','business/tests','tests/integration','tests/runtime','-q'])
        run([shutil.which('pnpm.cmd') or 'pnpm','build'],ROOT/'business/ui')
    elif command in ('check','smoke'):
        run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/check.py',*(['--health'] if command=='check' else []),*options.args])
    elif command=='acceptance': run(['uv','run','--project','business','--frozen','python',ROOT/'scripts/acceptance.py',*options.args])
    elif command=='status': compose('exec','-T','toolbox','/usr/bin/python3.13','/opt/anp3/runtime/supervisor.py','status')
    elif command=='config': compose('config','--quiet')
    else: parser.error('不支持的命令：'+command)


if __name__=='__main__': main()
