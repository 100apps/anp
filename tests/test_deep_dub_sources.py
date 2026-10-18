"""独立库来源/原文合成契约；不下载或验收真实媒体。"""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.types.json import Jsonb
import pytest

from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed,put
from tests.test_deep_dub_execution import executions,work,begin
from tests.test_deep_dub_artifacts import artifacts


@pytest.fixture(scope='module')
def candidates(artifacts):
    with artifacts[0]() as c:
        for _ in range(2):
            for name in ['009_source_candidates.sql','010_translation_candidates.sql']:
                c.execute((ROOT/'apps/deep_dub/migrations'/name).read_text(encoding='utf8'),prepare=False)
    return artifacts


@pytest.fixture
def source_work(candidates,work):
    s=work
    # seed.run 已有合成来源，另建真正走排队契约的无来源运行；只在临时库。
    with s['connect']() as c:
        actor(c,s['created_by'])
        run=c.execute('SELECT id FROM platform.deep_dub_queue_run(%s,%s,%s,%s,%s,%s,1,%s)',
            (s['workspace_id'],s['project'],s['profile'],'local','acceptance','fixture-source',str(uuid4()))).fetchone()[0]
        c.execute('SELECT id FROM platform.deep_dub_start_run(%s,%s)',(s['workspace_id'],run))
        s={**s,'run':run};attempt=begin(c,s,kind=None)
        c.execute("SELECT id FROM platform.deep_dub_mark_stage(%s,%s,'started')",(s['workspace_id'],attempt['id']))
        c.execute('RESET ROLE')
        artifact=put(c,'artifacts',workspace_id=s['workspace_id'],project_id=s['project'],created_by=s['created_by'],
            stage_attempt_id=attempt['id'],object_path='fixture/'+str(uuid4()),sha256='d'*64,bytes=1,media_type='video/mp4',duration_ms=10000)
    return {**s,'attempt':attempt['id'],'artifact':artifact}


def source(c,s,key=None,duration=10000):
    return c.execute('SELECT to_jsonb(platform.deep_dub_register_source(%s,%s,%s,%s,%s,%s))',
        (s['workspace_id'],s['attempt'],s['artifact'],duration,Jsonb({'fixture_only':True}),key or str(uuid4()))).fetchone()[0]


CUES=[{'cue_no':0,'speaker_key':'teacher','source_text':'Twelve samples remain.','start_ms':1000,'end_ms':2000},
      {'cue_no':1,'speaker_key':'teacher','source_text':'Do not remove them.','start_ms':2000,'end_ms':3500}]


def transcript(c,s,source_id,key=None,cues=None,segments=None):
    return c.execute('SELECT to_jsonb(platform.deep_dub_register_transcript(%s,%s,%s,%s,%s,%s,%s))',
        (s['workspace_id'],s['attempt'],source_id,'fixture-seg-v1',Jsonb(CUES if cues is None else cues),
         Jsonb([{'cue_numbers':[0,1]}] if segments is None else segments),key or str(uuid4()))).fetchone()[0]


def test_source_binds_once_to_frozen_url_and_replays(source_work):
    s=source_work;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);first=source(c,s,key)
        assert first==source(c,s,key)
        assert str(c.execute('SELECT source_id FROM platform.deep_dub_runs WHERE id=%s',(s['run'],)).fetchone()[0])==first['id']
        assert first['source_url']==c.execute('SELECT source_url FROM platform.deep_dub_projects WHERE id=%s',(s['project'],)).fetchone()[0]
        with pytest.raises(psycopg.DatabaseError) as changed:
            with c.transaction():source(c,s)
        assert changed.value.sqlstate=='PT409'


def test_source_rejects_other_attempt_and_duration_mismatch(source_work):
    s=source_work
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():source(c,s,duration=999)
        foreign=begin(c,s,kind=None);c.execute("SELECT id FROM platform.deep_dub_mark_stage(%s,%s,'started')",(s['workspace_id'],foreign['id']))
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():source(c,{**s,'attempt':foreign['id']})


def test_transcript_derives_immutable_segment_text_window_and_links(source_work):
    s=source_work;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);original=source(c,s);first=transcript(c,s,original['id'],key)
        assert first['sealed'] and first==transcript(c,s,original['id'],key)
        row=c.execute('SELECT source_text,start_ms,end_ms FROM platform.deep_dub_segments WHERE segment_set_id=%s',(first['id'],)).fetchone()
        assert row==('Twelve samples remain. Do not remove them.',1000,3500)
        assert c.execute('SELECT count(*) FROM platform.deep_dub_segment_cues WHERE segment_id IN (SELECT id FROM platform.deep_dub_segments WHERE segment_set_id=%s)',(first['id'],)).fetchone()[0]==2
        separate=transcript(c,s,original['id'],segments=[{'cue_numbers':[0]},{'cue_numbers':[1]}])
        assert separate['revision']==first['revision']+1


@pytest.mark.parametrize('segments',[[{'cue_numbers':[0]}],[{'cue_numbers':[0,0]}],[{'cue_numbers':[1,0]}],[{'cue_numbers':[0,2]}]])
def test_incomplete_duplicate_reordered_or_unknown_cues_rejected_atomically(source_work,segments):
    s=source_work
    with s['connect']() as c:
        actor(c,s['created_by']);original=source(c,s)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():transcript(c,s,original['id'],segments=segments)
        assert c.execute('SELECT count(*) FROM platform.deep_dub_cues WHERE source_id=%s',(original['id'],)).fetchone()[0]==0


def test_roles_and_source_windows_cannot_be_merged_or_exceeded(source_work):
    s=source_work
    with s['connect']() as c:
        actor(c,s['created_by']);original=source(c,s)
        for cues in [[CUES[0],{**CUES[1],'speaker_key':'student'}],[CUES[0],{**CUES[1],'end_ms':20000}]]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with c.transaction():transcript(c,s,original['id'],cues=cues)


def test_source_and_segment_set_concurrent_idempotence(source_work):
    s=source_work;key=str(uuid4())
    def submit(_):
        with s['connect']() as c:
            actor(c,s['created_by']);return source(c,s,key)
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(submit,range(8)))
    assert all(row==rows[0] for row in rows)
    key=str(uuid4())
    def split(_):
        with s['connect']() as c:
            actor(c,s['created_by']);return transcript(c,s,rows[0]['id'],key)
    with ThreadPoolExecutor(max_workers=4) as pool:sets=list(pool.map(split,range(8)))
    assert all(row==sets[0] for row in sets)


def translation(c,s,segment,context,key=None,text='还剩十二份样本，不要移除。'):
    return c.execute('SELECT to_jsonb(platform.deep_dub_register_translation(%s,%s,%s,%s,%s,%s,%s,%s))',
        (s['workspace_id'],s['attempt'],segment,context,text,'fixture-model','fixture-prompt-v1',key or str(uuid4()))).fetchone()[0]


def test_translation_is_unreviewed_version_with_context_and_idempotence(source_work):
    s=source_work;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);original=source(c,s);parts=transcript(c,s,original['id'])
        segment=c.execute('SELECT id FROM platform.deep_dub_segments WHERE segment_set_id=%s',(parts['id'],)).fetchone()[0]
        c.execute('RESET ROLE')
        context=put(c,'artifacts',workspace_id=s['workspace_id'],project_id=s['project'],created_by=s['created_by'],
            stage_attempt_id=s['attempt'],object_path='fixture/'+str(uuid4()),sha256='e'*64,bytes=2,media_type='application/json')
        actor(c,s['created_by']);first=translation(c,s,segment,context,key)
        assert first==translation(c,s,segment,context,key)
        second=translation(c,s,segment,context,text='不要移除剩下的十二份样本。')
        assert second['revision']==first['revision']+1 and first['id']!=second['id']
        assert c.execute('SELECT count(*) FROM platform.deep_dub_audits WHERE translation_id=ANY(%s::uuid[])',([first['id'],second['id']],)).fetchone()[0]==0
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction():translation(c,s,segment,context,key,text='不同内容')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():translation(c,s,segment,s['artifact'])


def test_registered_cues_cannot_be_changed_or_omitted(source_work):
    s=source_work
    with s['connect']() as c:
        actor(c,s['created_by']);original=source(c,s);transcript(c,s,original['id'])
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction():transcript(c,s,original['id'],cues=[{**CUES[0],'source_text':'changed'},CUES[1]])
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():transcript(c,s,original['id'],cues=[CUES[0]],segments=[{'cue_numbers':[0]}])
