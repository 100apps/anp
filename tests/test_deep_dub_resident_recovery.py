"""独立库核对常驻槽位恢复边界，不制造实际owner失联。"""
from copy import deepcopy
import pytest,psycopg
from psycopg.types.json import Jsonb
from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed
from tests.test_deep_dub_execution import executions,work,device,acquire,begin,expire
from tests.test_deep_dub_resident_probe import prepared,confirm,PROBE


@pytest.fixture
def lost(prepared):
    s,d=prepared
    with s['connect']() as c:
        for name in ('021_reconcile_lost_attempt.sql','023_resident_recovery.sql'):
            for _ in range(2):c.execute((ROOT/'apps/deep_dub/migrations'/name).read_text(),prepare=False)
        actor(c,s['created_by']);ready=confirm(c,s,d,PROBE)
        claim,_=acquire(c,s,d['id']);lease=claim['lease']
        c.execute('RESET ROLE');expire(c,lease['id'])
        ready=c.execute('SELECT to_jsonb(d) FROM platform.deep_dub_devices d WHERE id=%s',(d['id'],)).fetchone()[0]
    proof={**deepcopy(PROBE),'remote_receipt':{'job_id':s['attempt'],'operation_stopped':True,'worker_running':False,
        'resident_service_stopped':False,'service_identity':PROBE['service_identity'],'status':'succeeded',
        'input_sha256':'c'*64,'worker_sha256':'d'*64}}
    return s,ready,lease,proof


def reconcile(c,s,d,lease,proof):
    return c.execute('SELECT to_jsonb(platform.deep_dub_reconcile_resident(%s,%s,%s,%s,%s,%s))',
        (s['workspace_id'],d['id'],d['row_version'],s['attempt'],[lease['id']],Jsonb(proof))).fetchone()[0]


def test_expired_resident_owner_recovers_as_failed_not_success_and_keeps_run(lost):
    s,d,lease,proof=lost
    with s['connect']() as c:
        actor(c,s['created_by']);ready=reconcile(c,s,d,lease,proof)
        assert ready['state']=='ready' and ready['probe']['has_external_processes'] is True
        result=c.execute('SELECT to_jsonb(platform.deep_dub_close_lost_attempt(%s,%s,%s))',
            (s['workspace_id'],s['attempt'],[lease['id']])).fetchone()[0]
        assert result['status']=='failed' and result['error']['code']=='WORKER_LOST'
        assert result['monotonic_seconds'] is None and result['outputs']==[]
        assert c.execute('SELECT status FROM platform.deep_dub_runs WHERE id=%s',(s['run'],)).fetchone()[0]=='running'
        with pytest.raises(psycopg.Error):
            with c.transaction():reconcile(c,s,d,lease,proof)


@pytest.mark.parametrize('field,value',[('worker_running',True),('operation_stopped',False),('resident_service_stopped',True),
    ('job_id','00000000-0000-4000-8000-000000000000'),('status','uncertain'),('worker_sha256','bad')])
def test_missing_or_foreign_job_stop_proof_cannot_release(lost,field,value):
    s,d,lease,proof=lost;proof['remote_receipt'][field]=value
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):
            with c.transaction():reconcile(c,s,d,lease,proof)
        assert c.execute('SELECT status FROM platform.deep_dub_device_leases WHERE id=%s',(lease['id'],)).fetchone()[0]!='released'


def test_unexpired_owner_lease_cannot_be_recovered(lost):
    s,d,lease,proof=lost
    with s['connect']() as c:
        c.execute("UPDATE platform.deep_dub_device_leases SET expires_at=clock_timestamp()+interval '1 minute' WHERE id=%s",(lease['id'],))
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):
            with c.transaction():reconcile(c,s,d,lease,proof)
