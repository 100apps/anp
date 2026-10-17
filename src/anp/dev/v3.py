"""V3 开发编排：四存储、单平台与本机业务，严格保留旧 anp2。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
from contextlib import redirect_stdout
import io
import hashlib
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values,load_dotenv
from anp import ROOT
from anp.management.platform import ensure_secrets,envfile

GENERATED=ROOT/'.local/generated/platform-v3'


def command(arguments,**options):
    return subprocess.run(arguments,cwd=ROOT,check=True,**options)


def document():
    file=ROOT/'config/deployment.local.toml'
    if not file.exists():shutil.copy(ROOT/'config/deployment.example.toml',file)
    return tomllib.loads(file.read_text(encoding='utf8'))


def initialize():
    secrets=ensure_secrets();deployment=document()
    public=deployment['platform']['url'].rstrip('/')
    sys.path.insert(0,str(ROOT/'scripts'))
    from configure_platform import generate
    from platform_bindings import expand
    with redirect_stdout(io.StringIO()):generate(public,deployment=ROOT/'config/deployment.local.toml')
    storage=expand(deployment,secrets)['storage']
    ports={name:urlsplit(storage[name+'_host_url']).port for name in ['postgres','redis','clickhouse','s3']}
    prefix=storage.get('volume_prefix','anp_v3')
    environment={**secrets,'ANP_STORAGE_PREFIX':prefix,'ANP_PG_PORT':str(ports['postgres'] or 5432),
        'ANP_REDIS_PORT':str(ports['redis'] or 6379),'ANP_CH_PORT':str(ports['clickhouse'] or 8123),
        'ANP_CH_NATIVE_PORT':str(urlsplit(storage.get('clickhouse_host_migration_url','clickhouse://localhost:19000')).port or 19000),
        'ANP_S3_PORT':str(ports['s3'] or 9000),'ANP_S3_CONSOLE_PORT':str(storage.get('s3_console_port',(ports['s3'] or 9000)+1)),
        'ANP_PLATFORM_PORT':str(urlsplit(public).port or 8080),
        'ANP_PLATFORM_IMAGE':deployment['platform']['image'],'ANP_STORAGE_NETWORK':'anp-storage_default',
        'ANP_PLATFORM_CONFIG_REVISION':hashlib.sha256((GENERATED/'config.json').read_bytes()+(GENERATED/'litellm.yaml').read_bytes()).hexdigest(),
        'ANP_PLATFORM_STATE_VOLUME':prefix+'_platform_state','ANP_AGENT_STATE_VOLUME':prefix+'_agent_state'}
    envfile(GENERATED/'storage.env',environment)
    return deployment


def compose(layer,*arguments):
    return command(['docker','compose','--env-file',str(GENERATED/'storage.env'),'-p','anp-'+layer,
        '-f',str(ROOT/'deploy'/layer/'compose.yaml'),*arguments],stdout=subprocess.DEVNULL)


def wait_health(url,timeout=180):
    deadline=time.monotonic()+timeout
    with httpx.Client(trust_env=False,timeout=4) as client:
        while time.monotonic()<deadline:
            try:
                if client.get(url).status_code==200:return
            except httpx.HTTPError:pass
            time.sleep(.5)
    raise RuntimeError('等待服务就绪超时：'+url)


def stop_native():
    if sys.platform!='win32':return
    try:available=subprocess.run(['wsl','--list','--quiet'],capture_output=True)
    except FileNotFoundError:return
    distro=document().get('development',{}).get('distro','Ubuntu')
    names=available.stdout.decode('utf-16-le',errors='replace').replace('\x00','').splitlines()
    if available.returncode or distro not in [name.strip('\ufeff \r\n') for name in names]:return
    workspace=subprocess.check_output(['wsl','-d',distro,'--exec','wslpath','-u',str(ROOT)],text=True).strip()
    source='''import json,os,signal,time,sys
from pathlib import Path
marker=Path('/run/anp/native.pid.json')
if marker.exists():
 identity=json.loads(marker.read_text());pid=identity['pid'];cmd=Path('/proc/'+str(pid)+'/cmdline')
 if cmd.exists():
  if identity.get('workspace')!=sys.argv[1]:raise SystemExit('原生平台属于其他工作区，拒绝停止')
  assert b'anp_platform.native' in cmd.read_bytes(),'原生进程身份不符'
  os.kill(pid,signal.SIGTERM)
  deadline=time.monotonic()+45
  while cmd.exists() and time.monotonic()<deadline:time.sleep(.2)
  if cmd.exists():raise SystemExit('原生平台未完成退出，不启动另一平台')
'''
    command(['wsl','-d',distro,'-u','root','--exec','python3','-c',source,workspace])


def stop_docker():
    inspection=subprocess.run(['docker','inspect','--format','{{.State.Running}}','anp-platform-platform-1'],
        capture_output=True,text=True)
    if inspection.returncode or inspection.stdout.strip()!='true':return
    compose('platform','stop')
    # 首次从旧 V3 制品切换也保存同一 Agent 卷；只读停止后的容器。
    archive=GENERATED/'handoff/agent.tar';temporary=archive.with_suffix('.export.tar')
    with temporary.open('wb') as output:
        command(['docker','cp','anp-platform-platform-1:/state/agent/.langgraph_api','-'],stdout=output)
    os.replace(temporary,archive)


def prepare(mode):
    deployment=initialize()
    profiles=[]
    for service in deployment['storage'].get('local',[]):
        if service not in ('postgres','redis','clickhouse','rustfs'):raise ValueError('本地存储类型无效')
        profiles+=['--profile',service]
    if profiles:compose('storage',*profiles,'up','-d','--wait','--wait-timeout','180')
    if mode=='docker':
        stop_native()
        network=subprocess.run(['docker','network','inspect','anp-storage_default'],capture_output=True)
        if network.returncode:command(['docker','network','create','anp-storage_default'],stdout=subprocess.DEVNULL)
        compose('platform','up','-d','--wait','--wait-timeout','180')
        wait_health(deployment['platform']['url']+'/_platform/health')
    else:
        stop_docker()
        stop_native()
        sys.path.insert(0,str(ROOT/'scripts'))
        from prepare_native import prepare as artifacts
        from configure_native import configure
        distro=deployment.get('development',{}).get('distro','Ubuntu')
        artifacts(deployment['platform']['image'],distro)
        configure(distro,deployment=ROOT/'config/deployment.local.toml')
    from anp.applications.generator import sync
    sync()
    print('ANP_V3_PREPARED',mode,flush=True)


def environment():
    load_dotenv(GENERATED/'business.env',override=True)
    from anp.config import settings
    settings.cache_clear()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','prepare','api','worker','web','wait','native','stop-native'])
    parser.add_argument('--mode',choices=['docker','native'],default='docker')
    args=parser.parse_args()
    if args.action=='init':initialize();return
    if args.action=='prepare':prepare(args.mode);return
    if args.action=='stop-native':stop_native();return
    environment()
    from anp.config import value
    if args.action=='native':
        command([sys.executable,str(ROOT/'scripts/run_native.py'),'--reload','--debug-port','5684','--wait-for-client']);return
    wait_health(value('supabase','url').removesuffix('/supabase')+'/_platform/health')
    if args.action in ('api','worker'):
        if args.action=='api':
            from anp.management.applications import migrate_applications
            migrate_applications()
        from anp.dev.supervisor import main as serve
        sys.argv=[sys.argv[0],args.action];serve()
    elif args.action=='web':
        from anp.dev.frontend import main as frontend
        frontend()
    else:
        for key in ('api','worker'):
            wait_health('http://127.0.0.1:'+str(value('development',key+'_port'))+'/health')
        wait_health(value('workbench','web_url'))
        print('ANP_V3_BUSINESS_READY',flush=True)


if __name__=='__main__':main()
