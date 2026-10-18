"""独立临时库中的人工改稿正向/并发/权限回归，不接触正式生产资料。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from anp import ROOT
from apps.deep_dub.services.editor import TranslationEdit
from tests.test_deep_dub_foundation import database, actor, pytestmark
from tests.test_deep_dub_versions import versions, seed, all_audits, select


@pytest.fixture
def editable(seed):
    with seed['connect']() as connection:
        for _ in range(2):
            connection.execute((ROOT/'apps/deep_dub/migrations/013_editor_translation.sql').read_text(),prepare=False)
            connection.execute((ROOT/'apps/deep_dub/migrations/014_studio_snapshot.sql').read_text(),prepare=False)
    return seed


def save(c,s,*,key=None,text='还剩十二个样本，请核对。',base=None,revision=1,expected=1,project=None,workspace=None):
    return c.execute('SELECT id,translation_id,project_version FROM platform.deep_dub_edit_translation(%s,%s,%s,%s,%s,%s,%s,%s)',
        (workspace or s['workspace_id'],project or s['project'],s['segment'],
         s['translation'] if base is None and revision else base,revision,expected,text,key or str(uuid4()))).fetchone()


def test_manual_revision_keeps_exact_old_audit_and_audio(editable):
    s=editable;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by'])
        original_hash=c.execute("SELECT input_hash FROM platform.deep_dub_translations WHERE id=%s",(s['translation'],)).fetchone()[0]
        audit_ids=all_audits(c,s)
        selected,version=select(c,s,audit_ids)
        result=save(c,s,key=key,expected=version)
        assert result[2]==version+1
        assert save(c,s,key=key,expected=version)==result  # 成功后重试仍返回原回执。
        original=c.execute('SELECT chinese_text,input_hash FROM platform.deep_dub_translations WHERE id=%s',(s['translation'],)).fetchone()
        assert original==('还剩十二个样本。',original_hash)
        new=c.execute('SELECT revision,chinese_text,model_id,created_by FROM platform.deep_dub_translations WHERE id=%s',(result[1],)).fetchone()
        assert new==(2,'还剩十二个样本，请核对。','human:editor',s['created_by'])
        assert c.execute('SELECT translation_id FROM platform.deep_dub_audio_attempts WHERE id=%s',(s['audio'],)).fetchone()[0]==s['translation']
        assert c.execute('SELECT translation_id,sealed FROM platform.deep_dub_selections WHERE id=%s',(selected,)).fetchone()==(s['translation'],True)
        assert c.execute('SELECT count(*) FROM platform.deep_dub_audits WHERE translation_id=%s',(result[1],)).fetchone()[0]==0
        # 老审核的对象摘要仍一致；摘要函数刻意不向普通业务用户开放。
        c.execute('RESET ROLE')
        assert c.execute("SELECT count(*) FROM platform.deep_dub_audits WHERE translation_id=%s AND subject_hash=platform.deep_dub_subject_hash(%s,'translation',%s)",
            (s['translation'],s['workspace_id'],s['translation'])).fetchone()[0]==6


@pytest.mark.parametrize('change',[{'expected':9},{'revision':8},{'base':str(uuid4())}])
def test_stale_project_or_translation_is_rejected(editable,change):
    s=editable
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.DatabaseError) as error:save(c,s,**change)
        assert error.value.sqlstate=='PT409'


def test_same_key_changed_body_conflicts_and_receipt_is_immutable(editable):
    s=editable;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);result=save(c,s,key=key)
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.DatabaseError) as error:save(c,s,key=key,text='不同的重试内容。')
        assert error.value.sqlstate=='PT409'
    with s['connect']() as c:
        with pytest.raises(psycopg.DatabaseError):c.execute('UPDATE platform.deep_dub_translation_edits SET request_key=%s WHERE id=%s',(str(uuid4()),result[0]))


def test_two_editors_cannot_silently_overwrite(editable):
    s=editable
    def operation(n):
        try:
            with s['connect']() as c:
                actor(c,s['created_by']);save(c,s,text=f'并发候选 {n}。')
            return 'created'
        except psycopg.DatabaseError as error:return error.sqlstate
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(operation,range(2)))==['PT409','created']
    with s['connect']() as c:
        assert c.execute('SELECT row_version FROM platform.deep_dub_projects WHERE id=%s',(s['project'],)).fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM platform.deep_dub_translations WHERE segment_id=%s',(s['segment'],)).fetchone()[0]==2


def test_first_translation_without_base_is_supported(editable):
    s=editable
    # 不删除原历史；在同一个已封存分段集中不能新增段，使用此测试独立的空译稿项目种子。
    from tests.test_deep_dub_versions import put
    with s['connect']() as c:
        common={k:s[k] for k in ['workspace_id','project_id','created_by']}
        segment_set=put(c,'segment_sets',**common,source_id=s['source'],revision=2,strategy_revision='editor-empty-fixture',input_hash='b'*64)
        segment=put(c,'segments',**common,source_id=s['source'],segment_set_id=segment_set,speaker_id=s['speaker'],segment_no=0,
            source_text='Initial draft.',start_ms=1000,end_ms=2000)
        c.execute('UPDATE platform.deep_dub_segment_sets SET sealed=true WHERE id=%s',(segment_set,))
        actor(c,s['created_by']);result=save(c,{**s,'segment':segment},revision=0,text='首次人工译稿。')
        assert c.execute('SELECT revision,chinese_text FROM platform.deep_dub_translations WHERE id=%s',(result[1],)).fetchone()==(1,'首次人工译稿。')


def test_cross_workspace_and_readonly_identity_are_denied(editable):
    s=editable
    for user,workspace in [(s['users'][1],s['workspace_id']),(s['created_by'],s['workspaces'][1])]:
        with s['connect']() as c:
            actor(c,user)
            with pytest.raises(psycopg.DatabaseError) as error:save(c,s,workspace=workspace)
            assert error.value.sqlstate=='42501'
    with s['connect']() as c:
        actor(c,s['users'][1])
        assert c.execute('SELECT count(*) FROM platform.deep_dub_translation_edits WHERE project_id=%s',(s['project'],)).fetchone()[0]==0


def test_contract_rejects_ambiguous_or_empty_edit():
    from pydantic import ValidationError
    for body in [dict(expected_revision=1,base_translation_id=None,chinese_text='测试。'),
                 dict(expected_revision=0,base_translation_id=str(uuid4()),chinese_text='测试。'),
                 dict(expected_revision=0,chinese_text='  '),dict(expected_revision=True,chinese_text='测试。')]:
        with pytest.raises(ValidationError):TranslationEdit.model_validate({'expected_project_version':1,**body})


def snapshot(c,s,source=None,parts=None,profile=None,timeline=None):
    return c.execute('SELECT platform.deep_dub_studio_snapshot(%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],s['project'],source,parts,profile,timeline)).fetchone()[0]


def test_studio_snapshot_keeps_edit_history_and_hides_object_paths(editable):
    s=editable
    with s['connect']() as c:
        actor(c,s['created_by']);receipt=save(c,s)
        view=snapshot(c,s)
        assert view['schema_version']==1 and view['project']['row_version']==receipt[2]
        assert view['source']['id']==str(s['source']) and view['segment_set']['id']==str(s['segment_set'])
        assert [r['id'] for r in view['segments']]==[str(s['segment'])]
        assert [r['revision'] for r in view['translations']]==[1,2]
        assert view['translation_edits'][0]['translation_id']==str(receipt[1])
        assert view['audio_attempts'][0]['translation_id']==str(s['translation'])
        assert view['artifacts'] and all('object_path' not in row for row in view['artifacts'])
        assert view['timeline'] is None and view['renders']==[] and view['audits']==[]


def test_new_source_does_not_silently_reuse_old_segments(editable):
    from tests.test_deep_dub_versions import put
    s=editable
    with s['connect']() as c:
        artifact=c.execute('SELECT artifact_id FROM platform.deep_dub_sources WHERE id=%s',(s['source'],)).fetchone()[0]
        source=put(c,'sources',workspace_id=s['workspace_id'],project_id=s['project'],created_by=s['created_by'],revision=2,
            source_url='fixture://second-source',artifact_id=artifact,duration_ms=10000,identity_evidence={'fixture_only':True})
        actor(c,s['created_by']);view=snapshot(c,s)
        assert view['source']['id']==str(source) and view['segments']==[] and view['segment_set'] is None
        assert snapshot(c,s,source=s['source'])['segments'][0]['id']==str(s['segment'])
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.DatabaseError) as error:snapshot(c,s,source=source,parts=s['segment_set'])
        assert error.value.sqlstate=='PT404'


def test_snapshot_timeline_cannot_mix_quality_profile(editable):
    from tests.test_deep_dub_versions import timeline
    s=editable
    with s['connect']() as c:
        actor(c,s['created_by']);selection,version=select(c,s,all_audits(c,s))
        chosen=timeline(c,s,selection,version)[0]
        view=snapshot(c,s,timeline=chosen)
        assert view['timeline']['id']==str(chosen) and view['quality_profile']['id']==str(s['profile'])
        assert view['timeline_clips'][0]['selection_id']==str(selection)
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.DatabaseError) as error:snapshot(c,s,timeline=chosen,profile=uuid4())
        assert error.value.sqlstate=='PT409'


def test_snapshot_foreign_workspace_is_not_readable(editable):
    s=editable
    with s['connect']() as c:
        actor(c,s['users'][1])
        assert snapshot(c,s)['project']['id']==str(s['project'])  # viewer 可读同工作空间。
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.DatabaseError) as error:snapshot(c,{**s,'workspace_id':s['workspaces'][1]})
        assert error.value.sqlstate=='42501'
