"""独立听音模型的受控本机运行器；沿用物理GPU锁，只停止本次原生句柄。"""
import asyncio
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import socket
import subprocess
import sys
import time
from typing import Literal

import httpx
from pydantic import Field

from anp.config import value
from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.services.blocking import await_blocking,file_identity
from apps.deep_dub.services.model_pool import ModelConfig,OwnedModelServer


class AudioJudgeConfig(ModelConfig):
    family:Literal['voxtral']='voxtral'
    mmproj:str
    mmproj_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    runtime_files:dict[str,str]=Field(min_length=1,max_length=100)
    context_size:int=Field(default=8192,ge=4096,le=32768)

    def verify_files(self):
        super().verify_files()
        if file_identity(self.mmproj)['sha256']!=self.mmproj_sha256:
            raise BusinessError('音频编码器摘要不符',409,'MODEL_IDENTITY_MISMATCH')
        directory=Path(self.working_directory)
        expected={name.lower() for name in self.runtime_files}
        if expected!={p.name.lower() for p in directory.glob('*.dll')}:
            raise BusinessError('原生运行器依赖列表变化',409,'MODEL_IDENTITY_MISMATCH')
        for name,digest in self.runtime_files.items():
            if not re.fullmatch(r'[A-Za-z0-9._-]+\.dll',name,re.IGNORECASE) or not re.fullmatch(r'[a-f0-9]{64}',digest):
                raise BusinessError('原生运行器依赖声明无效',409,'MODEL_IDENTITY_MISMATCH')
            if file_identity(directory/name)['sha256']!=digest:
                raise BusinessError('原生运行器依赖摘要不符',409,'MODEL_IDENTITY_MISMATCH')


def configuration():
    filename=value('deep_dub','audio_reviewer_config')
    if not filename:raise BusinessError('尚未配置独立听音审核器',503,'AUDIO_REVIEWER_UNCONFIGURED')
    return AudioJudgeConfig.model_validate_json(Path(filename).read_bytes())


class OwnedAudioJudge(OwnedModelServer):
    def __init__(self,*args):
        super().__init__(*args);self.api_key=token_urlsafe(40)

    def start(self):
        c=self.config
        with socket.socket() as probe:probe.bind(('127.0.0.1',c.port))
        key_file=self.folder/'judge-api-key';key_file.write_text(self.api_key,encoding='utf-8');os.chmod(key_file,0o600)
        logs=self.pool.directory/'model-logs';logs.mkdir(exist_ok=True)
        self.logs=(logs/(self.pool.history_name+'.audio-judge.log')).open('xb')
        command=[c.executable,'--model',c.weights,'--mmproj',c.mmproj,'--alias',c.model_id,
            '--host','127.0.0.1','--port',str(c.port),'--n-gpu-layers','99','--main-gpu','0',
            '--ctx-size',str(c.context_size),'--parallel','1','--threads','4','--no-webui','--api-key-file',str(key_file)]
        self.pool.starting(command)
        try:
            env={k:v for k,v in os.environ.items() if not k.startswith('LLAMA_')}
            env.update(PATH=c.working_directory+os.pathsep+os.environ.get('PATH',''),CUDA_VISIBLE_DEVICES=c.gpu_uuid)
            self.process=subprocess.Popen(command,cwd=c.working_directory,env=env,
                stdin=subprocess.DEVNULL,stdout=self.logs,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        except BaseException:
            self.pool.spawn_failed();self.logs.close();raise
        self.pool.started(self.process.pid)


class AudioJudgeSession:
    def __init__(self,config,pool,folder):
        self.config=config;self.pool=pool;self.server=OwnedAudioJudge(config,pool,folder)
        self.requests=0;self.started=None;self.finished=None;self.properties=None

    async def __aenter__(self):
        self.started=time.monotonic();self.server.start()
        try:
            async with async_client(timeout=5,trust_env=False,headers={'Authorization':'Bearer '+self.server.api_key}) as client:
                deadline=time.monotonic()+120
                while True:
                    if self.server.process.poll() is not None:raise BusinessError('听音模型加载后退出',503,'AUDIO_JUDGE_START_FAILED')
                    try:
                        reply=await client.get(self.server.endpoint+'/health')
                        if reply.status_code==200:break
                    except httpx.TransportError:pass
                    if time.monotonic()>deadline:raise BusinessError('听音模型加载超时',504,'AUDIO_JUDGE_START_TIMEOUT')
                    await asyncio.sleep(.2)
                if not await await_blocking(self.server.owns_listener):raise BusinessError('听音端口不属于本次进程',409,'MODEL_LISTENER_MISMATCH')
                reply=await client.get(self.server.endpoint+'/props');reply.raise_for_status();props=reply.json()
                if props.get('modalities',{}).get('audio') is not True or props.get('total_slots')!=1 or Path(props.get('model_path','')).resolve()!=Path(self.config.weights).resolve():
                    raise BusinessError('运行器未声明音频能力或模型身份不符',409,'AUDIO_JUDGE_CAPABILITY_MISSING')
                self.properties={'modalities':props['modalities'],'build_info':props.get('build_info'),'total_slots':props.get('total_slots')}
            return self
        except BaseException:
            await await_blocking(self.server.stop);raise

    async def __aexit__(self,*args):
        await await_blocking(self.server.stop);self.finished=time.monotonic()

    def receipt(self):
        if self.finished is None or not self.pool.stopped():raise BusinessError('听音进程尚未确认退出',409,'OPERATION_NOT_STOPPED')
        return {'requests':self.requests,'process_lifecycle_seconds':self.finished-self.started,
                'owned_server_exit_code':self.server.exit_code,'operation_stopped':True,'properties':self.properties}
