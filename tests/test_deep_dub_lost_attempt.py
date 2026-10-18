"""独立库的失联失败收尾，不把管理员确认冒充进程停止测量。"""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import time
import psycopg
from psycopg.types.json import Jsonb
import pytest
from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed
from tests.test_deep_dub_execution import executions,work,device,acquire,PROBE


@pytest.fixture
def recovery(work):
    s=work
    with s['connect']() as connection:
        for _ in range(2):connection.execute((ROOT/'apps/deep_dub/migrations/021_reconcile_lost_attempt.sql').read_text(),prepare=False)
    return s


def close(connection,s,leases):
    return connection.execute('SELECT to_jsonb(platform.deep_dub_close_lost_attempt(%s,%s,%s))',(s['workspace_id'],s['attempt'],leases)).fetchone()[0]


def reconciled(connection,s):
    actor(connection,s['created_by']);target=device(connection,s);claimed,_=acquire(connection,s,target)
    lease=claimed['lease']['id']
    connection.execute('RESET ROLE')
    connection.execute("UPDATE platform.deep_dub_device_leases SET acquired_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 minute' WHERE id=%s",(lease,))
    version=connection.execute('SELECT row_version FROM platform.deep_dub_devices WHERE id=%s',(target,)).fetchone()[0]
    actor(connection,s['created_by'])
    connection.execute('SELECT platform.deep_dub_reconcile_device(%s,%s,%s,%s,%s)',(s['workspace_id'],target,version,[lease],Jsonb(PROBE)))
    return lease


def test_requires_complete_admin_reconciled_stopped_leases(recovery):
    s=recovery
    with s['connect']() as connection:
        actor(connection,s['created_by'])
        for ids in ([],[str(uuid4())]):
            with pytest.raises(psycopg.Error):
                with connection.transaction():close(connection,s,ids)
        target=device(connection,s);claimed,_=acquire(connection,s,target);lease=claimed['lease']['id']
        with pytest.raises(psycopg.Error):
            with connection.transaction():close(connection,s,[lease])


def test_recovery_only_marks_failure_unknown_monotonic_and_preserves_run(recovery):
    s=recovery
    with s['connect']() as connection:
        lease=reconciled(connection,s)
        before=connection.execute('SELECT to_jsonb(r) FROM platform.deep_dub_runs r WHERE id=%s',(s['run'],)).fetchone()[0]
        attempt=connection.execute('SELECT queued_at,started_at FROM platform.deep_dub_stage_attempts WHERE id=%s',(s['attempt'],)).fetchone()
        receipt=close(connection,s,[lease]);assert receipt['status']=='failed' and receipt['error']['code']=='WORKER_LOST'
        assert receipt['monotonic_seconds'] is None and receipt['outputs']==[] and receipt['finished_at']
        assert connection.execute('SELECT queued_at,started_at FROM platform.deep_dub_stage_attempts WHERE id=%s',(s['attempt'],)).fetchone()==attempt
        assert connection.execute('SELECT to_jsonb(r) FROM platform.deep_dub_runs r WHERE id=%s',(s['run'],)).fetchone()[0]==before
        assert close(connection,s,[lease])==receipt
        assert connection.execute('SELECT count(*) FROM platform.deep_dub_observation_outbox WHERE stage_attempt_id=%s',(s['attempt'],)).fetchone()[0]==1


@pytest.mark.parametrize('defect',['duplicate','wrong_set','not_stopped','not_admin_reconciled','other_operator','no_permission'])
def test_rejects_wrong_evidence_or_privilege(recovery,defect):
    s=recovery
    with s['connect']() as connection:
        lease=reconciled(connection,s);ids=[lease]
        connection.execute('RESET ROLE')
        if defect=='not_stopped':connection.execute("UPDATE platform.deep_dub_device_leases SET status='expired',released_at=NULL,operation_stopped=false WHERE id=%s",(lease,))
        if defect=='not_admin_reconciled':connection.execute("UPDATE platform.deep_dub_device_leases SET stop_evidence=jsonb_set(stop_evidence,'{method}','\"owner-confirmed\"') WHERE id=%s",(lease,))
        if defect=='other_operator':connection.execute("UPDATE platform.deep_dub_device_leases SET stop_evidence=jsonb_set(stop_evidence,'{confirmed_by}',to_jsonb(%s::text)) WHERE id=%s",(str(uuid4()),lease))
        if defect=='no_permission':connection.execute("UPDATE platform.members SET role='viewer' WHERE workspace_id=%s AND user_id=%s",(s['workspace_id'],s['created_by']))
        if defect=='duplicate':ids=[lease,lease]
        if defect=='wrong_set':ids=[str(uuid4())]
        actor(connection,s['created_by'])
        with pytest.raises(psycopg.Error):close(connection,s,ids)


@pytest.mark.parametrize('status',['failed','succeeded'])
def test_existing_terminal_receipt_is_never_overwritten(recovery,status):
    s=recovery
    with s['connect']() as connection:
        lease=reconciled(connection,s);connection.execute('RESET ROLE')
        connection.execute('UPDATE platform.deep_dub_stage_attempts SET status=%s,finished_at=clock_timestamp(),commit_hash=%s,error=%s WHERE id=%s',
            (status,'f'*64,Jsonb({'code':'EXISTING_FAILURE'}) if status=='failed' else None,s['attempt']))
        actor(connection,s['created_by'])
        with pytest.raises(psycopg.Error):close(connection,s,[lease])


def test_recovery_waits_for_normal_task_lock_without_holding_attempt_row(recovery):
    s=recovery
    with s['connect']() as setup:lease=reconciled(setup,s)
    first=s['connect']();started=Queue()
    def recover():
        with s['connect']() as second:
            actor(second,s['created_by']);second.execute("SET LOCAL lock_timeout='5s'")
            started.put(second.info.backend_pid)
            return close(second,s,[lease])
    try:
        # 正常 RPC 以 SECURITY DEFINER 身份取得这些锁；夹具用独立库 owner 模拟相同锁序。
        first.execute('SELECT id FROM platform.tasks WHERE id=%s FOR UPDATE',(s['task'],))
        first.execute('SELECT id FROM platform.deep_dub_runs WHERE id=%s FOR UPDATE',(s['run'],))
        first.execute('SELECT id FROM platform.deep_dub_stages WHERE id=%s FOR UPDATE',(s['stage'],))
        with ThreadPoolExecutor(max_workers=1) as workers:
            future=workers.submit(recover);pid=started.get(timeout=3);deadline=time.monotonic()+3
            while first.info.backend_pid not in first.execute('SELECT pg_blocking_pids(%s)',(pid,)).fetchone()[0]:
                assert time.monotonic()<deadline
                time.sleep(.02)
            first.execute("SET LOCAL lock_timeout='1s'")
            first.execute('SELECT id FROM platform.deep_dub_stage_attempts WHERE id=%s FOR UPDATE',(s['attempt'],))
            first.commit()
            assert future.result(timeout=6)['status']=='failed'
    finally:first.close()
