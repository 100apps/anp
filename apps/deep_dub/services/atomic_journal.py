"""进程日志原子保存；Windows短时读句柄不应导致永久失联，超时仍拒绝继续。"""
import json
import os
from pathlib import Path
from threading import RLock
import time
from uuid import uuid4

_writing=RLock()


def write_json(path,data,*,timeout=2.):
    path=Path(path)
    # 同进程线程串行化，不取代CPU/GPU跨进程租约锁。
    with _writing:
        temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
        try:
            with temporary.open('x',encoding='utf-8',newline='\n') as output:
                json.dump(data,output,ensure_ascii=False,indent=2);output.flush();os.fsync(output.fileno())
            deadline=time.monotonic()+timeout
            while True:
                try:
                    os.replace(temporary,path)
                    return
                except OSError as error:
                    # 不重试磁盘满、权限配置或未知错误；仅Windows读共享/删除共享冲突短时重试。
                    if getattr(error,'winerror',None) not in (5,32,33) or time.monotonic()>=deadline:raise
                    time.sleep(min(.01,max(0,deadline-time.monotonic())))
        finally:temporary.unlink(missing_ok=True)
