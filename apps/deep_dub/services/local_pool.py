"""本工作区单并发媒体 CPU 池；日志不明时拒绝自动恢复，不代表物理 CPU 独占。"""
from contextvars import ContextVar
from datetime import datetime,timezone
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from uuid import uuid4

from anp import ROOT
from anp.errors import BusinessError
from apps.deep_dub.services.atomic_journal import write_json

process_observer=ContextVar('deep_dub_process_observer',default=None)


def stamp():return datetime.now(timezone.utc).isoformat()


@lru_cache
def boot_identity():
    if sys.platform=='win32':
        value=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
            '(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")'],
            check=True,capture_output=True,text=True,timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW).stdout.strip()
    else:value=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not value or len(value)>200:raise BusinessError('无法核对主机启动身份',409,'CPU_POOL_UNKNOWN')
    return value


class LocalCpuPool:
    def __init__(self,attempt_id,directory=None):
        self.host=socket.gethostname()
        self.key='anp-media-cpu:'+self.host
        self.directory=Path(directory) if directory is not None else ROOT/'.local/deep_dub/cpu-pool'
        self.path=self.directory/(sha256(self.host.encode()).hexdigest()[:24]+'.json')
        self.attempt_id=attempt_id
        self.lock=None
        self.data=None
        self.previous=None
        self.history_name=uuid4().hex+'.json'

    def save(self):
        write_json(self.path,self.data)

    def __enter__(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        self.lock=self.path.with_suffix('.lock').open('a+b')
        self.lock.seek(0,2)
        if self.lock.tell()==0:self.lock.write(b'0');self.lock.flush()
        self.lock.seek(0)
        try:
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock.close();self.lock=None
            raise BusinessError('本工作区媒体 CPU 池正在使用',409,'CPU_POOL_BUSY') from None
        try:
            if self.path.exists():
                try:old=json.loads(self.path.read_text(encoding='utf8'))
                except (OSError,ValueError):
                    raise BusinessError('进程日志不可读，拒绝自动恢复',409,'CPU_POOL_UNKNOWN') from None
                if (old.get('state')!='idle' or old.get('pending_spawn') is not False
                        or old.get('host')!=self.host or any(job.get('exit_code') is None for job in old.get('jobs',[]))):
                    raise BusinessError('旧进程日志未收尾，需核对后恢复；拒绝自动接管',409,'CPU_POOL_UNKNOWN')
                self.previous=old
            self.data={'state':'working','host':self.host,'owner_pid':os.getpid(),'attempt_id':self.attempt_id,
                'started_at':stamp(),'pending_spawn':False,'jobs':[]}
            self.save()
        except BaseException:
            self.unlock();raise
        self.context_token=process_observer.set(self)
        return self

    def unlock(self):
        if self.lock is None:return
        if sys.platform=='win32':
            import msvcrt
            self.lock.seek(0);msvcrt.locking(self.lock.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(self.lock.fileno(),fcntl.LOCK_UN)
        self.lock.close();self.lock=None

    def stopped(self):
        return not self.data['pending_spawn'] and all(job['exit_code'] is not None for job in self.data['jobs'])

    def proof(self):
        return {'all_owned_operations_stopped':self.stopped(),'has_external_processes':False,
            'host_boot_id':boot_identity(),'scope':'本工作区单并发媒体CPU池；文件锁排他，非物理CPU独占或GPU空闲证明',
            'owner_pid':os.getpid(),'journal_jobs':self.data['jobs'],
            'previous_completed_jobs':self.previous['jobs'] if self.previous else []}

    def starting(self,command):
        self.data['pending_spawn']=True
        self.data['pending_command_sha256']=sha256(json.dumps(command).encode()).hexdigest()
        self.save()

    def started(self,pid):
        self.data['jobs'].append({'pid':pid,'started_at':stamp(),'exit_code':None,
                                 'command_sha256':self.data['pending_command_sha256']})
        self.data['pending_spawn']=False;self.save()

    def spawn_failed(self):
        self.data['pending_spawn']=False;self.save()

    def ended(self,pid,code):
        job=next(x for x in self.data['jobs'] if x['pid']==pid and x['exit_code'] is None)
        job.update(exit_code=code,finished_at=stamp());self.save()

    def __exit__(self,*args):
        try:
            if self.stopped():self.data.update(state='idle',finished_at=stamp())
            self.save()
            history=self.directory/'history';history.mkdir(exist_ok=True)
            with (history/self.history_name).open('x',encoding='utf8',newline='\n') as output:
                json.dump(self.data,output,ensure_ascii=False,indent=2)
        finally:
            process_observer.reset(self.context_token);self.unlock()
