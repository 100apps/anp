"""把尚未执行的原生进程归入 Windows Job，owner 退出时整树由内核终止。"""
import ctypes as c
from apps.deep_dub.services.owned_tree import OwnedJob,H,D


class ThreadEntry(c.Structure):
    _fields_=[('size',D),('usage',D),('thread',D),('owner',D),('priority',c.c_long),('delta',c.c_long),('flags',D)]


class NativeGuard(OwnedJob):
    def members(self):
        buffer=c.create_string_buffer(8+4096*c.sizeof(c.c_size_t))
        self.check(self.api.QueryInformationJobObject(self.handle,3,buffer,c.sizeof(buffer),None))
        assigned=D.from_buffer(buffer,0).value;count=D.from_buffer(buffer,4).value
        if assigned!=count or count>4096:raise RuntimeError('本次原生进程树成员快照不完整')
        return [c.c_size_t.from_buffer(buffer,8+i*c.sizeof(c.c_size_t)).value for i in range(count)]

    def resume(self,process):
        for name,args,result in [('CreateToolhelp32Snapshot',[D,D],H),('Thread32First',[H,c.POINTER(ThreadEntry)],c.c_int),
            ('Thread32Next',[H,c.POINTER(ThreadEntry)],c.c_int),('OpenThread',[D,c.c_int,D],H),('ResumeThread',[H],D)]:
            function=getattr(self.api,name);function.argtypes=args;function.restype=result
        snapshot=self.api.CreateToolhelp32Snapshot(4,0)
        if snapshot==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
        try:
            entry=ThreadEntry();entry.size=c.sizeof(entry);threads=[]
            more=self.api.Thread32First(snapshot,c.byref(entry))
            while more:
                if entry.owner==process.pid:threads.append(entry.thread)
                more=self.api.Thread32Next(snapshot,c.byref(entry))
            if len(threads)!=1:raise RuntimeError('原生进程尚未恢复时应只有一个主线程')
            thread=self.api.OpenThread(2,False,threads[0])
            if not thread:raise c.WinError(c.get_last_error())
            try:
                previous=self.api.ResumeThread(thread)
                if previous!=1:raise RuntimeError('原生主线程暂停状态不符合本次创建约定')
            finally:self.api.CloseHandle(thread)
        finally:self.api.CloseHandle(snapshot)
