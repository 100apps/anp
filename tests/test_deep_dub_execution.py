"""独立数据库中的执行与租约合成测试；不接管任何真实设备。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import secrets

import psycopg
from psycopg.types.json import Jsonb
import pytest

from anp import ROOT
from tests.test_deep_dub_foundation import database, actor, pytestmark
from tests.test_deep_dub_versions import versions, seed, conflict, put
from tests.test_deep_dub_versions import all_audits,select,timeline,audit

PROBE={'all_owned_operations_stopped':True,'has_external_processes':False,'host_boot_id':'fixture-boot'}


@pytest.fixture(scope='module')
def executions(versions):
    connect,users,workspaces=versions
    with connect() as c:
        for _ in range(2):
            for name in ['004_execution_relations.sql','005_run_and_device_contracts.sql','006_stage_and_observation_contracts.sql','007_publication_contracts.sql','018_source_content_audit_gate.sql']:
                c.execute((ROOT/'apps/deep_dub/migrations'/name).read_text(),prepare=False)
        c.execute("UPDATE platform.members SET role='admin' WHERE workspace_id=%s AND user_id=%s",(workspaces[0],users[0]))
    return versions


@pytest.fixture
def work(executions,seed):
    s=seed
    with s['connect']() as c:
        actor(c,s['created_by']);c.execute('SELECT id FROM platform.deep_dub_start_run(%s,%s)',(s['workspace_id'],s['run']))
        attempt=begin(c,s,'cpu')
    return {**s,'attempt':attempt['id'],'stage':attempt['stage_id']}


def begin(c,s,kind='cpu',node=None,key=None):
    return c.execute('SELECT platform.deep_dub_begin_stage(%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],s['run'],node or 'fixture-'+str(uuid4()),'partition','a'*64,kind,key or str(uuid4()))).fetchone()[0]['attempt']


def device(c,s,kind='cpu',capacity=1,ready=True):
    row=c.execute('SELECT id,row_version FROM platform.deep_dub_register_device(%s,%s,%s,%s,%s)',
        (s['workspace_id'],'fixture-'+str(uuid4()),'fixture-host',kind,capacity)).fetchone()
    if ready:
        c.execute('SELECT id FROM platform.deep_dub_confirm_device_probe(%s,%s,%s,%s)',(s['workspace_id'],row[0],row[1],Jsonb(PROBE)))
    return row[0]


def acquire(c,s,target,attempt=None,token=None,key=None,slots=1,seconds=30):
    token=token or secrets.token_urlsafe(32)
    result=c.execute('SELECT platform.deep_dub_acquire_lease(%s,%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],attempt or s['attempt'],target,'fixture-owner',token,slots,seconds,key or str(uuid4()))).fetchone()[0]
    return result,token


def finish(c,s,status='succeeded',attempt=None,lease=None,token=None,fence=None,stopped=True,outputs=None):
    return c.execute('SELECT platform.deep_dub_finish_stage(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],attempt or s['attempt'],status,outputs or [],0.5,Jsonb({'code':'FIXTURE_FAILURE'}) if status=='failed' else None,
         lease,fence,token,stopped)).fetchone()[0]


def expire(c,lease):
    c.execute("UPDATE platform.deep_dub_device_leases SET acquired_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 minute' WHERE id=%s",(lease,))


def test_execution_relations_repeat_and_keep_all_28_tables_under_rls(executions):
    connect,_,_=executions
    with connect() as c:
        rows=c.execute("SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='platform' AND relname LIKE 'deep_dub_%%' AND relkind='r'").fetchall()
        assert len(rows)==28 and all(enabled for (enabled,) in rows)


def test_queue_run_idempotence_keeps_original_clock_and_frozen_profile(work):
    s=work;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);args=(s['workspace_id'],s['project'],s['profile'],'local','acceptance','fixture-flow',1,key)
        first=c.execute('SELECT id,queued_at,task_id FROM platform.deep_dub_queue_run(%s,%s,%s,%s,%s,%s,%s,%s)',args).fetchone()
        second=c.execute('SELECT id,queued_at,task_id FROM platform.deep_dub_queue_run(%s,%s,%s,%s,%s,%s,%s,%s)',args).fetchone();assert first==second
        started=c.execute('SELECT queued_at,started_at FROM platform.deep_dub_start_run(%s,%s)',(s['workspace_id'],first[0])).fetchone();assert started[0]==first[1] and started[1]>=first[1]
        c.execute('RESET ROLE')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():c.execute('UPDATE platform.deep_dub_runs SET queued_at=clock_timestamp() WHERE id=%s',(first[0],))


def test_unverified_device_cannot_be_claimed_or_probed_from_low_utilization(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s,ready=False)
        result,_=acquire(c,s,target);assert result['acquired'] is False and result['reason']=='DEVICE_NOT_READY'
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():c.execute('SELECT id FROM platform.deep_dub_confirm_device_probe(%s,%s,1,%s)',(s['workspace_id'],target,Jsonb({'gpu_utilization':0})))


def test_cpu_capacity_is_atomic_under_parallel_claims(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s,capacity=1);attempts=[s['attempt'],begin(c,s)['id']]
    def submit(index):
        with s['connect']() as c:
            actor(c,s['created_by']);return acquire(c,s,target,attempt=attempts[index])[0]
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,range(2)))
    assert sum(x['acquired'] for x in results)==1
    assert any(x.get('reason')=='DEVICE_BUSY' for x in results)


def test_capacity_two_does_not_invalidate_first_lease_fence(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s,capacity=2)
        one,token=acquire(c,s,target);other=begin(c,s);two,_=acquire(c,s,target,attempt=other['id'])
        assert one['acquired'] and two['acquired'] and two['lease']['fence']>one['lease']['fence']
        answer=c.execute('SELECT platform.deep_dub_heartbeat_lease(%s,%s,%s,%s,30)',(s['workspace_id'],one['lease']['id'],one['lease']['fence'],token)).fetchone()[0]
        assert answer['renewed']


def test_lease_replay_is_idempotent_and_secret_hashes_are_not_returned(work):
    s=work;key=str(uuid4());token=secrets.token_urlsafe(32)
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s)
        one,_=acquire(c,s,target,key=key,token=token);two,_=acquire(c,s,target,key=key,token=token)
        assert one==two and 'token_hash' not in one['lease'] and 'request_hash' not in one['lease']
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('SELECT token_hash FROM platform.deep_dub_device_leases WHERE id=%s',(one['lease']['id'],))
        with conflict():
            with c.transaction():acquire(c,s,target)


def test_expiry_quarantines_and_does_not_reassign_hardware(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s);one,_=acquire(c,s,target);other=begin(c,s)['id'];c.execute('RESET ROLE');expire(c,one['lease']['id'])
        actor(c,s['created_by']);blocked,_=acquire(c,s,target,attempt=other)
        assert blocked=={'acquired':False,'reason':'DEVICE_QUARANTINED'}
    with s['connect']() as c:
        assert c.execute('SELECT state FROM platform.deep_dub_devices WHERE id=%s',(target,)).fetchone()[0]=='quarantined'
        assert c.execute('SELECT status FROM platform.deep_dub_device_leases WHERE id=%s',(one['lease']['id'],)).fetchone()[0]=='expired'


def test_expired_worker_cannot_commit_and_admin_must_verify_stopped_work(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s);one,token=acquire(c,s,target);lease=one['lease'];c.execute('RESET ROLE');expire(c,lease['id'])
        actor(c,s['created_by']);answer=finish(c,s,lease=lease['id'],fence=lease['fence'],token=token)
        assert answer=={'committed':False,'reason':'LEASE_EXPIRED'}
        version=c.execute('SELECT row_version FROM platform.deep_dub_devices WHERE id=%s',(target,)).fetchone()[0]
        with conflict():
            with c.transaction():c.execute('SELECT id FROM platform.deep_dub_confirm_device_probe(%s,%s,%s,%s)',(s['workspace_id'],target,version,Jsonb(PROBE)))
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():c.execute('SELECT id FROM platform.deep_dub_reconcile_device(%s,%s,%s,%s,%s)',(s['workspace_id'],target,version,[lease['id']],Jsonb({**PROBE,'all_owned_operations_stopped':False})))
        c.execute('SELECT id FROM platform.deep_dub_reconcile_device(%s,%s,%s,%s,%s)',(s['workspace_id'],target,version,[lease['id']],Jsonb(PROBE)))
        assert c.execute('SELECT state FROM platform.deep_dub_devices WHERE id=%s',(target,)).fetchone()[0]=='ready'


def test_wrong_token_or_fence_cannot_heartbeat_or_complete(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s);one,token=acquire(c,s,target);lease=one['lease']
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('SELECT platform.deep_dub_heartbeat_lease(%s,%s,%s,%s,30)',(s['workspace_id'],lease['id'],lease['fence'],secrets.token_urlsafe(32)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():finish(c,s,lease=lease['id'],fence=lease['fence']+1,token=token)


def test_completed_stage_receipt_is_idempotent_and_immutable(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s);one,token=acquire(c,s,target);lease=one['lease']
        one=finish(c,s,lease=lease['id'],fence=lease['fence'],token=token);two=finish(c,s,lease=lease['id'],fence=lease['fence'],token=token)
        assert one==two and one['committed']
        assert c.execute('SELECT count(*) FROM platform.deep_dub_observation_outbox WHERE stage_attempt_id=%s',(s['attempt'],)).fetchone()[0]==1
        c.execute('RESET ROLE')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():c.execute('UPDATE platform.deep_dub_stage_attempts SET queued_at=clock_timestamp() WHERE id=%s',(s['attempt'],))


def test_failed_attempt_retry_preserves_old_receipt_and_rejects_old_worker(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);finish(c,s,status='failed')
        node=c.execute('SELECT node FROM platform.deep_dub_stages WHERE id=%s',(s['stage'],)).fetchone()[0]
        current=begin(c,s,node=node);assert current['attempt_no']==2
        with conflict():
            with c.transaction():finish(c,s,status='failed')


def test_cancellation_rejects_late_commit_but_keeps_resource_until_stop_ack(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s);one,token=acquire(c,s,target);lease=one['lease']
        c.execute('SELECT id FROM platform.deep_dub_cancel_run(%s,%s)',(s['workspace_id'],s['run']))
        with conflict():
            with c.transaction():finish(c,s,lease=lease['id'],fence=lease['fence'],token=token)
        assert c.execute('SELECT status FROM platform.deep_dub_device_leases WHERE id=%s',(lease['id'],)).fetchone()[0]=='active'
        c.execute('SELECT platform.deep_dub_release_lease(%s,%s,%s,%s,true)',(s['workspace_id'],lease['id'],lease['fence'],token))


def test_revoked_permission_is_rechecked_before_commit(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);target=device(c,s);one,token=acquire(c,s,target);lease=one['lease'];c.execute('RESET ROLE')
        c.execute("UPDATE platform.members SET role='viewer' WHERE workspace_id=%s AND user_id=%s",(s['workspace_id'],s['created_by']))
        actor(c,s['created_by'])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():finish(c,s,lease=lease['id'],fence=lease['fence'],token=token)
        c.execute('RESET ROLE');c.execute("UPDATE platform.members SET role='admin' WHERE workspace_id=%s AND user_id=%s",(s['workspace_id'],s['created_by']))


def local_receipt(c,s):
    attempt=begin(c,s,kind=None,node='local-contract-'+str(uuid4()))
    c.execute('SELECT id FROM platform.deep_dub_mark_stage(%s,%s,%s)',(s['workspace_id'],attempt['id'],'started'))
    finish(c,s,attempt=attempt['id'])
    return attempt['id']


def score(c,s,attempt,key=None,status='not_measured',value=None,evidence=None):
    return c.execute('SELECT id,value,subject_hash FROM platform.deep_dub_record_score(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (s['workspace_id'],attempt,'voice_naturalness','fixture-v1',status,value,evidence,Jsonb({'fixture_only':True}),key or str(uuid4()))).fetchone()


def claim(c,s,token):
    return c.execute('SELECT platform.deep_dub_claim_observation(%s,%s,30)',(s['workspace_id'],token)).fetchone()[0]


def only_this_outbox(c,s,attempt):
    c.execute('RESET ROLE')
    c.execute("UPDATE platform.deep_dub_observation_outbox SET next_attempt_at='infinity',claim_expires_at='infinity' WHERE workspace_id=%s AND stage_attempt_id<>%s AND status<>'sent'",(s['workspace_id'],attempt))
    actor(c,s['created_by'])


def test_unknown_score_stays_null_and_enqueues_only_one_event(work):
    s=work;key=str(uuid4())
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=local_receipt(c,s)
        first=score(c,s,attempt,key=key);assert first[1] is None and score(c,s,attempt,key=key)==first
        assert c.execute("SELECT count(*) FROM platform.deep_dub_observation_outbox WHERE score_id=%s",(first[0],)).fetchone()[0]==1
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():score(c,s,attempt,value=1)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():score(c,s,attempt,status='measured',value=1)


def test_score_binds_frozen_attempt_and_cannot_be_overwritten(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);one=local_receipt(c,s);two=local_receipt(c,s)
        first=score(c,s,one);second=score(c,s,two);assert first[2]!=second[2]
        c.execute('RESET ROLE')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():c.execute('UPDATE platform.deep_dub_scores SET value=1 WHERE id=%s',(first[0],))


def test_outbox_claim_identity_and_expired_delivery_token_fencing(work):
    s=work;token=secrets.token_urlsafe(32);second_token=secrets.token_urlsafe(32)
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=local_receipt(c,s);only_this_outbox(c,s,attempt)
        first=claim(c,s,token);assert first['claimed'] and first['user_id']==str(s['created_by']) and first['session_id']==str(s['run'])
        assert not claim(c,s,second_token)['claimed'] and 'claim_token_hash' not in first['event']
        c.execute('RESET ROLE');c.execute("UPDATE platform.deep_dub_observation_outbox SET claim_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(first['event']['id'],));actor(c,s['created_by'])
        second=claim(c,s,second_token);assert second['event']['id']==first['event']['id'] and second['event']['delivery_attempts']==2
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():c.execute('SELECT platform.deep_dub_ack_observation(%s,%s,%s,true,NULL)',(s['workspace_id'],first['event']['id'],token))
        answer=c.execute('SELECT platform.deep_dub_ack_observation(%s,%s,%s,true,NULL)',(s['workspace_id'],first['event']['id'],second_token)).fetchone()[0]
        assert answer['sent']


def test_outbox_failure_retains_backoff_and_disallows_raw_secret_logs(work):
    s=work;token=secrets.token_urlsafe(32)
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=local_receipt(c,s);only_this_outbox(c,s,attempt);item=claim(c,s,token)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            with c.transaction():c.execute('SELECT platform.deep_dub_ack_observation(%s,%s,%s,false,%s)',(s['workspace_id'],item['event']['id'],token,'Bearer private-value'))
        answer=c.execute("SELECT platform.deep_dub_ack_observation(%s,%s,%s,false,'HTTP_ERROR')",(s['workspace_id'],item['event']['id'],token)).fetchone()[0]
        assert answer['acknowledged'] and not answer['sent'] and not claim(c,s,secrets.token_urlsafe(32))['claimed']


def qualified_render(c,s):
    """仅在独立库构造门禁夹具；不含真实媒体，不是质量验收。"""
    actor(c,s['created_by']);chosen=select(c,s,all_audits(c,s));snapshot=timeline(c,s,chosen[0],2)
    target=device(c,s);lease,token=acquire(c,s,target);finish(c,s,lease=lease['lease']['id'],fence=lease['lease']['fence'],token=token)
    c.execute('RESET ROLE')
    # 参考数据构造器已有的旧阶段只作关系夹具；为最终门禁补上其冻结回执。
    c.execute("UPDATE platform.deep_dub_stage_attempts a SET status='succeeded',started_at=clock_timestamp(),finished_at=clock_timestamp(),monotonic_seconds=0 WHERE a.stage_id IN (SELECT id FROM platform.deep_dub_stages WHERE run_id=%s AND current_attempt_id IS NULL)",(s['run'],))
    c.execute("UPDATE platform.deep_dub_stages s SET status='succeeded',current_attempt_id=(SELECT id FROM platform.deep_dub_stage_attempts WHERE stage_id=s.id ORDER BY attempt_no DESC LIMIT 1) WHERE run_id=%s AND current_attempt_id IS NULL",(s['run'],))
    common={k:s[k] for k in ['workspace_id','project_id','created_by']}
    background=put(c,'backgrounds',**common,source_id=s['source'],revision=1,original_artifact_id=s['evidence'],
        processed_artifact_id=s['evidence'],model_id='fixture',parameters={'fixture_only':True},input_hash='a'*64)
    render=put(c,'renders',**common,run_id=s['run'],source_id=s['source'],timeline_id=snapshot[0],quality_profile_id=s['profile'],
        background_id=background,revision=1,strategy_key='fixture',input_hash='a'*64,
        **{key:s['evidence'] for key in ['dialogue_artifact_id','mix_artifact_id','subtitle_artifact_id','video_artifact_id']})
    actor(c,s['created_by'])
    for kind,subject,rules in [('source',s['source'],['identity','duration','tracks','transcript_coverage']),
        ('timeline',snapshot[0],['coverage','collision','bounds']),('voice_reference',s['voice'],['speaker_identity','reference_quality']),
        ('background',background,['decode','source_language_leakage','event_preservation']),
        ('render',render,['full_decode','loudness','duration','subtitles','snapshot','content','collision','bounds'])]:
        for rule in rules:
            method='independent_model' if rule in ('speaker_identity','event_preservation','transcript_coverage') else 'technical'
            audit(c,s,kind,rule,subject=subject,method=method)
    return render


def request_publication(c,s,render,key=None):
    return c.execute('SELECT platform.deep_dub_request_publication(%s,%s,%s)',(s['workspace_id'],render,key or str(uuid4()))).fetchone()[0]


def test_publication_requires_verified_run_and_does_not_duplicate_render(work):
    s=work
    with s['connect']() as c:
        render=qualified_render(c,s)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():request_publication(c,s,render)
        verified=c.execute('SELECT platform.deep_dub_verify_run(%s,%s,%s)',(s['workspace_id'],s['run'],render)).fetchone()[0]
        assert verified['verified'] and verified['performance_passed']
        one=request_publication(c,s,render);two=request_publication(c,s,render);assert one['id']==two['id'] and 'claim_token_hash' not in one


def test_publication_cannot_reuse_old_pass_after_new_quality_failure(work):
    s=work
    with s['connect']() as c:
        render=qualified_render(c,s)
        c.execute('SELECT platform.deep_dub_verify_run(%s,%s,%s)',(s['workspace_id'],s['run'],render))
        audit(c,s,'render','content',subject=render,status='failed')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():request_publication(c,s,render)


def test_expired_upload_stays_unknown_and_late_receipt_is_not_a_reupload(work):
    s=work;token=secrets.token_urlsafe(32)
    with s['connect']() as c:
        render=qualified_render(c,s);c.execute('SELECT platform.deep_dub_verify_run(%s,%s,%s)',(s['workspace_id'],s['run'],render));publication=request_publication(c,s,render)
        args=(s['workspace_id'],publication['id'],token,30)
        assert c.execute('SELECT platform.deep_dub_claim_publication(%s,%s,%s,%s)',args).fetchone()[0]['claimed']
        replay=c.execute('SELECT platform.deep_dub_claim_publication(%s,%s,%s,%s)',args).fetchone()[0]
        assert replay=={'claimed':False,'reason':'UPLOAD_IN_PROGRESS'}
        c.execute('RESET ROLE');c.execute("UPDATE platform.deep_dub_publications SET claim_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",(publication['id'],));actor(c,s['created_by'])
        assert c.execute('SELECT platform.deep_dub_claim_publication(%s,%s,%s,%s)',args).fetchone()[0]=={'claimed':False,'reason':'RECEIPT_UNKNOWN'}
        receipt={'bvid':'BV1xx411c7mD','aid':1,'provider_code':0}
        result=c.execute('SELECT platform.deep_dub_record_publication_receipt(%s,%s,%s,%s,%s,%s)',
            (s['workspace_id'],publication['id'],token,'a'*64,Jsonb(receipt),s['evidence'])).fetchone()[0]
        assert result['status']=='submitted'
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():c.execute('SELECT platform.deep_dub_confirm_publication(%s,%s,%s,%s)',
                (s['workspace_id'],publication['id'],'https://www.bilibili.com/video/BV1xx411c7mD/',s['evidence']))


def test_stage_waiting_for_review_can_be_cancelled_without_rewriting_frozen_attempt(work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=begin(c,s,kind=None)
        c.execute("SELECT id FROM platform.deep_dub_mark_stage(%s,%s,'started')",(s['workspace_id'],attempt['id']))
        finish(c,s,status='waiting_review',attempt=attempt['id'])
        c.execute('SELECT id FROM platform.deep_dub_cancel_run(%s,%s)',(s['workspace_id'],s['run']))
        assert c.execute('SELECT status FROM platform.deep_dub_stage_attempts WHERE id=%s',(attempt['id'],)).fetchone()[0]=='waiting_review'
