"""独立库验证待审听音复核的能力边界；不代表真实内容质量。"""
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed,put
from tests.test_deep_dub_execution import executions,work,device,acquire,finish
from tests.test_deep_dub_artifacts import artifacts
from tests.test_deep_dub_sources import candidates,source_work,source,CUES
from tests.test_deep_dub_source_recheck_contracts import pending,start


@pytest.fixture
def auditory(pending):
    with pending['connect']() as c:
        for _ in range(2):c.execute((ROOT/'apps/deep_dub/migrations/020_source_content_review.sql').read_text(encoding='utf-8'),prepare=False)
    return pending


@pytest.mark.parametrize('kind,partition',[(None,None),('cpu',None),('gpu','not-source'),('gpu',str(uuid4()))])
def test_pending_auditory_review_rejects_non_gpu_or_other_source(auditory,kind,partition):
    s=auditory
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.Error):start(c,s,node='source_content_review',kind=kind,partition=partition)


def test_current_source_gpu_can_only_save_evidence_and_preserves_wait(auditory):
    s=auditory
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=start(c,s,node='source_content_review');target=device(c,s,kind='gpu')
        leased,token=acquire(c,s,target,attempt=attempt['id']);assert leased['acquired'];lease=leased['lease']
        path=f"{s['workspace_id']}/{s['project']}/{attempt['id']}/"+'d'*64
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is True
        with pytest.raises(psycopg.Error,match='只能保存证据'):
            with c.transaction():c.execute('SELECT platform.deep_dub_register_transcript(%s,%s,%s,%s,%s,%s,%s)',
                (s['workspace_id'],attempt['id'],s['source'],'not-approved',Jsonb(CUES),Jsonb([{'cue_numbers':[0,1]}]),str(uuid4())))
        assert finish(c,s,attempt=attempt['id'],lease=lease['id'],fence=lease['fence'],token=token)['committed']
        assert c.execute('SELECT id,status,queued_at,technical_verified_at FROM platform.deep_dub_runs WHERE id=%s',(s['run'],)).fetchone()==s['waited']
        assert c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0] is False
