"""实际终止隔离 owner，确认内核停止它的原生子进程及孙进程。"""
import ctypes
from ctypes import wintypes
import json,subprocess,sys,time
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform!='win32',reason='Windows Job 的真实 owner 失联验证')
def test_owner_termination_kills_owned_native_tree_without_inference_or_other_jobs(tmp_path):
    marker=tmp_path/'child.json';ready=tmp_path/'ready.json'
    child_code=f"import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW);Path({str(marker)!r}).write_text(str(p.pid));time.sleep(60)"
    owner_code=f'''import sys,time,subprocess,json
from pathlib import Path
sys.path[:0]=[{str(ROOT)!r},{str(ROOT/'src')!r}]
from apps.deep_dub.services.native_guard import NativeGuard
guard=NativeGuard()
child=subprocess.Popen([sys.executable,'-c',{child_code!r}],creationflags=subprocess.CREATE_NO_WINDOW|4)
guard.assign(child);guard.resume(child)
deadline=time.monotonic()+10
while not Path({str(marker)!r}).exists():
 assert time.monotonic()<deadline
 time.sleep(.02)
Path({str(ready)!r}).write_text(json.dumps({{'native':child.pid,'grandchild':int(Path({str(marker)!r}).read_text()),'members':guard.members(),'accounting':guard.accounting()}}))
time.sleep(60)
'''
    owner=subprocess.Popen([sys._base_executable,'-c',owner_code],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handles=[]
    try:
        deadline=time.monotonic()+12
        while not ready.exists():
            if owner.poll() is not None:pytest.fail(owner.stderr.read().decode('utf-8',errors='replace'))
            assert time.monotonic()<deadline
            time.sleep(.02)
        state=json.loads(ready.read_bytes())
        assert {state['native'],state['grandchild']}<=set(state['members'])
        assert len(state['members'])==state['accounting']['active_processes']
        for pid in state['members']:
            handle=kernel.OpenProcess(0x1000|0x100000|1,False,pid);assert handle;handles.append(handle)
            assert kernel.WaitForSingleObject(handle,0)==258
        owner.terminate();owner.wait(timeout=5)
        for handle in handles:
            assert kernel.WaitForSingleObject(handle,5000)==0
            code=wintypes.DWORD();assert kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value!=259
    finally:
        if owner.poll() is None:owner.kill();owner.wait(timeout=5)
        for handle in handles:
            if kernel.WaitForSingleObject(handle,0)==258:kernel.TerminateProcess(handle,1);kernel.WaitForSingleObject(handle,5000)
            kernel.CloseHandle(handle)
