from pathlib import Path
import asyncio
import os
import socket
import subprocess
import sys
import time
import pytest
import httpx
from anp import ROOT
from anp.config import secret,value
from anp.management.bootstrap import create_user,DEFAULT_WORKSPACE
from anp.sdk.objects import Objects
from uuid import uuid4


@pytest.fixture(scope='session')
def identities():
    async def prepare():
        result={}
        for role in ('developer','viewer'):
            email=f'test-{role}@anp.local'
            password='ANP-Test-'+secret('ADMIN_PASSWORD')[:24]
            result[role]={**await create_user(email,password,role),'password':password}
        outside=str(uuid4())
        await Objects(secret('SERVICE_ROLE_KEY')).insert('workspaces',{'id':outside,'name':'隔离验收工作空间'})
        email='outside-'+outside[:8]+'@anp.local'
        password='ANP-Test-'+secret('ADMIN_PASSWORD')[:24]
        result['outside']={**await create_user(email,password,'developer',outside),'password':password}
        result['admin']={'email':secret('ADMIN_EMAIL'),'password':secret('ADMIN_PASSWORD')}
        async with httpx.AsyncClient(trust_env=False,timeout=20) as client:
            for name,data in result.items():
                response=await client.post(value('supabase','url')+'/auth/v1/token?grant_type=password',
                    headers={'apikey':secret('ANON_KEY')},json={'email':data['email'],'password':data['password']})
                response.raise_for_status()
                data['token']=response.json()['access_token']
        return result
    return asyncio.run(prepare())


def listening(port):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1',port))==0


@pytest.fixture(scope='module')
def web_services():
    processes=[]
    logs=[]
    api_port=int(value('development','api_port'));worker_port=int(value('development','worker_port'))
    for role,port,host in [('api',api_port,'127.0.0.1'),('worker',worker_port,'0.0.0.0')]:
        if listening(port): continue
        file=(ROOT/'.local/logs'/f'test-{role}.log')
        file.parent.mkdir(parents=True,exist_ok=True)
        log=file.open('w',encoding='utf-8');logs.append(log)
        process=subprocess.Popen([sys.executable,'-m','uvicorn',f'anp.server.{role}:app','--host',host,'--port',str(port)],
            cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        processes.append(process)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if listening(api_port) and listening(worker_port): break
        time.sleep(.2)
    else: pytest.fail('业务服务启动超时')
    yield
    for process in processes:
        if process.poll() is None:
            if sys.platform=='win32': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else: process.terminate()
    for log in logs: log.close()


def cli(arguments,identity,profile='acceptance',transport='local',environment=None):
    env={**os.environ,'ANP_PASSWORD':identity['password'],'ANP_PROFILE':profile,'PYTHONUTF8':'1',**(environment or {})}
    login=subprocess.run([sys.executable,'-m','anp.cli.entry','auth','login','--email',identity['email']],
        cwd=ROOT,env=env,text=True,encoding='utf-8',capture_output=True)
    assert login.returncode==0,login.stdout
    return subprocess.run([sys.executable,'-m','anp.cli.entry','--transport',transport,*arguments],
        cwd=ROOT,env=env,text=True,encoding='utf-8',capture_output=True)
