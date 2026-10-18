"""独立临时库中的合成关系测试；不生成媒体，不记作生产或质量通过。"""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from anp import ROOT
from tests.test_deep_dub_foundation import database, actor, create, pytestmark

TRANSLATION_RULES=['meaning','facts','numbers','negation','names','complete']
AUDIO_RULES=['content','decode','nonsilent','timing','collision','bounds']
RULES={'translation_required':TRANSLATION_RULES,'audio_required':AUDIO_RULES,
       'max_advance_ms':500,'collision_tolerance_ms':50,'background_mode':'preserve','voice_preference':'episode-reference'}


@contextmanager
def conflict():
    with pytest.raises(psycopg.DatabaseError) as raised:
        yield
    assert raised.value.sqlstate=='PT409'


@pytest.fixture(scope='module')
def versions(database):
    connect,users,workspaces=database
    with connect() as connection:
        for _ in range(2):
            for name in ['002_content_versions.sql','003_selection_contracts.sql']:
                connection.execute((ROOT/'apps/deep_dub/migrations'/name).read_text(),prepare=False)
    return database


def put(connection, table, **values):
    columns=list(values)
    query=sql.SQL('INSERT INTO platform.{} ({}) VALUES ({}) RETURNING id').format(
        sql.Identifier('deep_dub_'+table),sql.SQL(',').join(map(sql.Identifier,columns)),
        sql.SQL(',').join(sql.Placeholder() for _ in columns))
    return connection.execute(query,[Jsonb(values[k]) if isinstance(values[k],dict) else values[k] for k in columns]).fetchone()[0]


@pytest.fixture
def seed(versions):
    connect,users,workspaces=versions
    workspace,user=workspaces[0],users[0]
    with connect() as c:
        actor(c,user);project=create(c,workspace,str(uuid4()))
        profile=c.execute('SELECT id FROM platform.deep_dub_create_quality_profile(%s,%s,1,%s,%s)',
            (workspace,'high',Jsonb(RULES),'独立库合成关系测试')).fetchone()[0]
        c.execute('RESET ROLE')
        common={'workspace_id':workspace,'project_id':project,'created_by':user}
        def artifact(kind='audio/wav',duration=1000):
            return put(c,'artifacts',**common,object_path='synthetic/'+str(uuid4()),sha256='a'*64,
                       bytes=1,media_type=kind,duration_ms=duration)
        source=put(c,'sources',**common,revision=1,source_url='fixture://synthetic',artifact_id=artifact('video/mp4',10000),
                   duration_ms=10000,identity_evidence={'fixture_only':True})
        speaker=put(c,'speakers',**common,speaker_key='fixture-speaker',display_name='合成角色')
        segment_set=put(c,'segment_sets',**common,source_id=source,revision=1,strategy_revision='fixture-1',input_hash='a'*64)
        segment=put(c,'segments',**common,source_id=source,segment_set_id=segment_set,speaker_id=speaker,segment_no=0,
                    source_text='Twelve samples remain.',start_ms=1000,end_ms=3500)
        cue=put(c,'cues',**common,source_id=source,speaker_id=speaker,cue_no=0,
                source_text='Twelve samples remain.',start_ms=1000,end_ms=3500)
        put(c,'segment_cues',workspace_id=workspace,project_id=project,source_id=source,segment_id=segment,cue_id=cue,position=0)
        c.execute('UPDATE platform.deep_dub_segment_sets SET sealed=true WHERE id=%s',(segment_set,))
        reference=artifact()
        voice=put(c,'voice_references',**common,speaker_id=speaker,source_id=source,revision=1,start_ms=1000,end_ms=3500,
                  original_artifact_id=reference,processed_artifact_id=reference,input_hash='a'*64)
        emotion=put(c,'emotion_references',**common,source_id=source,segment_id=segment,revision=1,start_ms=1000,end_ms=3500,
                    artifact_id=reference,input_hash='a'*64)
        task=c.execute("INSERT INTO platform.tasks(workspace_id,created_by,app_id,title,input,request_key) VALUES(%s,%s,'deep_dub','合成关系测试','{}',%s) RETURNING id",
                       (workspace,user,str(uuid4()))).fetchone()[0]
        run=put(c,'runs',**common,task_id=task,source_id=source,quality_profile_id=profile,flow_revision='fixture-1',
                input_hash='a'*64,request_key=str(uuid4()),mode='local')
        stage=put(c,'stages',**common,run_id=run,node='tts',input_hash='a'*64)
        attempt=put(c,'stage_attempts',**common,stage_id=stage,attempt_no=1)
        translation=put(c,'translations',**common,segment_id=segment,revision=1,chinese_text='还剩十二个样本。',
                        model_id='合成测试生成器',prompt_revision='fixture-1',input_hash='a'*64)
        audio=put(c,'audio_attempts',**common,segment_id=segment,speaker_id=speaker,translation_id=translation,
                  voice_reference_id=voice,emotion_reference_id=emotion,stage_attempt_id=attempt,kind='generation',
                  parameters={'fixture_only':True},artifact_id=artifact(),duration_ms=1000,input_hash='a'*64)
        evidence=artifact('application/json',None)
    return {**common,'connect':connect,'users':users,'workspaces':workspaces,'project':project,'profile':profile,
            'source':source,'speaker':speaker,'segment_set':segment_set,'segment':segment,'cue':cue,'voice':voice,
            'emotion':emotion,'task':task,'run':run,'stage':stage,'attempt':attempt,'translation':translation,'audio':audio,'evidence':evidence}


def audit(c,s,kind,rule,status='passed',subject=None,key=None,method=None,evidence=True):
    return c.execute('SELECT id FROM platform.deep_dub_record_audit(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],s['project'],kind,subject or s[kind],s['profile'],rule,'fixture-1',status,
         method or ('independent_model' if kind=='translation' else 'technical'),s['evidence'] if evidence else None,
         Jsonb({'fixture_only':True}),key or str(uuid4()))).fetchone()[0]


def all_audits(c,s):
    return [audit(c,s,'translation',rule) for rule in TRANSLATION_RULES]+[audit(c,s,'audio',rule) for rule in AUDIO_RULES]


def select(c,s,audits,expected=1,key=None,translation=None,audio=None):
    return c.execute('SELECT id,project_version FROM platform.deep_dub_select_version(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],s['project'],s['segment'],translation or s['translation'],audio or s['audio'],s['profile'],
         audits,expected,key or str(uuid4()))).fetchone()


def timeline(c,s,selected,expected,start=1000,key=None,clips=None):
    return c.execute('SELECT id,input_hash,project_version FROM platform.deep_dub_commit_timeline(%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],s['project'],s['segment_set'],s['profile'],Jsonb(clips if clips is not None else [
            {'segment_id':str(s['segment']),'selection_id':str(selected),'start_ms':start,'gain':1}]),expected,key or str(uuid4()))).fetchone()


def test_content_migrations_repeat_and_all_tables_have_rls(versions):
    connect,_,_=versions
    with connect() as c:
        rows=c.execute("SELECT relname,relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='platform' AND relname LIKE 'deep_dub_%%' AND relkind='r'").fetchall()
        assert len(rows)==23 and all(enabled for _,enabled in rows)
        assert not c.execute("SELECT has_function_privilege('anon','platform.deep_dub_select_version(uuid,uuid,uuid,uuid,uuid,uuid,uuid[],bigint,text)','EXECUTE')").fetchone()[0]


def test_quality_profile_cannot_remove_common_gates(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            with c.transaction():
                c.execute("SELECT id FROM platform.deep_dub_create_quality_profile(%s,'low',1,%s,'试图去掉门禁')",
                          (s['workspace_id'],Jsonb({**RULES,'translation_required':['meaning']})))


def test_source_window_and_cross_source_cue_link_are_rejected(seed):
    s=seed;common={k:s[k] for k in ['workspace_id','project_id','created_by']}
    with s['connect']() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():
                put(c,'cues',**common,source_id=s['source'],speaker_id=s['speaker'],cue_no=999,source_text='越界',start_ms=9999,end_ms=10001)
        other=put(c,'sources',**common,revision=2,source_url='fixture://other',artifact_id=s['evidence'],duration_ms=10000,identity_evidence={'fixture_only':True})
        cue=put(c,'cues',**common,source_id=other,speaker_id=s['speaker'],cue_no=0,source_text='其他来源',start_ms=1000,end_ms=2000)
        unsealed=put(c,'segment_sets',**common,source_id=s['source'],revision=2,strategy_revision='fixture-2',input_hash='a'*64)
        segment=put(c,'segments',**common,source_id=s['source'],segment_set_id=unsealed,speaker_id=s['speaker'],segment_no=0,source_text='同源分段',start_ms=1000,end_ms=2000)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction():
                put(c,'segment_cues',workspace_id=s['workspace_id'],project_id=s['project'],source_id=s['source'],segment_id=segment,cue_id=cue,position=1)


def test_new_translation_cannot_overwrite_old_revision(seed):
    s=seed
    with s['connect']() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): c.execute("UPDATE platform.deep_dub_translations SET chinese_text='修改旧版本' WHERE id=%s",(s['translation'],))


def test_unmeasured_or_technical_self_review_cannot_pass_translation(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): audit(c,s,'translation','meaning',method='technical')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): audit(c,s,'translation','meaning',evidence=False)
        ids=all_audits(c,s)
        ids[0]=audit(c,s,'translation','meaning',status='not_measured',evidence=False)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): select(c,s,ids)


def test_selection_requires_every_current_rule_and_audited_version(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by']);ids=all_audits(c,s)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): select(c,s,ids[:-1])
        key=str(uuid4());first=select(c,s,ids,key=key);assert select(c,s,list(reversed(ids)),key=key)==first
        assert c.execute('SELECT count(*) FROM platform.deep_dub_selection_audits WHERE selection_id=%s',(first[0],)).fetchone()[0]==12
        with conflict():
            with c.transaction(): select(c,s,ids,expected=1)


def test_newer_failed_audit_blocks_old_passes(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by']);ids=all_audits(c,s);audit(c,s,'translation','facts',status='failed')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): select(c,s,ids)


def test_audit_idempotence_conflicts_and_viewer_cannot_audit(seed):
    s=seed;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);first=audit(c,s,'translation','meaning',key=key)
        assert audit(c,s,'translation','meaning',key=key)==first
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction(): audit(c,s,'translation','meaning',status='failed',key=key)
        c.execute('RESET ROLE');actor(c,s['users'][1])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction(): audit(c,s,'translation','meaning')


def test_parallel_selection_compare_and_swap_only_allows_one_writer(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by']);ids=all_audits(c,s)
    def submit(_):
        try:
            with s['connect']() as c:
                actor(c,s['created_by']);return ('selected',select(c,s,ids)[0])
        except psycopg.DatabaseError as error:
            if error.sqlstate!='PT409': raise
            return ('conflict',None)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
    assert sorted(x[0] for x in results)==['conflict','selected']


def test_timeline_movement_creates_new_snapshot_without_changing_emotion_source(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by']);chosen=select(c,s,all_audits(c,s));key=str(uuid4())
        first=timeline(c,s,chosen[0],2,key=key)
        assert timeline(c,s,chosen[0],2,key=key)==first
        assert timeline(c,s,chosen[0],2,key=key,clips=[{'segment_id':str(s['segment']),
            'selection_id':str(chosen[0]),'start_ms':1000,'gain':1.0}])==first
        second=timeline(c,s,chosen[0],3,start=600)
        assert first[0]!=second[0] and first[1]!=second[1]
        assert c.execute('SELECT start_ms,end_ms FROM platform.deep_dub_emotion_references WHERE id=%s',(s['emotion'],)).fetchone()==(1000,3500)
        assert c.execute('SELECT start_ms FROM platform.deep_dub_timeline_clips WHERE timeline_id=%s',(first[0],)).fetchone()[0]==1000
        with conflict():
            with c.transaction(): timeline(c,s,chosen[0],3,start=700)


def test_timeline_rejects_duplicate_clips_bounds_and_new_failed_review(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by']);chosen=select(c,s,all_audits(c,s))
        for start in [499,9800]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with c.transaction(): timeline(c,s,chosen[0],2,start=start)
        duplicate={'segment_id':str(s['segment']),'selection_id':str(chosen[0]),'start_ms':1000,'gain':1}
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): timeline(c,s,chosen[0],2,clips=[duplicate,duplicate])
        audit(c,s,'audio','content',status='failed')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): timeline(c,s,chosen[0],2)


def test_old_audits_cannot_approve_identical_text_in_a_new_candidate(seed):
    s=seed;common={k:s[k] for k in ['workspace_id','project_id','created_by']}
    with s['connect']() as c:
        actor(c,s['created_by']);ids=all_audits(c,s);c.execute('RESET ROLE')
        translation=put(c,'translations',**common,segment_id=s['segment'],revision=2,chinese_text='还剩十二个样本。',
                        model_id='另一次合成测试生成',prompt_revision='fixture-2',input_hash='b'*64)
        artifact=c.execute('SELECT artifact_id FROM platform.deep_dub_audio_attempts WHERE id=%s',(s['audio'],)).fetchone()[0]
        audio=put(c,'audio_attempts',**common,segment_id=s['segment'],speaker_id=s['speaker'],translation_id=translation,
                  voice_reference_id=s['voice'],emotion_reference_id=s['emotion'],stage_attempt_id=s['attempt'],
                  kind='generation',parameters={'fixture_only':True},artifact_id=artifact,duration_ms=1000,input_hash='b'*64)
        actor(c,s['created_by'])
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction(): select(c,s,ids,translation=translation)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): select(c,s,ids,translation=translation,audio=audio)


def test_sealed_memberships_cannot_be_appended_or_reopened(seed):
    s=seed;common={k:s[k] for k in ['workspace_id','project_id','created_by']}
    with s['connect']() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():
                put(c,'segments',**common,source_id=s['source'],segment_set_id=s['segment_set'],speaker_id=s['speaker'],
                    segment_no=1,source_text='不能追加',start_ms=4000,end_ms=5000)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): c.execute('UPDATE platform.deep_dub_segment_sets SET sealed=false WHERE id=%s',(s['segment_set'],))
        actor(c,s['created_by']);ids=all_audits(c,s);chosen=select(c,s,ids);snapshot=timeline(c,s,chosen[0],2);c.execute('RESET ROLE')
        for query,args in [
            ('INSERT INTO platform.deep_dub_selection_audits(workspace_id,project_id,selection_id,audit_id) VALUES(%s,%s,%s,%s)',(s['workspace_id'],s['project'],chosen[0],ids[0])),
            ('INSERT INTO platform.deep_dub_timeline_clips(workspace_id,project_id,timeline_id,segment_set_id,segment_id,selection_id,start_ms) VALUES(%s,%s,%s,%s,%s,%s,1000)',(s['workspace_id'],s['project'],snapshot[0],s['segment_set'],s['segment'],chosen[0])),
            ('DELETE FROM platform.deep_dub_timeline_revisions WHERE id=%s',(snapshot[0],)),
        ]:
            with pytest.raises(psycopg.errors.CheckViolation):
                with c.transaction(): c.execute(query,args)


def test_cross_project_and_wrong_speaker_references_cannot_be_spliced(seed):
    s=seed;common={k:s[k] for k in ['workspace_id','project_id','created_by']}
    with s['connect']() as c:
        actor(c,s['created_by']);other_project=create(c,s['workspace_id'],str(uuid4()));c.execute('RESET ROLE')
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction():
                put(c,'translations',workspace_id=s['workspace_id'],project_id=other_project,created_by=s['created_by'],
                    segment_id=s['segment'],revision=2,chinese_text='跨项目拼接',model_id='fixture',prompt_revision='fixture',input_hash='a'*64)
        speaker=put(c,'speakers',**common,speaker_key='other-speaker',display_name='另一角色')
        voice=put(c,'voice_references',**common,speaker_id=speaker,source_id=s['source'],revision=1,start_ms=1000,end_ms=2000,
                  original_artifact_id=s['evidence'],processed_artifact_id=s['evidence'],input_hash='b'*64)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction():
                put(c,'audio_attempts',**common,segment_id=s['segment'],speaker_id=s['speaker'],translation_id=s['translation'],
                    voice_reference_id=voice,emotion_reference_id=s['emotion'],stage_attempt_id=s['attempt'],kind='generation',
                    parameters={'fixture_only':True},artifact_id=s['evidence'],duration_ms=1000,input_hash='a'*64)


def test_high_profile_cannot_silently_omit_background_or_switch_voice(seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by'])
        for changed in [{'background_mode':'omit-by-quality-choice'},{'voice_preference':'stable-chinese-voice'}]:
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                with c.transaction():
                    c.execute("SELECT id FROM platform.deep_dub_create_quality_profile(%s,'high',2,%s,'不能降档')",
                              (s['workspace_id'],Jsonb({**RULES,**changed})))


def test_bad_legacy_profile_without_arrays_cannot_skip_selection_gates(seed):
    s=seed
    with s['connect']() as c:
        bad=put(c,'quality_profiles',workspace_id=s['workspace_id'],created_by=s['created_by'],profile_key='low',revision=99,
                rules={},rules_hash='a'*64,provenance='故意错误的管理员夹具')
        altered={**s,'profile':bad};actor(c,s['created_by'])
        evidence=[audit(c,altered,'translation','meaning')]
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): select(c,altered,evidence)


def test_render_cannot_mix_source_or_quality_revision(seed):
    s=seed;common={k:s[k] for k in ['workspace_id','project_id','created_by']}
    with s['connect']() as c:
        actor(c,s['created_by']);chosen=select(c,s,all_audits(c,s));snapshot=timeline(c,s,chosen[0],2)
        second_profile=c.execute("SELECT id FROM platform.deep_dub_create_quality_profile(%s,'high',2,%s,'严格修订')",
                                 (s['workspace_id'],Jsonb({**RULES,'max_advance_ms':499}))).fetchone()[0]
        c.execute('RESET ROLE')
        background=put(c,'backgrounds',**common,source_id=s['source'],revision=1,original_artifact_id=s['evidence'],
            processed_artifact_id=s['evidence'],model_id='fixture',parameters={'fixture_only':True},input_hash='a'*64)
        values={**common,'run_id':s['run'],'source_id':s['source'],'timeline_id':snapshot[0],'quality_profile_id':s['profile'],
            'background_id':background,'revision':1,'strategy_key':'fixture','input_hash':'a'*64,
            **{key:s['evidence'] for key in ['dialogue_artifact_id','mix_artifact_id','subtitle_artifact_id','video_artifact_id']}}
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with c.transaction(): put(c,'renders',**{**values,'quality_profile_id':second_profile})
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): put(c,'renders',**{**values,'background_id':None})
        put(c,'renders',**values)
