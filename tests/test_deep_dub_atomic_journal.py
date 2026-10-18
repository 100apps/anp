import ctypes
from ctypes import wintypes
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from apps.deep_dub.services.atomic_journal import write_json
import apps.deep_dub.services.atomic_journal as journal


def test_concurrent_writes_are_complete_and_have_no_shared_temporary_file(tmp_path):
    path=tmp_path/'journal.json'
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i:write_json(path,{'index':i,'payload':'测量'*1000}),range(40)))
    data=json.loads(path.read_bytes());assert data['index'] in range(40) and data['payload']=='测量'*1000
    assert not list(tmp_path.glob('*.tmp'))


def test_unknown_os_error_propagates_and_keeps_original_journal(tmp_path,monkeypatch):
    path=tmp_path/'journal.json';path.write_text('{"original":true}',encoding='utf-8');calls=[]
    def failed(*args):calls.append(args);raise OSError(28,'No space')
    monkeypatch.setattr(journal.os,'replace',failed)
    with pytest.raises(OSError):write_json(path,{'new':True})
    assert len(calls)==1 and json.loads(path.read_bytes())=={'original':True} and not list(tmp_path.glob('*.tmp'))


def blocked_reader(path):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE;kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateFileW(str(path),0x80000000,1,None,3,128,None)
    assert handle not in (None,ctypes.c_void_p(-1).value)
    lock=threading.Lock();closed=False
    def close():
        nonlocal closed
        with lock:
            if not closed:kernel.CloseHandle(handle);closed=True
    return close


@pytest.mark.skipif(sys.platform!='win32',reason='实际Windows删除共享冲突')
def test_actual_windows_reader_handle_reproduces_replace_denial_and_bounded_retry(tmp_path):
    path=tmp_path/'journal.json';write_json(path,{'old':True});close=blocked_reader(path)
    probe=tmp_path/'probe.tmp';probe.write_text('{}',encoding='utf-8')
    try:
        with pytest.raises(PermissionError):journal.os.replace(probe,path)
        timer=threading.Timer(.15,close);timer.start()
        write_json(path,{'new':True});timer.join()
    finally:
        close();probe.unlink(missing_ok=True)
    assert json.loads(path.read_bytes())=={'new':True} and not list(tmp_path.glob('*.tmp'))


@pytest.mark.skipif(sys.platform!='win32',reason='实际Windows删除共享冲突')
def test_actual_permanent_reader_timeout_keeps_previous_log_and_rejects(tmp_path):
    path=tmp_path/'journal.json';write_json(path,{'old':True});close=blocked_reader(path)
    try:
        with pytest.raises(PermissionError):write_json(path,{'new':True},timeout=.03)
        assert json.loads(path.read_bytes())=={'old':True}
    finally:close()
    assert not list(tmp_path.glob('*.tmp'))
