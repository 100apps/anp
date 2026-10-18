"""独立数据库背景与成片登记夹具；不渲染媒体，不记质量/发布通过。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed,put,all_audits,select,timeline
from tests.test_deep_dub_execution import executions,work
from tests.test_deep_dub_artifacts import artifacts
from tests.test_deep_dub_sources import candidates
from tests.test_deep_dub_audio_candidates import media,leased,artifact


@pytest.fixture(scope='module')
def renders(media):
    with media[0]() as c:
        for _ in range(2):c.execute((ROOT/'apps/deep_dub/migrations/012_render_candidates.sql').read_text(),prepare=False)
    return media


@pytest.fixture
def assembled(renders,leased):
    s=leased
    with s['connect']() as c:
        actor(c,s['created_by']);chosen=select(c,s,all_audits(c,s));snapshot=timeline(c,s,chosen[0],2)
        items={key:artifact(c,s,duration,kind) for key,duration,kind in [
            ('original',10000,'audio/wav'),('processed',10000,'audio/wav'),('removed',10000,'audio/wav'),
            ('dialogue',10000,'audio/wav'),('mix',10000,'audio/wav'),('subtitle',None,'application/x-subrip'),
            ('video',10000,'video/mp4'),('receipt',None,'application/json')]}
    return {**s,**items,'timeline':snapshot[0]}


def background(c,s,key=None,original=None,removed=None,model='fixture-separation'):
    return c.execute('SELECT to_jsonb(platform.deep_dub_register_background(%s,%s,%s,%s,%s,%s,%s,%s,%s))',
        (s['workspace_id'],s['attempt'],original or s['original'],s['processed'],removed or s['removed'],s['receipt'],
         model,Jsonb({'fixture_only':True}),key or str(uuid4()))).fetchone()[0]


def render(c,s,bg,key=None,dialogue=None,mix=None,timeline_id=None,video=None):
    return c.execute('SELECT to_jsonb(platform.deep_dub_register_render(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s))',
        (s['workspace_id'],s['attempt'],timeline_id or s['timeline'],bg,'fixture-render',Jsonb({'fixture_only':True}),
         dialogue or s['dialogue'],mix or s['mix'],s['subtitle'],video or s['video'],s['receipt'],key or str(uuid4()))).fetchone()[0]


def test_background_is_versioned_full_duration_and_diagnostic_is_separate(assembled):
    s=assembled;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);first=background(c,s,key)
        assert first==background(c,s,key) and first['source_id']==str(s['source'])
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction():background(c,s,key,model='changed-model')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():background(c,s,original=artifact(c,s,1000))
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():background(c,s,removed=s['processed'])


def test_render_binds_snapshot_and_is_not_automatically_verified(assembled):
    s=assembled;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);bg=background(c,s);first=render(c,s,bg['id'],key)
        assert first==render(c,s,bg['id'],key)
        assert first['run_id']==str(s['run']) and first['timeline_id']==str(s['timeline'])
        assert c.execute('SELECT status,technical_verified_at FROM platform.deep_dub_runs WHERE id=%s',(s['run'],)).fetchone()==('running',None)
        assert c.execute('SELECT count(*) FROM platform.deep_dub_audits WHERE render_id=%s',(first['id'],)).fetchone()[0]==0
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():c.execute('SELECT platform.deep_dub_request_publication(%s,%s,%s)',(s['workspace_id'],first['id'],str(uuid4())))


def test_render_rejects_missing_background_short_audio_or_diagnostic_mix(assembled):
    s=assembled
    with s['connect']() as c:
        actor(c,s['created_by']);bg=background(c,s)
        for options in [{'bg':None},{'bg':bg['id'],'dialogue':artifact(c,s,1000)},
                        {'bg':bg['id'],'mix':s['removed']},{'bg':bg['id'],'video':s['mix']}]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with c.transaction():render(c,s,**options)


def test_unsealed_timeline_and_other_attempt_outputs_cannot_be_rendered(assembled):
    s=assembled
    with s['connect']() as c:
        actor(c,s['created_by']);bg=background(c,s);c.execute('RESET ROLE')
        unsealed=put(c,'timeline_revisions',workspace_id=s['workspace_id'],project_id=s['project'],created_by=s['created_by'],
            source_id=s['source'],segment_set_id=s['segment_set'],quality_profile_id=s['profile'],revision=99,
            input_hash='c'*64,request_key=str(uuid4()),request_hash='c'*64,project_version=3)
        actor(c,s['created_by'])
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():render(c,s,bg['id'],timeline_id=unsealed)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():render(c,s,bg['id'],dialogue=s['evidence'])


def test_render_concurrent_idempotence_keeps_one_candidate(assembled):
    s=assembled;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);bg=background(c,s)
    def submit(_):
        with s['connect']() as c:
            actor(c,s['created_by']);return render(c,s,bg['id'],key)
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(submit,range(8)))
    assert all(row==rows[0] for row in rows)
