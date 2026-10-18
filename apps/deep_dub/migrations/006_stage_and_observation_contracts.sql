CREATE OR REPLACE FUNCTION platform.deep_dub_mark_stage(p_workspace uuid,p_attempt uuid,p_phase text)
RETURNS platform.deep_dub_stage_attempts LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; stage platform.deep_dub_stages;
BEGIN
 attempt:=platform.deep_dub_lock_attempt(p_workspace,p_attempt);
 IF attempt.status NOT IN ('queued','running') OR p_phase IS NULL OR p_phase NOT IN ('started','loaded') THEN
  RAISE EXCEPTION '阶段状态或阶段事件无效' USING ERRCODE='PT409'; END IF;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id;
 IF stage.required_device_kind IS NOT NULL AND NOT EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace
 AND stage_attempt_id=p_attempt AND status='active' AND expires_at>clock_timestamp()) THEN
  RAISE EXCEPTION '阶段尚无有效设备租约' USING ERRCODE='PT409'; END IF;
 IF p_phase='loaded' AND attempt.started_at IS NULL THEN RAISE EXCEPTION '尚未记录实际启动' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_stage_attempts SET status='running',started_at=coalesce(started_at,clock_timestamp()),
 loaded_at=CASE WHEN p_phase='loaded' THEN coalesce(loaded_at,clock_timestamp()) ELSE loaded_at END WHERE id=p_attempt RETURNING * INTO attempt;
 UPDATE platform.deep_dub_stages SET status='running' WHERE id=attempt.stage_id;
 RETURN attempt;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_finish_stage(
 p_workspace uuid,p_attempt uuid,p_status text,p_outputs uuid[],p_elapsed double precision,p_error jsonb,
 p_lease uuid,p_fence bigint,p_token text,p_operation_stopped boolean
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; stage platform.deep_dub_stages; lease platform.deep_dub_device_leases;
 fingerprint text; canonical jsonb; stamp timestamptz;
BEGIN
 attempt:=platform.deep_dub_lock_attempt(p_workspace,p_attempt);
 IF p_status IS NULL OR p_status NOT IN ('succeeded','failed','waiting_review') OR p_outputs IS NULL OR cardinality(p_outputs)>10000
 OR cardinality(p_outputs)<>(SELECT count(DISTINCT x) FROM unnest(p_outputs) x)
 OR p_elapsed IS NULL OR NOT(p_elapsed>=0 AND p_elapsed<'Infinity'::float8)
 OR (p_error IS NOT NULL AND jsonb_typeof(p_error)<>'object') OR (p_status='failed' AND p_error IS NULL)
 OR (p_status='succeeded' AND p_error IS NOT NULL) THEN
  RAISE EXCEPTION '阶段回执不合法' USING ERRCODE='22023'; END IF;
 SELECT coalesce(jsonb_agg(x ORDER BY x),'[]'::jsonb) INTO canonical FROM unnest(p_outputs) x;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_status,canonical,p_elapsed,p_error,p_lease,p_fence,p_operation_stopped));
 IF attempt.commit_hash IS NOT NULL THEN
  IF attempt.commit_hash<>fingerprint THEN RAISE EXCEPTION '阶段回执不能覆盖' USING ERRCODE='23505'; END IF;
  RETURN jsonb_build_object('committed',true,'attempt',to_jsonb(attempt));
 END IF;
 IF attempt.status NOT IN ('queued','running') THEN RAISE EXCEPTION '尝试已结束' USING ERRCODE='PT409'; END IF;
 IF p_status<>'failed' AND attempt.started_at IS NULL THEN RAISE EXCEPTION '尚未记录实际启动' USING ERRCODE='PT409'; END IF;
 IF (SELECT count(*) FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=attempt.project_id
 AND stage_attempt_id=p_attempt AND id=ANY(p_outputs))<>cardinality(p_outputs) THEN
  RAISE EXCEPTION '产物不属于本次尝试' USING ERRCODE='23503'; END IF;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id;
 IF p_lease IS NOT NULL THEN
  SELECT * INTO lease FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND id=p_lease;
  IF NOT FOUND OR lease.stage_attempt_id<>p_attempt THEN RAISE EXCEPTION '租约不属于阶段' USING ERRCODE='23503'; END IF;
  PERFORM 1 FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=lease.device_id FOR UPDATE;
  SELECT * INTO lease FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND id=p_lease FOR UPDATE;
  IF lease.created_by<>auth.uid() OR lease.fence IS DISTINCT FROM p_fence OR lease.token_hash IS DISTINCT FROM encode(sha256(convert_to(p_token,'UTF8')),'hex') THEN
   RAISE EXCEPTION '旧 fence 或租约凭据不正确' USING ERRCODE='42501'; END IF;
 ELSIF EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt)
 OR (p_status<>'failed' AND stage.required_device_kind IS NOT NULL) THEN
  RAISE EXCEPTION '缺少本次运算租约' USING ERRCODE='PT409';
 END IF;
 stamp:=clock_timestamp();
 IF p_lease IS NOT NULL THEN
  IF lease.status<>'active' OR lease.expires_at<=stamp THEN
   IF lease.status='active' THEN UPDATE platform.deep_dub_device_leases SET status='expired' WHERE id=p_lease; END IF;
   UPDATE platform.deep_dub_devices SET state='quarantined',row_version=row_version+1 WHERE id=lease.device_id;
   IF p_status<>'failed' THEN RETURN jsonb_build_object('committed',false,'reason','LEASE_EXPIRED'); END IF;
  END IF;
  IF p_operation_stopped IS DISTINCT FROM true THEN
   IF p_status<>'failed' THEN RAISE EXCEPTION '尚未确认实际运算停止' USING ERRCODE='PT409'; END IF;
   UPDATE platform.deep_dub_device_leases SET status='expired' WHERE id=p_lease AND status='active';
   UPDATE platform.deep_dub_devices SET state='quarantined',row_version=row_version+1 WHERE id=lease.device_id;
  ELSE
   UPDATE platform.deep_dub_device_leases SET status='released',operation_stopped=true,released_at=stamp,
    stop_evidence=jsonb_build_object('confirmed_by',auth.uid(),'method','stage-receipt') WHERE id=p_lease;
  END IF;
 END IF;
 UPDATE platform.deep_dub_stage_attempts SET status=p_status,finished_at=stamp,monotonic_seconds=p_elapsed,
 outputs=canonical,error=p_error,commit_hash=fingerprint WHERE id=p_attempt RETURNING * INTO attempt;
 UPDATE platform.deep_dub_stages SET status=p_status WHERE id=stage.id;
 INSERT INTO platform.deep_dub_observation_outbox(workspace_id,project_id,stage_attempt_id,event_kind)
 VALUES(p_workspace,attempt.project_id,p_attempt,'stage') ON CONFLICT DO NOTHING;
 RETURN jsonb_build_object('committed',true,'attempt',to_jsonb(attempt));
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_cancel_run(p_workspace uuid,p_run uuid)
RETURNS platform.deep_dub_runs LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE run platform.deep_dub_runs;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.cancel') THEN RAISE EXCEPTION '无权取消运行' USING ERRCODE='42501'; END IF;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run;
 IF NOT FOUND THEN RAISE EXCEPTION '运行不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=run.project_id FOR KEY SHARE;
 PERFORM 1 FROM platform.tasks WHERE workspace_id=p_workspace AND id=run.task_id FOR UPDATE;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run FOR UPDATE;
 IF run.status IN ('succeeded','failed','cancelled') THEN RETURN run; END IF;
 PERFORM platform.cancel_task(run.task_id);
 WITH ended AS (UPDATE platform.deep_dub_stage_attempts a SET status='cancelled',finished_at=clock_timestamp()
 WHERE a.workspace_id=p_workspace AND a.status IN ('queued','running') AND EXISTS(
 SELECT 1 FROM platform.deep_dub_stages s WHERE s.workspace_id=p_workspace AND s.id=a.stage_id AND s.run_id=p_run) RETURNING a.id,a.project_id)
 INSERT INTO platform.deep_dub_observation_outbox(workspace_id,project_id,stage_attempt_id,event_kind)
 SELECT p_workspace,project_id,id,'stage' FROM ended ON CONFLICT DO NOTHING;
 UPDATE platform.deep_dub_stages SET status='cancelled' WHERE workspace_id=p_workspace AND run_id=p_run AND status IN ('queued','running','waiting_review');
 UPDATE platform.deep_dub_runs SET status='cancelled' WHERE id=p_run RETURNING * INTO run;
 RETURN run;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_record_score(
 p_workspace uuid,p_attempt uuid,p_metric text,p_rule_revision text,p_measurement_status text,p_value numeric,
 p_evidence uuid,p_details jsonb,p_request_key text
) RETURNS platform.deep_dub_scores LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; stage platform.deep_dub_stages; result platform.deep_dub_scores; fingerprint text; subject text;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.audit') THEN RAISE EXCEPTION '无权登记评分' USING ERRCODE='42501'; END IF;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=p_attempt;
 IF NOT FOUND THEN RAISE EXCEPTION '阶段尝试不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=attempt.project_id FOR KEY SHARE;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=p_attempt FOR SHARE;
 IF attempt.status NOT IN ('succeeded','failed','cancelled','waiting_review') THEN RAISE EXCEPTION '阶段尚未产生冻结回执' USING ERRCODE='PT409'; END IF;
 IF p_metric IS NULL OR length(btrim(p_metric)) NOT BETWEEN 1 AND 100 OR p_rule_revision IS NULL OR length(btrim(p_rule_revision)) NOT BETWEEN 1 AND 200
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION '评分标识不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id;
 subject:=platform.deep_dub_digest(jsonb_build_array(stage.input_hash,attempt.id,attempt.outputs,attempt.commit_hash));
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_metric,p_rule_revision,p_measurement_status,trim_scale(p_value),p_evidence,p_details,subject));
 INSERT INTO platform.deep_dub_scores(workspace_id,project_id,stage_attempt_id,metric,rule_revision,subject_hash,measurement_status,value,evidence_artifact_id,details,request_key,request_hash,created_by)
 VALUES(p_workspace,attempt.project_id,p_attempt,p_metric,p_rule_revision,subject,p_measurement_status,p_value,p_evidence,p_details,p_request_key,fingerprint,auth.uid())
 ON CONFLICT(workspace_id,project_id,request_key) DO NOTHING;
 SELECT * INTO result FROM platform.deep_dub_scores WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '评分幂等键内容冲突' USING ERRCODE='23505'; END IF;
 INSERT INTO platform.deep_dub_observation_outbox(workspace_id,project_id,stage_attempt_id,score_id,event_kind)
 VALUES(p_workspace,attempt.project_id,p_attempt,result.id,'score') ON CONFLICT DO NOTHING;
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_claim_observation(p_workspace uuid,p_token text,p_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE item platform.deep_dub_observation_outbox; attempt platform.deep_dub_stage_attempts; stage platform.deep_dub_stages;
 run platform.deep_dub_runs; task platform.tasks; score platform.deep_dub_scores;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN RAISE EXCEPTION '无权投递观测' USING ERRCODE='42501'; END IF;
 IF p_token IS NULL OR octet_length(p_token) NOT BETWEEN 32 AND 512 OR p_seconds IS NULL OR p_seconds NOT BETWEEN 5 AND 300 THEN
  RAISE EXCEPTION '投递租约参数不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO item FROM platform.deep_dub_observation_outbox WHERE workspace_id=p_workspace AND
 ((status='pending' AND next_attempt_at<=clock_timestamp()) OR (status='sending' AND claim_expires_at<=clock_timestamp()))
 ORDER BY CASE WHEN event_kind='stage' THEN 0 ELSE 1 END,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN jsonb_build_object('claimed',false); END IF;
 UPDATE platform.deep_dub_observation_outbox SET status='sending',delivery_attempts=delivery_attempts+1,claimed_by=auth.uid(),
 claim_token_hash=encode(sha256(convert_to(p_token,'UTF8')),'hex'),claim_expires_at=clock_timestamp()+make_interval(secs=>p_seconds)
 WHERE id=item.id RETURNING * INTO item;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=item.stage_attempt_id;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=stage.run_id;
 SELECT * INTO task FROM platform.tasks WHERE workspace_id=p_workspace AND id=run.task_id;
 IF item.score_id IS NOT NULL THEN SELECT * INTO score FROM platform.deep_dub_scores WHERE workspace_id=p_workspace AND id=item.score_id; END IF;
 RETURN jsonb_build_object('claimed',true,'event',to_jsonb(item)-'claim_token_hash',
 'attempt',to_jsonb(attempt),'stage',to_jsonb(stage),'run',to_jsonb(run),'user_id',task.created_by,'session_id',run.id,'score',CASE WHEN item.score_id IS NULL THEN NULL ELSE to_jsonb(score) END);
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_ack_observation(p_workspace uuid,p_event uuid,p_token text,p_success boolean,p_error_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE item platform.deep_dub_observation_outbox;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN RAISE EXCEPTION '无权确认观测' USING ERRCODE='42501'; END IF;
 SELECT * INTO item FROM platform.deep_dub_observation_outbox WHERE workspace_id=p_workspace AND id=p_event FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '观测事件不存在' USING ERRCODE='PT404'; END IF;
 IF item.claimed_by IS DISTINCT FROM auth.uid() OR item.claim_token_hash IS DISTINCT FROM encode(sha256(convert_to(p_token,'UTF8')),'hex') THEN
  RAISE EXCEPTION '投递凭据不正确' USING ERRCODE='42501'; END IF;
 IF item.status='sent' THEN RETURN jsonb_build_object('acknowledged',true,'sent',true); END IF;
 IF item.status='pending' AND NOT p_success THEN RETURN jsonb_build_object('acknowledged',true,'sent',false); END IF;
 IF item.status<>'sending' OR item.claim_expires_at<=clock_timestamp() THEN RAISE EXCEPTION '投递租约已过期' USING ERRCODE='PT409'; END IF;
 IF p_success IS NULL OR (NOT p_success AND (p_error_code IS NULL OR p_error_code !~ '^[A-Z0-9_]{1,100}$')) THEN
  RAISE EXCEPTION '回执或错误标识无效，禁止写入原始凭据日志' USING ERRCODE='22023'; END IF;
 IF p_success THEN UPDATE platform.deep_dub_observation_outbox SET status='sent',sent_at=clock_timestamp(),last_error=NULL WHERE id=p_event;
 ELSE UPDATE platform.deep_dub_observation_outbox SET status='pending',last_error=p_error_code,
 next_attempt_at=clock_timestamp()+make_interval(secs=>least(300,power(2,least(delivery_attempts,8)))::integer) WHERE id=p_event; END IF;
 RETURN jsonb_build_object('acknowledged',true,'sent',p_success);
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_check_attempt_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
BEGIN
 IF (to_jsonb(OLD)-ARRAY['status','loaded_at','started_at','finished_at','monotonic_seconds','error','outputs','commit_hash'])<>
    (to_jsonb(NEW)-ARRAY['status','loaded_at','started_at','finished_at','monotonic_seconds','error','outputs','commit_hash'])
 OR (OLD.started_at IS NOT NULL AND OLD.started_at IS DISTINCT FROM NEW.started_at)
 OR (OLD.loaded_at IS NOT NULL AND OLD.loaded_at IS DISTINCT FROM NEW.loaded_at)
 OR (OLD.finished_at IS NOT NULL AND to_jsonb(OLD)<>to_jsonb(NEW)) THEN
  RAISE EXCEPTION '尝试身份、首次时间或已冻结回执不能覆盖' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS attempt_identity ON platform.deep_dub_stage_attempts;
CREATE TRIGGER attempt_identity BEFORE UPDATE ON platform.deep_dub_stage_attempts FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_check_attempt_identity();
DO $$ DECLARE proc record; BEGIN
 FOR proc IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='platform' AND p.proname=ANY(ARRAY['deep_dub_mark_stage','deep_dub_finish_stage','deep_dub_cancel_run','deep_dub_record_score','deep_dub_claim_observation','deep_dub_ack_observation']) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon',proc.signature);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated',proc.signature);
 END LOOP;
END $$;
NOTIFY pgrst,'reload schema';
