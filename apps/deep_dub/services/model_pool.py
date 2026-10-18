"""本机受控模型进程：物理GPU文件锁、旧路由锁和已核验的桌面进程白名单。

目前只实现 Windows NVIDIA 本机执行；不把显存空闲或健康接口当作作业归属证明。
"""
from contextlib import ExitStack
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field

from anp import STATE_ROOT
from anp.config import value
from anp.errors import BusinessError
from apps.deep_dub.services.blocking import file_identity
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.listener_owner import listener_owners


class ModelConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    executable:str
    executable_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    working_directory:str
    weights:str
    weights_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    source_revision:str=Field(pattern=r'^[0-9a-f]{40}$')
    gpu_uuid:str=Field(pattern=r'^GPU-[a-zA-Z0-9-]+$')
    gpu_index:int=Field(ge=0,le=32)
    port:int=Field(default=18110,ge=1024,le=65535)
    model_id:str=Field(default='anp-index-tts2-5-q8',min_length=1,max_length=200)
    family:Literal['index_tts2','qwen3_asr','qwen3_forced_aligner']='index_tts2'
    minimum_free_mib:int=Field(default=11000,ge=1024)
    coordination_locks:list[str]=Field(min_length=1)
    allowed_graphics_executables:list[str]=Field(default_factory=list)

    def verify_files(self):
        for filename,expected in [(self.executable,self.executable_sha256),(self.weights,self.weights_sha256)]:
            if file_identity(filename)['sha256']!=expected:
                raise BusinessError('模型或执行器摘要与配置不符',409,'MODEL_IDENTITY_MISMATCH')
        if not Path(self.working_directory).is_dir():
            raise BusinessError('模型运行目录不存在',503,'MODEL_UNCONFIGURED')


def configuration(kind='tts'):
    families={'tts':'index_tts2','asr':'qwen3_asr','aligner':'qwen3_forced_aligner'}
    if kind not in families:raise ValueError('未知模型用途')
    filename=value('deep_dub',kind+'_config')
    if not filename:raise BusinessError('尚未配置本机受控模型执行器',503,'MODEL_UNCONFIGURED')
    config=ModelConfig.model_validate_json(Path(filename).read_bytes())
    if config.family!=families[kind]:
        raise BusinessError('模型家族与用途不符',409,'MODEL_FAMILY_MISMATCH')
    return config


def command_output(command):
    return subprocess.run(command,check=True,capture_output=True,text=True,timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0).stdout


def inventory(config):
    if sys.platform!='win32':raise BusinessError('当前受控模型运行器仅支持Windows NVIDIA',503,'MODEL_HOST_UNSUPPORTED')
    rows=list(csv.reader(command_output(['nvidia-smi','--query-gpu=index,uuid,memory.free','--format=csv,noheader,nounits']).splitlines()))
    gpu=next((r for r in rows if r[1].strip()==config.gpu_uuid and int(r[0])==config.gpu_index),None)
    if not gpu:raise BusinessError('GPU序号或物理UUID与配置不符',409,'MODEL_GPU_MISMATCH')
    # WDDM也会列出桌面图形进程。逐个核对管理员配置的完整可执行路径；未知者拒绝，绝不停止。
    apps=list(csv.reader(command_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,process_name','--format=csv,noheader,nounits']).splitlines()))
    allowed={os.path.normcase(os.path.abspath(p)) for p in config.allowed_graphics_executables}
    selected=[r for r in apps if r[0].strip()==config.gpu_uuid]
    if any(len(r)!=3 or os.path.normcase(os.path.abspath(r[2].strip())) not in allowed for r in selected):
        raise BusinessError('GPU有未知外部进程，拒绝启动模型；未停止任何其他进程',409,'MODEL_GPU_OCCUPIED')
    free=int(gpu[2])
    if free<config.minimum_free_mib:raise BusinessError('GPU余量不足，拒绝加载',409,'MODEL_GPU_MEMORY_LOW')
    return {'gpu_uuid':config.gpu_uuid,'gpu_index':config.gpu_index,'free_mib':free,
        'allowed_desktop_processes':len(selected),'external_model_processes':0,
        'scope':'物理UUID、完整进程路径白名单及旧路由锁；允许桌面图形进程共用，不代表整卡独占'}


class FileLock:
    def __init__(self,path):self.path=Path(path);self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b')
        self.file.seek(0,2)
        if self.file.tell()==0:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None
            raise BusinessError('GPU或已有TTS路由器正在使用',409,'MODEL_POOL_BUSY') from None
        return self
    def __exit__(self,*args):
        if self.file is not None:
            # 关闭句柄由操作系统释放文件锁，包括异常路径。
            self.file.close();self.file=None


class LocalModelPool(LocalCpuPool):
    def __init__(self,attempt_id,config,directory=None):
        super().__init__(attempt_id,directory)
        self.config=config;self.key='anp-model-gpu:'+config.gpu_uuid
        self.directory=Path(directory) if directory else STATE_ROOT/'deep_dub/gpu-pool'
        self.path=self.directory/(sha256(self.key.encode()).hexdigest()[:24]+'.json')
        self.guards=ExitStack();self.observation=None;self.entered=False

    def __enter__(self):
        try:
            for path in sorted(set(self.config.coordination_locks)):
                self.guards.enter_context(FileLock(path))
            super().__enter__()
            self.entered=True
            self.observation=inventory(self.config)
            return self
        except BaseException:
            if self.entered:super().__exit__(*sys.exc_info())
            self.guards.close();raise

    def proof(self):
        return {**super().proof(),'scope':self.observation['scope'],'gpu':self.observation,
            'coordination_lock_count':len(set(self.config.coordination_locks))}

    def __exit__(self,*args):
        try:super().__exit__(*args)
        finally:self.guards.close()


class OwnedModelServer:
    """仅结束本次Popen句柄对应进程；HTTP超时绝不等同已停止。"""
    def __init__(self,config,pool,folder):
        self.config=config;self.pool=pool;self.folder=Path(folder)
        self.process=None;self.logs=None;self.exit_code=None
        self.endpoint=f'http://127.0.0.1:{config.port}'

    def start(self):
        c=self.config
        with socket.socket() as probe:probe.bind(('127.0.0.1',c.port))
        settings={'host':'127.0.0.1','port':c.port,'backend':'cuda','device':c.gpu_index,'threads':4,
            'lazy_load':True,'max_loaded_models':1,'log_request_body':False,'max_request_body_bytes':16*1024**2,
            'models':[{'id':c.model_id,'family':c.family,'path':c.weights,'task':{'index_tts2':'tts','qwen3_asr':'asr','qwen3_forced_aligner':'align'}[c.family],'mode':'offline',
                'session_options':{'index_tts2.conv_weight_type':'f16','index_tts2.speaker_cache_slots':'8','index_tts2.emotion_cache_slots':'8'} if c.family=='index_tts2' else {}}]}
        path=self.folder/'server.json';path.write_bytes(json.dumps(settings).encode())
        log_dir=self.pool.directory/'model-logs';log_dir.mkdir(parents=True,exist_ok=True)
        self.logs=(log_dir/(self.pool.history_name+'.log')).open('xb')
        command=[c.executable,'--config',str(path)]
        self.pool.starting(command)
        try:
            env={**os.environ,'PATH':c.working_directory+os.pathsep+os.environ.get('PATH','')}
            self.process=subprocess.Popen(command,cwd=c.working_directory,env=env,stdin=subprocess.DEVNULL,
                stdout=self.logs,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        except BaseException:
            self.pool.spawn_failed();self.logs.close();raise
        self.pool.started(self.process.pid)

    def owns_listener(self):
        if self.process is None or self.process.poll() is not None:return False
        # 不能仅凭health响应把端口抢占者认作本次进程。
        owners=listener_owners(self.config.port)
        return self.process.poll() is None and owners=={self.process.pid}

    def stop(self):
        p=self.process
        if p is not None:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=10)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=10)
            self.exit_code=p.returncode
            self.pool.ended(p.pid,p.returncode);self.process=None
        if self.logs is not None:self.logs.close();self.logs=None
