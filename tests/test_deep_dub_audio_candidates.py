"""独立库参考与音频元数据夹具；不生成声音、不占用任何实际设备。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed,put
from tests.test_deep_dub_execution import executions,work,begin,device,acquire,expire
from tests.test_deep_dub_artifacts import artifacts
from tests.test_deep_dub_sources import candidates


@pytest.fixture(scope='module')
def media(candidates):
    with candidates[0]() as c:
        for _ in range(2):c.execute((ROOT/'apps/deep_dub/migrations/011_audio_candidates.sql').read_text(),prepare=False)
    return candidates


@pytest.fixture
def leased(media,work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s);lease,token=acquire(c,s,target)
        c.execute("SELECT id FROM platform.deep_dub_mark_stage(%s,%s,'started')",(s['workspace_id'],s['attempt']))
    return {**s,'lease':lease['lease']['id'],'token':token}


def artifact(c,s,duration=1000,kind='audio/wav',attempt=None):
    c.execute('RESET ROLE')
    result=put(c,'artifacts',workspace_id=s['workspace_id'],project_id=s['project'],created_by=s['created_by'],
        stage_attempt_id=attempt or s['attempt'],object_path='fixture/'+str(uuid4()),sha256='e'*64,bytes=1,media_type=kind,duration_ms=duration)
    actor(c,s['created_by']);return result


def reference(c,s,kind='voice',subject=None,start=1000,end=3500,original=None,processed=None,key=None):
    return c.execute('SELECT platform.deep_dub_register_reference(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],s['attempt'],kind,s['source'],subject or s['speaker' if kind=='voice' else 'segment'],
         start,end,original,processed or original,Jsonb({'fixture_only':True}),key or str(uuid4()))).fetchone()[0]


def audio(c,s,wav,evidence,key=None,kind='generation',parent=None,generation=1,tempo=1,translation=None):
    return c.execute('SELECT to_jsonb(platform.deep_dub_register_audio(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::numeric,%s::numeric,%s,%s))',
        (s['workspace_id'],s['attempt'],s['segment'],translation or s['translation'],s['voice'],s['emotion'],wav,evidence,
         kind,parent,generation,tempo,Jsonb({'model_id':'fixture-only','not_real_generation':True}),key or str(uuid4()))).fetchone()[0]


def test_reference_versions_preserve_original_and_accurate_emotion_window(leased):
    s=leased;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);original=artifact(c,s,2500);processed=artifact(c,s,2200)
        voice=reference(c,s,original=original,processed=processed,key=key)
        assert voice==reference(c,s,original=original,processed=processed,key=key)
        emotion=reference(c,s,kind='emotion',original=original,processed=processed)
        assert emotion['original_artifact_id']==str(original) and emotion['artifact_id']==str(processed)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():reference(c,s,kind='emotion',start=1200,end=3700,original=original)
        wrong=artifact(c,s,1000)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():reference(c,s,original=wrong)


def test_reference_cannot_attach_other_attempt_or_absent_role(leased):
    s=leased
    with s['connect']() as c:
        actor(c,s['created_by']);original=s['evidence']
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():reference(c,s,original=original)
        original=artifact(c,s,2500)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction():reference(c,s,subject=uuid4(),original=original)


def test_audio_generation_is_unreviewed_and_idempotent(leased):
    s=leased;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);wav=artifact(c,s);evidence=artifact(c,s,None,'application/json')
        first=audio(c,s,wav,evidence,key);assert first==audio(c,s,wav,evidence,key)
        assert first['duration_ms']==1000 and first['pcm_total_factor']==1
        assert c.execute('SELECT count(*) FROM platform.deep_dub_audits WHERE audio_attempt_id=%s',(first['id'],)).fetchone()[0]==0
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction():audio(c,s,wav,evidence,key,generation=1.1)


def test_pcm_chain_cannot_bypass_cumulative_limit_or_change_generation(leased):
    s=leased
    with s['connect']() as c:
        actor(c,s['created_by']);evidence=artifact(c,s,None,'application/json');first=audio(c,s,artifact(c,s),evidence)
        child=audio(c,s,artifact(c,s,952),evidence,kind='pcm_tempo',parent=first['id'],tempo=1.05)
        assert child['pcm_total_factor']==1.05
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():audio(c,s,artifact(c,s,907),evidence,kind='pcm_tempo',parent=child['id'],tempo=1.05)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():audio(c,s,artifact(c,s,952),evidence,kind='pcm_tempo',parent=first['id'],tempo=1.05,generation=1.1)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():audio(c,s,artifact(c,s,2000),evidence,kind='pcm_tempo',parent=first['id'],tempo=1.05)


def test_audio_needs_declared_device_live_lease_and_own_receipt(leased):
    s=leased
    with s['connect']() as c:
        actor(c,s['created_by']);wav=artifact(c,s);evidence=artifact(c,s,None,'application/json')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():audio(c,s,wav,s['evidence'])
        other=begin(c,s,kind=None);c.execute("SELECT id FROM platform.deep_dub_mark_stage(%s,%s,'started')",(s['workspace_id'],other['id']))
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():audio(c,{**s,'attempt':other['id']},wav,evidence)
        c.execute('RESET ROLE');expire(c,s['lease']);actor(c,s['created_by'])
        with pytest.raises(psycopg.DatabaseError) as expired:
            with c.transaction():audio(c,s,wav,evidence)
        assert expired.value.sqlstate=='PT409'


def test_audio_concurrent_replay_returns_one_candidate(leased):
    s=leased;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);wav=artifact(c,s);evidence=artifact(c,s,None,'application/json')
    def submit(_):
        with s['connect']() as c:
            actor(c,s['created_by']);return audio(c,s,wav,evidence,key)
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(submit,range(8)))
    assert all(row==rows[0] for row in rows)
