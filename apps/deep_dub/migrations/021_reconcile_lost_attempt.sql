-- 管理员已核对停止的失联尝试：仅失败收尾，不改原运行、不伪造单调耗时。
CREATE OR REPLACE FUNCTION platform.deep_dub_close_lost_attempt(
 p_workspace uuid,p_attempt uuid,p_lease_ids uuid[]
) RETURNS platform.deep_dub_stage_attempts LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; stage platform.deep_dub_stages;
 leases uuid[]; proof jsonb; fingerprint text; run_id uuid; task_id uuid;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.device.manage') OR NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN
  RAISE EXCEPTION '无权收尾已核对的失联尝试' USING ERRCODE='42501'; END IF;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=p_attempt;
 IF NOT FOUND THEN RAISE EXCEPTION '执行尝试不存在' USING ERRCODE='PT404'; END IF;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id;
 SELECT r.id,r.task_id INTO run_id,task_id FROM platform.deep_dub_runs r WHERE r.workspace_id=p_workspace AND r.id=stage.run_id;
 -- 与阶段领取/正常回执使用相同锁顺序，避免恢复与迟到请求相互持锁。
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=attempt.project_id FOR KEY SHARE;
 PERFORM 1 FROM platform.tasks WHERE workspace_id=p_workspace AND id=task_id FOR UPDATE;
 PERFORM 1 FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=run_id FOR UPDATE;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=stage.id FOR UPDATE;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=p_attempt FOR UPDATE;
 PERFORM 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt FOR UPDATE;
 SELECT array_agg(id ORDER BY id),jsonb_agg(jsonb_build_object('lease_id',id,'stop_evidence',stop_evidence) ORDER BY id)
 INTO leases,proof FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt;
 IF p_lease_ids IS NULL OR cardinality(p_lease_ids) NOT BETWEEN 1 AND 100 OR array_position(p_lease_ids,NULL) IS NOT NULL
 OR cardinality(p_lease_ids)<>(SELECT count(DISTINCT x) FROM unnest(p_lease_ids) x)
 OR leases IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM unnest(p_lease_ids) x) THEN
  RAISE EXCEPTION '必须核对本尝试的完整租约集合' USING ERRCODE='PT409'; END IF;
 IF EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt
 AND (status<>'released' OR operation_stopped IS DISTINCT FROM true
 OR stop_evidence->>'method' IS DISTINCT FROM 'administrator-reconciled'
 OR stop_evidence->>'confirmed_by' IS DISTINCT FROM auth.uid()::text)) THEN
  RAISE EXCEPTION '必须由当前管理员先核对实际停止并释放全部租约' USING ERRCODE='PT409'; END IF;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array('lost-attempt-v1',p_attempt,leases,proof));
 IF attempt.commit_hash IS NOT NULL THEN
  IF attempt.commit_hash<>fingerprint OR attempt.status<>'failed' OR attempt.error->>'code' IS DISTINCT FROM 'WORKER_LOST' THEN
   RAISE EXCEPTION '已有阶段回执不可覆盖' USING ERRCODE='23505'; END IF;
  RETURN attempt;
 END IF;
 IF attempt.status NOT IN ('queued','running') OR stage.current_attempt_id IS DISTINCT FROM p_attempt THEN
  RAISE EXCEPTION '只能收尾当前失联且尚无回执的尝试' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_stage_attempts SET status='failed',finished_at=clock_timestamp(),monotonic_seconds=NULL,
 outputs='[]'::jsonb,error=jsonb_build_object('code','WORKER_LOST','method','administrator-reconciled',
 'monotonic_measurement','unavailable_after_owner_loss','lease_stop_evidence',proof),commit_hash=fingerprint
 WHERE id=p_attempt RETURNING * INTO attempt;
 UPDATE platform.deep_dub_stages SET status='failed' WHERE id=stage.id;
 INSERT INTO platform.deep_dub_observation_outbox(workspace_id,project_id,stage_attempt_id,event_kind)
 VALUES(p_workspace,attempt.project_id,p_attempt,'stage') ON CONFLICT DO NOTHING;
 RETURN attempt;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_close_lost_attempt(uuid,uuid,uuid[]) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_close_lost_attempt(uuid,uuid,uuid[]) TO authenticated;
NOTIFY pgrst,'reload schema';
