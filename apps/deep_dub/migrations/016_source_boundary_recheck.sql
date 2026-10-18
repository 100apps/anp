-- 待审返修仅开放来源接缝重识别；不改变运行状态、排队起点或独立审核门禁。
CREATE OR REPLACE FUNCTION platform.deep_dub_source_recheck_permitted(p_workspace uuid,p_run uuid,p_node text,p_partition text,p_kind text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
 SELECT coalesce(p_node='source_boundary_recheck' AND p_kind='gpu'
 AND platform.has_permission(p_workspace,'deep_dub.audit')
 AND EXISTS(SELECT 1 FROM platform.deep_dub_runs r
 WHERE r.workspace_id=p_workspace AND r.id=p_run AND r.status='waiting_review'
 AND r.technical_verified_at IS NULL AND r.source_id::text=p_partition
 AND EXISTS(SELECT 1 FROM platform.deep_dub_stages s JOIN platform.deep_dub_stage_attempts a
 ON (a.workspace_id,a.id)=(s.workspace_id,s.current_attempt_id)
 WHERE s.workspace_id=r.workspace_id AND s.run_id=r.id AND s.node='source_preparation_review'
 AND s.status='waiting_review' AND a.status='waiting_review' AND jsonb_array_length(a.outputs)=1)),false);
$$;
REVOKE ALL ON FUNCTION platform.deep_dub_source_recheck_permitted(uuid,uuid,text,text,text) FROM PUBLIC,anon,authenticated;


CREATE OR REPLACE FUNCTION platform.deep_dub_lock_attempt(p_workspace uuid,p_attempt uuid)
RETURNS platform.deep_dub_stage_attempts LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_stage_attempts; stage platform.deep_dub_stages; run platform.deep_dub_runs; task platform.tasks;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN RAISE EXCEPTION '无权执行阶段' USING ERRCODE='42501'; END IF;
 SELECT * INTO result FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=p_attempt;
 IF NOT FOUND THEN RAISE EXCEPTION '阶段尝试不存在' USING ERRCODE='PT404'; END IF;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=result.stage_id;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=stage.run_id;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=result.project_id FOR KEY SHARE;
 SELECT * INTO task FROM platform.tasks WHERE workspace_id=p_workspace AND id=run.task_id FOR UPDATE;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=run.id FOR UPDATE;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=stage.id FOR UPDATE;
 SELECT * INTO result FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=p_attempt FOR UPDATE;
 IF task.status<>'running' OR task.cancel_requested OR (run.status<>'running' AND NOT platform.deep_dub_source_recheck_permitted(p_workspace,run.id,stage.node,stage.partition_key,stage.required_device_kind)) THEN RAISE EXCEPTION '运行已停止或取消' USING ERRCODE='PT409'; END IF;
 IF stage.current_attempt_id IS DISTINCT FROM p_attempt THEN RAISE EXCEPTION '旧尝试已失效' USING ERRCODE='PT409'; END IF;
 IF result.created_by IS DISTINCT FROM auth.uid() THEN RAISE EXCEPTION '尝试必须保持原执行者身份' USING ERRCODE='42501'; END IF;
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION platform.deep_dub_begin_stage(
 p_workspace uuid,p_run uuid,p_node text,p_partition text,p_input_hash text,p_device_kind text,p_request_key text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE run platform.deep_dub_runs; task platform.tasks; stage platform.deep_dub_stages; attempt platform.deep_dub_stage_attempts;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN RAISE EXCEPTION '无权执行阶段' USING ERRCODE='42501'; END IF;
 IF p_node IS NULL OR length(p_node) NOT BETWEEN 1 AND 100 OR p_partition IS NULL OR length(p_partition)>200
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200
 OR p_input_hash IS NULL OR p_input_hash !~ '^[a-f0-9]{64}$' OR (p_device_kind IS NOT NULL AND p_device_kind NOT IN ('cpu','gpu')) THEN
  RAISE EXCEPTION '阶段输入不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run;
 IF NOT FOUND THEN RAISE EXCEPTION '运行不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=run.project_id FOR KEY SHARE;
 SELECT * INTO task FROM platform.tasks WHERE workspace_id=p_workspace AND id=run.task_id FOR UPDATE;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run FOR UPDATE;
 IF task.status<>'running' OR task.cancel_requested OR (run.status<>'running' AND NOT platform.deep_dub_source_recheck_permitted(p_workspace,p_run,p_node,p_partition,p_device_kind)) THEN RAISE EXCEPTION '运行已停止或取消' USING ERRCODE='PT409'; END IF;
 IF p_node='source_boundary_recheck' AND NOT platform.has_permission(p_workspace,'deep_dub.audit') THEN
  RAISE EXCEPTION '无权执行来源返修' USING ERRCODE='42501'; END IF;
 INSERT INTO platform.deep_dub_stages(workspace_id,project_id,run_id,node,partition_key,input_hash,required_device_kind,created_by)
 VALUES(p_workspace,run.project_id,p_run,p_node,p_partition,p_input_hash,p_device_kind,auth.uid())
 ON CONFLICT(workspace_id,run_id,node,partition_key,input_hash) DO NOTHING;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND run_id=p_run AND node=p_node
 AND partition_key=p_partition AND input_hash=p_input_hash FOR UPDATE;
 IF stage.required_device_kind IS DISTINCT FROM p_device_kind THEN RAISE EXCEPTION '阶段资源声明不能改变' USING ERRCODE='23505'; END IF;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND stage_id=stage.id AND request_key=p_request_key;
 IF FOUND THEN RETURN jsonb_build_object('acquired',false,'reason','IDEMPOTENT_REPLAY','attempt',to_jsonb(attempt)); END IF;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=stage.current_attempt_id FOR UPDATE;
 IF FOUND AND attempt.status IN ('queued','running','succeeded','waiting_review') THEN
  RETURN jsonb_build_object('acquired',false,'reason','ATTEMPT_EXISTS','attempt',to_jsonb(attempt)); END IF;
 INSERT INTO platform.deep_dub_stage_attempts(workspace_id,project_id,stage_id,attempt_no,request_key,created_by)
 SELECT p_workspace,run.project_id,stage.id,coalesce(max(attempt_no),0)+1,p_request_key,auth.uid()
 FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND stage_id=stage.id RETURNING * INTO attempt;
 UPDATE platform.deep_dub_stages SET current_attempt_id=attempt.id,status='queued' WHERE id=stage.id;
 RETURN jsonb_build_object('acquired',true,'attempt',to_jsonb(attempt));
END $$;

CREATE OR REPLACE FUNCTION platform.deep_dub_object_access(p_name text,p_write boolean) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE object_workspace uuid; object_project uuid; object_attempt uuid;
BEGIN
 IF p_name IS NULL OR p_name !~ '^[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f]{64}$' THEN RETURN false; END IF;
 BEGIN
  object_workspace:=split_part(p_name,'/',1)::uuid;object_project:=split_part(p_name,'/',2)::uuid;object_attempt:=split_part(p_name,'/',3)::uuid;
 EXCEPTION WHEN invalid_text_representation THEN RETURN false;
 END;
 IF NOT platform.has_permission(object_workspace,CASE WHEN p_write THEN 'deep_dub.task.run' ELSE 'deep_dub.project.read' END) THEN RETURN false; END IF;
 IF NOT p_write THEN RETURN EXISTS(SELECT 1 FROM platform.deep_dub_projects WHERE workspace_id=object_workspace AND id=object_project); END IF;
 RETURN EXISTS(SELECT 1 FROM platform.deep_dub_stage_attempts a
 JOIN platform.deep_dub_stages s ON (s.workspace_id,s.id)=(a.workspace_id,a.stage_id)
 JOIN platform.deep_dub_runs r ON (r.workspace_id,r.id)=(s.workspace_id,s.run_id)
 JOIN platform.tasks t ON (t.workspace_id,t.id)=(r.workspace_id,r.task_id)
 WHERE a.workspace_id=object_workspace AND a.project_id=object_project AND a.id=object_attempt AND a.created_by=auth.uid()
 AND a.status='running' AND s.current_attempt_id=a.id AND (r.status='running' OR platform.deep_dub_source_recheck_permitted(r.workspace_id,r.id,s.node,s.partition_key,s.required_device_kind)) AND t.status='running' AND NOT t.cancel_requested
 AND (s.required_device_kind IS NULL OR EXISTS(SELECT 1 FROM platform.deep_dub_device_leases l
 WHERE l.workspace_id=object_workspace AND l.stage_attempt_id=a.id AND l.status='active' AND l.expires_at>statement_timestamp())));
END $$;

CREATE OR REPLACE FUNCTION platform.deep_dub_candidate_attempt(p_workspace uuid,p_attempt uuid)
RETURNS platform.deep_dub_stage_attempts LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_stage_attempts; project uuid; kind text;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN RAISE EXCEPTION '无权登记执行候选' USING ERRCODE='42501'; END IF;
 SELECT project_id INTO project FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND id=p_attempt;
 IF NOT FOUND THEN RAISE EXCEPTION '执行尝试不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=project FOR UPDATE;
 result:=platform.deep_dub_lock_attempt(p_workspace,p_attempt);
 IF EXISTS(SELECT 1 FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=result.stage_id AND node='source_boundary_recheck') THEN
  RAISE EXCEPTION '来源返修只能保存证据，禁止登记生产候选' USING ERRCODE='PT409'; END IF;
 IF result.status<>'running' THEN RAISE EXCEPTION '候选只能在实际执行阶段登记' USING ERRCODE='PT409'; END IF;
 SELECT required_device_kind INTO kind FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=result.stage_id;
 IF kind IS NOT NULL AND NOT EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace
 AND stage_attempt_id=p_attempt AND status='active' AND expires_at>clock_timestamp()) THEN
  RAISE EXCEPTION '设备租约无效，拒绝候选提交' USING ERRCODE='PT409'; END IF;
 RETURN result;
END $$;
NOTIFY pgrst,'reload schema';
