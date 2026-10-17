"""生成 WSL 原生平台配置；沿用同一存储实例与业务接口。"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib
from urllib.parse import quote,urlsplit
from dotenv import dotenv_values
from anp import ROOT
from platform_bindings import bind


def configure(distro='Ubuntu',smoke=False,deployment=None):
    directory=ROOT/'.local/generated/platform-v3'
    settings=json.loads((directory/'config.json').read_text(encoding='utf8'))
    secrets={**dict(dotenv_values(ROOT/'config/secrets.local.env')),**os.environ}
    project=subprocess.check_output(['wsl','-d',distro,'--exec','wslpath','-u',str(ROOT)],text=True).strip()
    pg,redis,ch,s3=(54323,6381,8125,9002) if smoke else (54322,6379,8123,9000)
    resources={'storage':{
        'postgres_host_url':f'postgresql://supabase_admin:{quote(secrets["POSTGRES_PASSWORD"],safe="")}@127.0.0.1:{pg}/postgres',
        'redis_host_url':f'redis://:{quote(secrets["REDIS_PASSWORD"],safe="")}@127.0.0.1:{redis}/0',
        'clickhouse_host_url':f'http://anp:{quote(secrets["CH_PASSWORD"],safe="")}@127.0.0.1:{ch}',
        'clickhouse_host_migration_url':'clickhouse://127.0.0.1:19000',
        's3_host_url':f'http://127.0.0.1:{s3}'}}
    document=tomllib.loads(Path(deployment).read_text(encoding='utf8')) if deployment else {}
    resources['storage'].update(document.get('storage',{}))
    resources.update({key:value for key,value in document.items() if key!='storage'})
    bind(settings,resources,secrets,'host')
    python='/opt/anp/python/bin/python3.12'
    path=f'{project}/platform/runtime:{project}/src:{project}:/opt/anp/.venv/lib/python3.12/site-packages'
    environment={'ANP_ROOT':project,'PYTHONHOME':'/opt/anp/python','LD_LIBRARY_PATH':'/opt/anp/python/lib',
        'PYTHONPATH':path,'ANP_APPLICATIONS_ROOT':project+'/apps','ANP_AGENT_STATE':'/opt/anp/state/agent'}
    script=str(ROOT/'src/anp/dev/stdio_bridge.py').replace('\\','/')
    windows_python=project+'/.venv/Scripts/python.exe'
    api,worker,ui=(8181,8282,5174) if smoke else (8180,8281,5173)
    business=document.get('business',{})
    api,worker,ui=[urlsplit(business.get(key,f'http://127.0.0.1:{port}')).port or 80
        for key,port in [('api_host_url',api),('worker_host_url',worker),('ui_host_url',ui)]]
    # env 是传输进程的启动边界，避免 Linux debugpy 给 Windows Python 注入 Linux 路径。
    # 业务 API/Worker 由 IDE 单独调试，不通过这条二进制数据通道启动调试器。
    settings['host_bridges']=[{'port':local,'command':['/usr/bin/env',windows_python,'-E',script,str(remote)]}
        for local,remote in [(19081,api),(19082,worker),(19574,ui)]]
    for route in settings['routes']:
        if route['path']=='/api/':route['target']='127.0.0.1:19081'
        if route['path']=='/workbench/':route['target']='127.0.0.1:19574'
    agent=settings['services']['agent']
    agent['command']=[python,'-m','anp_platform.agent']
    agent['environment'].update(environment,ANP_WORKBENCH_WORKER_URL='http://127.0.0.1:19082')
    settings['port']=urlsplit(settings['public_url']).port or 8080
    settings['listen_addresses']=['127.0.0.1','::1']
    settings['files']['litellm']={project+'/.local/generated/platform-v3/litellm.yaml':'/app/config.yaml'}
    settings['agent_state_archive']=project+'/.local/generated/platform-v3/handoff/agent.tar'
    settings['agent_state_directory']='/opt/anp/state/agent'
    native=directory/'native.json';native.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf8')
    metadata=json.loads(subprocess.check_output(['wsl','-d',distro,'-u','root','--exec','cat','/opt/anp/components.json'],text=True))
    metadata['components']['agent']['working_dir']='/opt/anp/state/agent'
    writer='from pathlib import Path;import sys;Path("/opt/anp/components.native.json").write_bytes(sys.stdin.buffer.read());Path("/opt/anp/state/agent").mkdir(parents=True,exist_ok=True)'
    subprocess.run(['wsl','-d',distro,'-u','root','--exec','python3','-c',writer],input=json.dumps(metadata).encode(),check=True)
    environment.update(ANP_PLATFORM_CONFIG=project+'/.local/generated/platform-v3/native.json',
        ANP_COMPONENTS='/opt/anp/components.native.json',ANP_NGINX_BINARY='/opt/anp/bin/nginx',
        ANP_NGINX_MIME_TYPES='/opt/anp/etc/mime.types',ANP_PLATFORM_MIGRATIONS=project+'/migrations')
    (directory/'native-launch.json').write_text(json.dumps({'distro':distro,'python':python,'environment':environment},indent=2),encoding='utf8')
    return {'configuration':str(native),'listen_port':settings['port'],'windows_callbacks':'stdio'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--distro',default='Ubuntu');parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--deployment',type=Path)
    args=parser.parse_args();print(json.dumps(configure(args.distro,args.smoke,args.deployment),ensure_ascii=False))
