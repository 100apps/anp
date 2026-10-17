"""管理一个平台容器内的上游进程，独立检查就绪，失败退避并转发退出信号。"""
import asyncio
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import logging
import os
from pathlib import Path
import signal
import stat
import base64
import hashlib
import sys
import time
from urllib.request import urlopen
from .gateway import write

log=logging.getLogger('anp.platform')


class Runtime:
    def __init__(self,settings,metadata):
        self.settings=settings
        self.metadata=metadata
        self.states={name:{'status':'waiting','pid':None,'restarts':0} for name in settings['services']}
        self.processes={}
        self.stopping=False
        self.stop_event=asyncio.Event()

    async def pause(self,seconds):
        try:await asyncio.wait_for(self.stop_event.wait(),timeout=seconds)
        except asyncio.TimeoutError:pass

    def ready(self):
        required=[name for name,spec in self.settings['services'].items() if spec.get('required',True)]
        return bool(required) and all(self.states[name]['status']=='ready' for name in required)

    def probe(self,url):
        try:
            with urlopen(url,timeout=2) as response:return response.status==200
        except Exception:return False

    async def launch(self,name,output,initializing=False):
        component=self.settings['services'][name]
        image=self.metadata['components'][name]
        if component.get('execution')=='builtin' and not initializing:
            # 直接启动 Python 引擎，避免 launcher 再 exec Python 造成 debugpy 重复附加。
            environment=dict(item.split('=',1) for item in image['environment'] if '=' in item)
            environment.update({key:str(value) for key,value in component.get('environment',{}).items()})
            return await asyncio.create_subprocess_exec(*component.get('command',image['command']),
                env=environment,cwd=image['working_dir'],stdout=output,stderr=output,start_new_session=True)
        arguments=['/usr/bin/env',sys.executable,'-m','anp_platform.child',name]
        if initializing:arguments.append('initialize')
        # Go/Node/Rust 组件的 Python 启动包装器无需调试，也不向第三方 exec 注入调试器。
        return await asyncio.create_subprocess_exec(*arguments,stdout=output,stderr=output,start_new_session=True)

    async def reap_group(self,process):
        if process.returncode is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:await asyncio.wait_for(process.wait(),20)
            except asyncio.TimeoutError:pass
        # 父进程退出并不代表 Prisma 等同组子进程退出；异常退出同样需要清理。
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        await process.wait()

    async def service(self,name,spec):
        state=self.states[name]
        if name not in self.metadata['components']:
            state['status']='missing_artifact'
            return
        until=time.monotonic()+300
        while not self.stopping:
            dependencies=spec.get('depends_on',[])
            if all(self.states[dependency]['status']=='ready' for dependency in dependencies):break
            if time.monotonic()>until:
                state['status']='dependency_timeout'
                return
            await asyncio.sleep(.5)
        if spec.get('before_start')=='schema':
            from .bootstrap import schema
            try:
                await asyncio.to_thread(schema,self.settings)
            except Exception as error:
                state['status']='initialization_failed'
                # 连接异常可能包含私有 DSN，只记录类型，不泄露连接字符串。
                log.error('平台组件 %s 初始化失败：%s',name,type(error).__name__)
                return
        if spec.get('initialization_command'):
            logs=Path('/var/log/anp');logs.mkdir(parents=True,exist_ok=True)
            state['status']='initializing'
            with (logs/(name+'.log')).open('ab',buffering=0) as output:
                process=await self.launch(name,output,initializing=True)
                self.processes[name]=process
                state['pid']=process.pid
                while process.returncode is None and not self.stopping:
                    await asyncio.sleep(.5)
                await self.reap_group(process)
                if self.stopping:return
                state['pid']=None
                if process.returncode!=0:
                    state['status']='initialization_failed'
                    return
        while not self.stopping:
            log.info('启动平台组件 %s',name)
            logs=Path('/var/log/anp');logs.mkdir(parents=True,exist_ok=True)
            with (logs/(name+'.log')).open('ab',buffering=0) as output:
                process=await self.launch(name,output)
                self.processes[name]=process
                state.update(status='starting',pid=process.pid)
                started=time.monotonic()
                configured=False
                while process.returncode is None and not self.stopping:
                    if spec.get('health_url'):
                        healthy=await asyncio.to_thread(self.probe,spec['health_url'])
                        if healthy and spec.get('configure') and not configured:
                            from .bootstrap import configure_dbx,configure_litellm
                            try:
                                state['status']='configuring'
                                initializer={'dbx':configure_dbx,'litellm':configure_litellm}[spec['configure']]
                                await asyncio.to_thread(initializer,self.settings)
                                configured=True
                            except Exception as error:
                                state['status']='configuration_failed'
                                log.error('平台组件 %s 预配置失败：%s',name,type(error).__name__)
                                await self.pause(5)
                                continue
                        state['status']='ready' if healthy else 'starting'
                    else:state['status']='ready' if time.monotonic()-started>2 else 'starting'
                    await asyncio.sleep(1)
                await self.reap_group(process)
                if self.stopping:break
            state.update(status='failed',pid=None)
            state['restarts']+=1
            if state['restarts']>5:
                state['status']='restart_limit'
                return
            await self.pause(min(2**state['restarts'],30))

    async def run(self):
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,self.stop)
        server=HTTPServer(('127.0.0.1',12000),handler(self))
        import threading
        threading.Thread(target=server.serve_forever,daemon=True).start()
        from .bridges import Bridges
        bridges=Bridges()
        await bridges.start(self.settings.get('host_bridges',[]))
        gateway=await asyncio.create_subprocess_exec(os.environ.get('ANP_NGINX_BINARY','nginx'),'-c',write(self.settings),'-g','daemon off;')
        tasks=[asyncio.create_task(self.service(name,spec)) for name,spec in self.settings['services'].items()]
        try:
            while not self.stopping:
                if gateway.returncode is not None:
                    raise RuntimeError('平台路径网关已退出')
                await asyncio.sleep(.5)
        finally:
            self.stopping=True
            await asyncio.gather(*tasks,return_exceptions=True)
            if gateway.returncode is None:
                gateway.terminate()
                await gateway.wait()
            await bridges.close()
            server.shutdown()

    def stop(self):
        self.stopping=True
        self.stop_event.set()


def handler(runtime):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/health','/services'):
                self.send_error(404);return
            ready=runtime.ready()
            body=json.dumps({'ready':ready,'services':runtime.states},ensure_ascii=False).encode()
            self.send_response(200 if ready or self.path=='/services' else 503)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    return Handler


def prepare_roots(metadata):
    for name in metadata['components']:
        if not metadata['components'][name].get('rootfs',True):continue
        root=Path('/opt/anp/roots')/name
        (root/'dev').mkdir(exist_ok=True)
        for node,minor in [('null',3),('zero',5),('random',8),('urandom',9)]:
            path=root/'dev'/node
            if not path.exists():os.mknod(path,stat.S_IFCHR|0o666,os.makedev(1,minor))
            os.chmod(path,0o666)
        for file in ('resolv.conf','hosts'):
            target=root/'etc'/file
            if target.is_symlink():target.unlink()
            target.write_bytes((Path('/etc')/file).read_bytes())


def main():
    logging.basicConfig(level=logging.INFO)
    settings=json.loads(Path(os.environ.get('ANP_PLATFORM_CONFIG','/run/anp/config.json')).read_text())
    metadata=json.loads(Path(os.environ.get('ANP_COMPONENTS','/opt/anp/components.json')).read_text())
    Path('/run/anp').mkdir(parents=True,exist_ok=True)
    from .bootstrap import databases
    databases(settings)
    prepare_roots(metadata)
    for name,files in settings.get('files',{}).items():
        if name not in metadata['components']:continue
        root=Path('/opt/anp/roots')/name
        for source,destination in files.items():
            if '..' in Path(destination).parts:raise ValueError('无效组件配置路径')
            target=root/destination.lstrip('/')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(Path(source).read_bytes())
    credentials=settings.get('studio_basic_auth')
    if credentials:
        digest=base64.b64encode(hashlib.sha1(credentials['password'].encode()).digest()).decode()
        Path('/run/anp/studio.htpasswd').write_text(credentials['username']+':{SHA}'+digest+'\n')
    from .handoff import restore,snapshot
    archive=settings.get('agent_state_archive')
    directory=settings.get('agent_state_directory','/state/agent')
    if archive:restore(directory,archive)
    try:asyncio.run(Runtime(settings,metadata).run())
    finally:
        if archive:snapshot(directory,archive)


if __name__=='__main__':main()
