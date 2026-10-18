"""独立库验证服务槽位与整卡探针分离；不推理或接触真实GPU。"""
from copy import deepcopy
from uuid import uuid4
import pytest,psycopg
from psycopg.types.json import Jsonb
from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed
from tests.test_deep_dub_execution import executions,work,device,acquire,begin


PROBE={'device_scope':'resident-model-service-slot','all_owned_operations_stopped':True,
 'has_external_processes':True,'resident_service_preserved':True,'physical_gpu_exclusive':False,
 'host_boot_id':'fixture-boot','port':8080,'service_identity':{'pid':123,'start_ticks':456,'boot_id':'fixture-boot',
 'executable_sha256':'a'*64,'weights_sha256':'b'*64}}


@pytest.fixture
def prepared(work):
    with work['connect']() as c:
        for _ in range(2):c.execute((ROOT/'apps/deep_dub/migrations/022_resident_service_probe.sql').read_text(),prepare=False)
        actor(c,work['created_by'])
        attempt=begin(c,work,'gpu')
        host='fixture-'+uuid4().hex
        d=c.execute('SELECT to_jsonb(platform.deep_dub_register_device(%s,%s,%s,%s,%s))',
            (work['workspace_id'],'anp-resident-tts:'+host+':8080',host,'gpu',1)).fetchone()[0]
    return {**work,'attempt':attempt['id'],'stage':attempt['stage_id']},d


def confirm(c,s,d,proof):
    return c.execute('SELECT to_jsonb(platform.deep_dub_confirm_resident_probe(%s,%s,%s,%s))',
        (s['workspace_id'],d['id'],d['row_version'],Jsonb(proof))).fetchone()[0]


def test_resident_scope_preserves_external_process_truth_and_rejects_legacy(prepared):
    s,d=prepared
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):
            with c.transaction():c.execute('SELECT platform.deep_dub_confirm_device_probe(%s,%s,%s,%s)',
                (s['workspace_id'],d['id'],d['row_version'],Jsonb(PROBE)))
        ready=confirm(c,s,d,PROBE)
        assert ready['state']=='ready' and ready['probe']['has_external_processes'] is True
        assert ready['probe']['physical_gpu_exclusive'] is False
        with pytest.raises(psycopg.Error):
            with c.transaction():confirm(c,s,d,PROBE)


@pytest.mark.parametrize('field,value',[('has_external_processes',False),('all_owned_operations_stopped',False),
 ('resident_service_preserved',False),('physical_gpu_exclusive',True),('port',8081),('port','8080'),('device_scope','physical-gpu')])
def test_wrong_scope_or_incomplete_stop_proof_rejected(prepared,field,value):
    s,d=prepared;probe=deepcopy(PROBE);probe[field]=value
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):
            with c.transaction():confirm(c,s,d,probe)
        assert c.execute('SELECT state FROM platform.deep_dub_devices WHERE id=%s',(d['id'],)).fetchone()[0]!='ready'


def test_ordinary_physical_device_cannot_use_resident_probe(prepared):
    s,d=prepared
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s)
        physical=c.execute('SELECT to_jsonb(d) FROM platform.deep_dub_devices d WHERE id=%s',(target,)).fetchone()[0]
        with pytest.raises(psycopg.Error):
            with c.transaction():confirm(c,s,physical,PROBE)


def test_active_lease_and_quarantined_service_not_auto_recovered(prepared):
    s,d=prepared
    with s['connect']() as c:
        actor(c,s['created_by']);ready=confirm(c,s,d,PROBE)
        acquire(c,s,d['id'])
        current=c.execute('SELECT to_jsonb(d) FROM platform.deep_dub_devices d WHERE id=%s',(d['id'],)).fetchone()[0]
        with pytest.raises(psycopg.Error):
            with c.transaction():confirm(c,s,current,PROBE)
        c.execute('RESET ROLE');c.execute("UPDATE platform.deep_dub_devices SET state='quarantined' WHERE id=%s",(d['id'],))
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):
            with c.transaction():confirm(c,s,current,PROBE)
