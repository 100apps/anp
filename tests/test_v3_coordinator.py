"""状态查询须有界并发、隔离单任务错误、可取消，并轮询首批之外的长任务。"""
import asyncio
import pytest
from anp.server import api


def test_slow_task_does_not_block_others_and_failure_does_not_end_batch(monkeypatch,caplog):
    async def scenario():
        held=asyncio.Event();advanced=asyncio.Event()
        active=0;maximum=0;visited=[]
        async def reconcile(_,task):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active);visited.append(task['id'])
            try:
                if task['id']=='slow':await held.wait()
                elif task['id']=='failed':raise RuntimeError('postgresql://private-password@example')
                else:advanced.set()
            finally:active-=1
        monkeypatch.setattr(api,'reconcile_remote',reconcile)
        job=asyncio.create_task(api.reconcile_batch(None,[{'id':name} for name in ('slow','good','failed','later')],2))
        await asyncio.wait_for(advanced.wait(),timeout=1)
        assert not job.done()
        held.set();await job
        assert maximum==2 and active==0
        assert set(visited)=={'slow','good','failed','later'}
    asyncio.run(scenario())
    assert 'RuntimeError' in caplog.text and 'private-password' not in caplog.text


def test_shutdown_cancels_inflight_and_waiting_queries(monkeypatch):
    async def scenario():
        started=asyncio.Event();hold=asyncio.Event();active=0
        async def reconcile(_,task):
            nonlocal active
            active+=1
            if active==2:started.set()
            try:await hold.wait()
            finally:active-=1
        monkeypatch.setattr(api,'reconcile_remote',reconcile)
        job=asyncio.create_task(api.reconcile_batch(None,[{'id':str(i)} for i in range(5)],2))
        await asyncio.wait_for(started.wait(),timeout=1)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):await job
        assert active==0
    asyncio.run(scenario())


def test_more_than_fifty_long_running_tasks_are_all_checked(monkeypatch):
    visited=[]
    class Objects:
        async def select(self,table,**filters):
            assert table=='tasks' and filters['mode']=='eq.http' and filters['status']=='eq.running'
            assert filters['order']=='created_at.asc,id.asc'
            return [{'id':str(i)} for i in range(58)][filters['offset']:filters['offset']+filters['limit']]
    async def reconcile(_,task):visited.append(task['id'])
    monkeypatch.setattr(api,'reconcile_remote',reconcile)
    async def scenario():
        offset=await api.reconcile_page(Objects(),0,4)
        assert offset==50
        offset=await api.reconcile_page(Objects(),offset,4)
        assert offset==0
    asyncio.run(scenario())
    assert set(visited)=={str(i) for i in range(58)}
