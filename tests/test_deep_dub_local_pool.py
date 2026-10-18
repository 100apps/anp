"""实际文件锁/子进程日志；不登记设备或占用GPU。"""
import asyncio
import json
import sys
import threading

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import _run
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.blocking import await_blocking


def test_owned_process_exit_is_recorded_without_host_command_paths(tmp_path):
    with LocalCpuPool('fixture-attempt',tmp_path) as pool:
        asyncio.run(_run([sys.executable,'-c','print(1)'],10))
        assert pool.stopped() and len(pool.data['jobs'])==1
        assert pool.data['jobs'][0]['exit_code']==0
        assert sys.executable not in pool.path.read_text(encoding='utf8')
    saved=json.loads(pool.path.read_text(encoding='utf8'))
    assert saved['state']=='idle' and saved['jobs'][0]['pid']>0
    with LocalCpuPool('second-attempt',tmp_path) as next_pool:
        assert next_pool.stopped()


def test_file_lock_prevents_second_executor(tmp_path):
    with LocalCpuPool('first',tmp_path):
        with pytest.raises(BusinessError) as busy:
            with LocalCpuPool('second',tmp_path):pass
        assert busy.value.code=='CPU_POOL_BUSY'


def test_unfinished_spawn_record_is_preserved_and_cannot_be_silently_reset(tmp_path):
    with LocalCpuPool('fixture-abandoned-spawn',tmp_path) as pool:
        pool.starting(['fixture-not-executed'])
    before=pool.path.read_bytes()
    with pytest.raises(BusinessError) as unknown:
        with LocalCpuPool('new-attempt',tmp_path):pass
    assert unknown.value.code=='CPU_POOL_UNKNOWN' and pool.path.read_bytes()==before


def test_cancellation_does_not_return_before_blocking_worker_really_stops():
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    def work():
        started.set();release.wait(timeout=5);finished.set()
    async def run():
        operation=asyncio.create_task(await_blocking(work))
        while not started.is_set():await asyncio.sleep(.01)
        operation.cancel();await asyncio.sleep(.02)
        assert not operation.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):await operation
        assert finished.is_set()
    try:asyncio.run(run())
    finally:release.set()
