"""第二套识别的待审能力仍仅保存证据，合成数据库夹具不代表模型或质量。"""
from uuid import uuid4
import psycopg
import pytest
from psycopg.types.json import Jsonb
from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed,put
from tests.test_deep_dub_execution import executions,work,device,acquire,finish
from tests.test_deep_dub_artifacts import artifacts
from tests.test_deep_dub_sources import candidates,source_work,source,CUES
from tests.test_deep_dub_source_recheck_contracts import pending,start


@pytest.fixture
def independent(pending):
    with pending['connect']() as c:
        for _ in range(2):c.execute((ROOT/'apps/deep_dub/migrations/017_source_independent_asr.sql').read_text(encoding='utf-8'),prepare=False)
    return pending


@pytest.mark.parametrize('kind',[None,'cpu'])
def test_pending_independent_asr_rejects_non_gpu(independent,kind):
    s=independent
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):start(c,s,node='source_independent_asr',kind=kind)


def test_second_model_can_lease_and_finish_evidence_without_adopting_source(independent):
    s=independent
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=start(c,s,node='source_independent_asr');target=device(c,s,kind='gpu')
        leased,token=acquire(c,s,target,attempt=attempt['id']);assert leased['acquired'];lease=leased['lease']
        path=f"{s['workspace_id']}/{s['project']}/{attempt['id']}/"+'e'*64
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is True
        with pytest.raises(psycopg.Error,match='只能保存证据'):
            with c.transaction():c.execute('SELECT platform.deep_dub_register_transcript(%s,%s,%s,%s,%s,%s,%s)',
                (s['workspace_id'],attempt['id'],s['source'],'not-approved',Jsonb(CUES),Jsonb([{'cue_numbers':[0,1]}]),str(uuid4())))
        assert finish(c,s,attempt=attempt['id'],lease=lease['id'],fence=lease['fence'],token=token)['committed']
        assert c.execute('SELECT id,status,queued_at,technical_verified_at FROM platform.deep_dub_runs WHERE id=%s',(s['run'],)).fetchone()==s['waited']
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is False
