"""Windows 作业对象约束下载器及其 Node/FFmpeg 子孙，取消只清理本次树。

先启动等待stdin闸门的隔离Python，归入新建Job后才允许启动工具。
https://learn.microsoft.com/windows/win32/procthread/job-objects
"""
import asyncio
import ctypes as c
from pathlib import Path
import subprocess
import sys
import time

from anp.errors import BusinessError
from apps.deep_dub.services.local_pool import process_observer

D=c.c_uint32; Q=c.c_uint64; S=c.c_size_t; H=c.c_void_p


class BasicLimit(c.Structure):
    _fields_=[('process_time',c.c_int64),('job_time',c.c_int64),('flags',D),
        ('min_working_set',S),('max_working_set',S),('active_limit',D),
        ('affinity',S),('priority',D),('scheduling',D)]


class ExtendedLimit(c.Structure):
    _fields_=[('basic',BasicLimit),('io',Q*6),('process_memory',S),('job_memory',S),
        ('peak_process',S),('peak_job',S)]


class Accounting(c.Structure):
    _fields_=[('user',c.c_int64),('kernel',c.c_int64),('period_user',c.c_int64),
        ('period_kernel',c.c_int64),('faults',D),('total',D),('active',D),('terminated',D)]


class OwnedJob:
    def __init__(self):
        if sys.platform!='win32':raise BusinessError('下载进程树运行器仅支持Windows',503,'DOWNLOAD_HOST_UNSUPPORTED')
        self.api=c.WinDLL('kernel32.dll',use_last_error=True,winmode=0x800)
        for name,args,result in [('CreateJobObjectW',[H,c.c_wchar_p],H),
            ('SetInformationJobObject',[H,c.c_int,H,D],c.c_int),
            ('AssignProcessToJobObject',[H,H],c.c_int),('TerminateJobObject',[H,D],c.c_int),
            ('QueryInformationJobObject',[H,c.c_int,H,D,H],c.c_int),('CloseHandle',[H],c.c_int)]:
            fn=getattr(self.api,name);fn.argtypes=args;fn.restype=result
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:raise c.WinError(c.get_last_error())
        limits=ExtendedLimit();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE；禁止breakaway。
        try:self.check(self.api.SetInformationJobObject(self.handle,9,c.byref(limits),c.sizeof(limits)))
        except BaseException:self.close();raise

    @staticmethod
    def check(result):
        if not result:raise c.WinError(c.get_last_error())

    def assign(self,process):self.check(self.api.AssignProcessToJobObject(self.handle,int(process._handle)))
    def terminate(self):self.check(self.api.TerminateJobObject(self.handle,1))
    def accounting(self):
        info=Accounting()
        self.check(self.api.QueryInformationJobObject(self.handle,1,c.byref(info),c.sizeof(info),None))
        return {'total_processes':info.total,'active_processes':info.active,'terminated_processes':info.terminated}
    def close(self):
        if self.handle:self.api.CloseHandle(self.handle);self.handle=None


async def run_tree(command,folder,timeout):
    """输出只保留私有临时文件；退出后确认整树无活动进程，再登记根进程停止。"""
    folder=Path(folder);out=folder/'tool.stdout';err=folder/'tool.stderr'
    if out.exists() or err.exists():raise BusinessError('拒绝覆盖工具日志',409,'DOWNLOAD_TARGET_EXISTS')
    observer=process_observer.get();job=OwnedJob();process=None;assigned=False;confirmed=False
    started=time.monotonic();receipt=None
    bootstrap="import sys; gate=sys.stdin.buffer.read(1); assert gate==b'1'; import subprocess; sys.exit(subprocess.call(sys.argv[1:],stdin=subprocess.DEVNULL))"
    try:
        with out.open('xb') as stdout,err.open('xb') as stderr:
            if observer:observer.starting(command)
            try:
                process=subprocess.Popen([sys._base_executable,'-I','-S','-u','-c',bootstrap,*command],
                    stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
            except BaseException:
                if observer:observer.spawn_failed()
                raise
            if observer:observer.started(process.pid)
            job.assign(process);assigned=True
            process.stdin.write(b'1');process.stdin.close()
            while process.poll() is None:
                if time.monotonic()-started>timeout:raise BusinessError('来源获取超时',408,'SOURCE_DOWNLOAD_TIMEOUT')
                if out.stat().st_size>16*1024**2 or err.stat().st_size>16*1024**2:
                    raise BusinessError('来源工具日志超限',422,'DOWNLOAD_LOG_LIMIT')
                await asyncio.sleep(.1)
            receipt=job.accounting()
            if receipt['active_processes']:
                raise BusinessError('工具退出后仍有子孙进程，拒绝成功回执',409,'DOWNLOAD_CHILDREN_ACTIVE')
            confirmed=True
            if process.returncode:
                raise BusinessError('来源获取失败；私有错误需核对，不自动换账号或降低画质',422,'SOURCE_DOWNLOAD_FAILED')
        return {'exit_code':process.returncode,'job':receipt,'operation_stopped':True,'elapsed_seconds':time.monotonic()-started}
    finally:
        # 不仅等待根进程，必须核实这个新建Job中的所有子孙。查询失败不把日志标为已停止。
        try:
            if process is not None:
                if not confirmed:
                    if assigned:job.terminate()
                    elif process.poll() is None:process.kill()
                    await asyncio.to_thread(process.wait,timeout=15)
                    deadline=time.monotonic()+15
                    while assigned and job.accounting()['active_processes']:
                        if time.monotonic()>deadline:raise BusinessError('本次下载树未停止，租约不得释放',409,'OPERATION_NOT_STOPPED')
                        await asyncio.sleep(.05)
                    confirmed=True
                if confirmed and observer:observer.ended(process.pid,process.returncode)
                if process.stdin and not process.stdin.closed:process.stdin.close()
        finally:job.close()
