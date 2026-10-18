"""独立库合成设备验证待审返修能力；不证明真实模型、媒体或质量。"""
from uuid import uuid4
import psycopg
import pytest
from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed,put
from tests.test_deep_dub_execution import executions,work,device,acquire,finish
from tests.test_deep_dub_artifacts import artifacts
from tests.test_deep_dub_sources import candidates,source_work,source,CUES
from psycopg.types.json import Jsonb


@pytest.fixture
def pending(source_work):
    s=source_work
    with s['connect']() as c:
        for _ in range(2):
            for name in ('015_source_preparation_wait.sql','016_source_boundary_recheck.sql'):
                c.execute((ROOT/'apps/deep_dub/migrations'/name).read_text(encoding='utf-8'),prepare=False)
        actor(c,s['created_by']);src=source(c,s)
        finish(c,s,attempt=s['attempt'],outputs=[s['artifact']])
        review=c.execute('SELECT platform.deep_dub_begin_stage(%s,%s,%s,%s,%s,%s,%s)',
            (s['workspace_id'],s['run'],'source_preparation_review',str(uuid4()),'b'*64,None,str(uuid4()))).fetchone()[0]['attempt']
        c.execute("SELECT id FROM platform.deep_dub_mark_stage(%s,%s,'started')",(s['workspace_id'],review['id']))
        c.execute('RESET ROLE')
        note=put(c,'artifacts',workspace_id=s['workspace_id'],project_id=s['project'],stage_attempt_id=review['id'],
            created_by=s['created_by'],object_path='fixture/'+str(uuid4()),sha256='c'*64,bytes=10,media_type='application/json')
        actor(c,s['created_by']);finish(c,s,status='waiting_review',attempt=review['id'],outputs=[note])
        waited=c.execute('SELECT id,status,queued_at,technical_verified_at FROM platform.deep_dub_wait_for_source_review(%s,%s,%s)',(s['workspace_id'],s['run'],review['id'])).fetchone()
    return {**s,'source':src['id'],'waited':waited}


def start(c,s,node='source_boundary_recheck',kind='gpu',partition=None):
    return c.execute('SELECT platform.deep_dub_begin_stage(%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],s['run'],node,partition or s['source'],'d'*64,kind,str(uuid4()))).fetchone()[0]['attempt']


@pytest.mark.parametrize('node,kind,partition',[('translate','gpu',None),('source_boundary_recheck',None,None),
    ('source_boundary_recheck','cpu',None),('source_boundary_recheck','gpu','not-source')])
def test_pending_review_rejects_other_production_node_kind_or_source(pending,node,kind,partition):
    s=pending
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):start(c,s,node,kind,partition)


def test_only_recheck_evidence_lease_and_no_source_or_transcript_adoption(pending):
    s=pending
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=start(c,s)
        target=device(c,s,kind='gpu');leased,token=acquire(c,s,target,attempt=attempt['id'])
        assert leased['acquired'];lease=leased['lease']
        path=f"{s['workspace_id']}/{s['project']}/{attempt['id']}/"+'e'*64
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is True
        for query,args in [
            ('SELECT platform.deep_dub_register_source(%s,%s,%s,%s,%s,%s)',(s['workspace_id'],attempt['id'],s['artifact'],10000,Jsonb({'fixture':True}),str(uuid4()))),
            ('SELECT platform.deep_dub_register_transcript(%s,%s,%s,%s,%s,%s,%s)',(s['workspace_id'],attempt['id'],s['source'],'must-not-adopt',Jsonb(CUES),Jsonb([{'cue_numbers':[0,1]}]),str(uuid4())))]:
            with pytest.raises(psycopg.Error,match='只能保存证据'):
                with c.transaction():c.execute(query,args)
        assert c.execute('SELECT platform.deep_dub_heartbeat_lease(%s,%s,%s,%s,60)',(s['workspace_id'],lease['id'],lease['fence'],token)).fetchone()[0]['renewed']
        assert finish(c,s,attempt=attempt['id'],lease=lease['id'],fence=lease['fence'],token=token)['committed']
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is False
        assert c.execute('SELECT id,status,queued_at,technical_verified_at FROM platform.deep_dub_runs WHERE id=%s',(s['run'],)).fetchone()==s['waited']
        assert c.execute('SELECT count(*) FROM platform.deep_dub_segment_sets WHERE project_id=%s AND source_id=%s',(s['project'],s['source'])).fetchone()[0]==0


def test_review_revocation_and_run_cancel_stop_lease_renewal(pending):
    s=pending
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=start(c,s);target=device(c,s,kind='gpu')
        leased,token=acquire(c,s,target,attempt=attempt['id']);lease=leased['lease']
        path=f"{s['workspace_id']}/{s['project']}/{attempt['id']}/"+'e'*64
        c.execute('RESET ROLE');c.execute("UPDATE platform.members SET role='viewer' WHERE workspace_id=%s AND user_id=%s",(s['workspace_id'],s['created_by']))
        actor(c,s['created_by'])
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is False
        with pytest.raises(psycopg.Error):
            with c.transaction():c.execute('SELECT platform.deep_dub_heartbeat_lease(%s,%s,%s,%s,60)',(s['workspace_id'],lease['id'],lease['fence'],token))
        c.execute('RESET ROLE');c.execute("UPDATE platform.members SET role='admin' WHERE workspace_id=%s AND user_id=%s",(s['workspace_id'],s['created_by']))
        actor(c,s['created_by']);c.execute('SELECT platform.deep_dub_cancel_run(%s,%s)',(s['workspace_id'],s['run']))
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is False
        with pytest.raises(psycopg.Error):
            with c.transaction():c.execute('SELECT platform.deep_dub_heartbeat_lease(%s,%s,%s,%s,60)',(s['workspace_id'],lease['id'],lease['fence'],token))
