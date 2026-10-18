-- 锁顺序：项目 -> 平台任务 -> run -> stage -> attempt -> device -> lease。
CREATE OR REPLACE FUNCTION platform.deep_dub_queue_run(
 p_workspace uuid,p_project uuid,p_profile uuid,p_mode text,p_purpose text,p_flow_revision text,p_expected_version bigint,p_request_key text,p_reuse_policy text DEFAULT 'none'
) RETURNS platform.deep_dub_runs LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE project platform.deep_dub_projects; result platform.deep_dub_runs; task uuid; fingerprint text; stamp timestamptz:=clock_timestamp(); rules jsonb;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.create') OR NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN
  RAISE EXCEPTION '无权提交译配运行' USING ERRCODE='42501'; END IF;
 IF p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 OR p_flow_revision IS NULL OR length(p_flow_revision) NOT BETWEEN 1 AND 200
 OR p_mode IS NULL OR p_mode NOT IN ('local','http') OR p_purpose IS NULL OR p_purpose NOT IN ('production','acceptance')
 OR p_reuse_policy IS NULL OR p_reuse_policy NOT IN ('none','approved_versions') THEN
  RAISE EXCEPTION '运行参数不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO project FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=p_project FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '项目不存在' USING ERRCODE='PT404'; END IF;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_project,p_profile,p_mode,p_purpose,p_flow_revision,p_expected_version,p_reuse_policy));
 SELECT * INTO result FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND project_id=p_project AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash IS DISTINCT FROM fingerprint THEN RAISE EXCEPTION '运行幂等键内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 IF project.row_version IS DISTINCT FROM p_expected_version THEN RAISE EXCEPTION '项目版本冲突' USING ERRCODE='PT409'; END IF;
 SELECT q.rules INTO rules FROM platform.deep_dub_quality_profiles q WHERE workspace_id=p_workspace AND id=p_profile;
 IF NOT coalesce(rules->'translation_required' @> '["meaning","facts","numbers","negation","names","complete"]'::jsonb,false)
 OR NOT coalesce(rules->'audio_required' @> '["content","decode","nonsilent","timing","collision","bounds"]'::jsonb,false) THEN
  RAISE EXCEPTION '运行缺少冻结的质量门禁' USING ERRCODE='23514'; END IF;
 INSERT INTO platform.tasks(workspace_id,created_by,app_id,title,input,request_key,mode)
 VALUES(p_workspace,auth.uid(),'deep_dub',left('译配：'||project.name,120),jsonb_build_object(
 'project_id',p_project,'source_url',project.source_url,'quality_profile_id',p_profile,'purpose',p_purpose,'flow_revision',p_flow_revision,
 'project_version',p_expected_version,'reuse_policy',p_reuse_policy),'deep_dub:'||gen_random_uuid()::text,p_mode) RETURNING id INTO task;
 INSERT INTO platform.deep_dub_runs(workspace_id,project_id,task_id,quality_profile_id,mode,purpose,reuse_policy,flow_revision,input_hash,request_key,request_hash,queued_at,created_by)
 VALUES(p_workspace,p_project,task,p_profile,p_mode,p_purpose,p_reuse_policy,p_flow_revision,
 platform.deep_dub_digest(jsonb_build_array(fingerprint,project.source_url)),p_request_key,fingerprint,stamp,auth.uid()) RETURNING * INTO result;
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_start_run(p_workspace uuid,p_run uuid)
RETURNS platform.deep_dub_runs LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_runs; task platform.tasks;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN RAISE EXCEPTION '无权启动运行' USING ERRCODE='42501'; END IF;
 SELECT * INTO result FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run;
 IF NOT FOUND THEN RAISE EXCEPTION '运行不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=result.project_id FOR KEY SHARE;
 SELECT * INTO task FROM platform.tasks WHERE workspace_id=p_workspace AND id=result.task_id FOR UPDATE;
 SELECT * INTO result FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run FOR UPDATE;
 IF task.cancel_requested OR task.status='cancelled' THEN RAISE EXCEPTION '任务已取消' USING ERRCODE='PT409'; END IF;
 IF result.status='running' AND task.status='running' THEN RETURN result; END IF;
 IF result.status<>'queued' OR task.status<>'pending' THEN RAISE EXCEPTION '运行状态不允许启动，不重置首次计时' USING ERRCODE='PT409'; END IF;
 PERFORM platform.begin_task(task.id,result.mode);
 UPDATE platform.deep_dub_runs SET status='running',started_at=clock_timestamp() WHERE id=p_run RETURNING * INTO result;
 RETURN result;
END $$;
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
 IF task.status<>'running' OR task.cancel_requested OR run.status<>'running' THEN RAISE EXCEPTION '运行已停止或取消' USING ERRCODE='PT409'; END IF;
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
 IF task.status<>'running' OR task.cancel_requested OR run.status<>'running' THEN RAISE EXCEPTION '运行已停止或取消' USING ERRCODE='PT409'; END IF;
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
CREATE OR REPLACE FUNCTION platform.deep_dub_register_device(
 p_workspace uuid,p_key text,p_host text,p_kind text,p_capacity integer
) RETURNS platform.deep_dub_devices LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_devices;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.device.manage') THEN RAISE EXCEPTION '无权登记设备' USING ERRCODE='42501'; END IF;
 IF p_host IS NULL OR length(p_host) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION '主机身份无效' USING ERRCODE='22023'; END IF;
 INSERT INTO platform.deep_dub_devices(workspace_id,device_key,host_key,kind,capacity,created_by)
 VALUES(p_workspace,p_key,p_host,p_kind,p_capacity,auth.uid()) ON CONFLICT(workspace_id,device_key) DO NOTHING;
 SELECT * INTO result FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND device_key=p_key;
 IF result.host_key<>p_host OR result.kind<>p_kind OR result.capacity<>p_capacity THEN RAISE EXCEPTION '设备身份或容量冲突' USING ERRCODE='23505'; END IF;
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_confirm_device_probe(
 p_workspace uuid,p_device uuid,p_expected_version bigint,p_probe jsonb
) RETURNS platform.deep_dub_devices LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_devices;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.device.manage') THEN RAISE EXCEPTION '无权核对设备' USING ERRCODE='42501'; END IF;
 IF p_probe IS NULL OR jsonb_typeof(p_probe)<>'object' OR p_probe->'all_owned_operations_stopped' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'has_external_processes' IS DISTINCT FROM 'false'::jsonb
 OR jsonb_typeof(p_probe->'host_boot_id') IS DISTINCT FROM 'string' OR length(p_probe->>'host_boot_id') NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION '需要完整的进程归属和停止证明，不能按低利用率猜测空闲' USING ERRCODE='23514'; END IF;
 SELECT * INTO result FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=p_device FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '设备不存在' USING ERRCODE='PT404'; END IF;
 IF result.row_version IS DISTINCT FROM p_expected_version THEN RAISE EXCEPTION '设备状态已改变' USING ERRCODE='PT409'; END IF;
 IF EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device AND status<>'released') THEN
  RAISE EXCEPTION '仍有未确认停止的租约，不能恢复分配' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_devices SET state='ready',probe=p_probe,probed_at=clock_timestamp(),row_version=row_version+1
 WHERE id=p_device RETURNING * INTO result;
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_acquire_lease(
 p_workspace uuid,p_attempt uuid,p_device uuid,p_owner text,p_token text,p_slots integer,p_seconds integer,p_request_key text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; stage platform.deep_dub_stages; device platform.deep_dub_devices;
 lease platform.deep_dub_device_leases; fingerprint text; used integer; stamp timestamptz:=clock_timestamp();
BEGIN
 attempt:=platform.deep_dub_lock_attempt(p_workspace,p_attempt);
 IF p_token IS NULL OR octet_length(p_token) NOT BETWEEN 32 AND 512 OR p_seconds IS NULL OR p_seconds NOT BETWEEN 5 AND 300
 OR p_slots IS NULL OR p_slots NOT BETWEEN 1 AND 128 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION '租约参数不合法' USING ERRCODE='22023'; END IF;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_device,p_owner,p_slots,p_seconds,encode(sha256(convert_to(p_token,'UTF8')),'hex')));
 SELECT * INTO device FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=p_device FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '设备不存在' USING ERRCODE='PT404'; END IF;
 stamp:=clock_timestamp();
 SELECT * INTO lease FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt AND request_key=p_request_key;
 IF FOUND THEN
  IF lease.request_hash<>fingerprint OR lease.created_by<>auth.uid() THEN RAISE EXCEPTION '租约幂等键内容冲突' USING ERRCODE='23505'; END IF;
  IF lease.status='active' AND lease.expires_at<=stamp THEN
   UPDATE platform.deep_dub_device_leases SET status='expired' WHERE id=lease.id RETURNING * INTO lease;
   UPDATE platform.deep_dub_devices SET state='quarantined',row_version=row_version+1 WHERE id=p_device;
  END IF;
  RETURN jsonb_build_object('acquired',lease.status='active' AND lease.expires_at>stamp,'lease',to_jsonb(lease)-'token_hash'-'request_hash');
 END IF;
 IF EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt) THEN
  RAISE EXCEPTION '同一次尝试不能再次分配资源；需要结束旧尝试并保留重试账本' USING ERRCODE='PT409'; END IF;
 IF attempt.status NOT IN ('queued','running') THEN RAISE EXCEPTION '尝试已结束' USING ERRCODE='PT409'; END IF;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id;
 IF stage.required_device_kind IS DISTINCT FROM device.kind THEN RAISE EXCEPTION '资源类型与阶段声明不匹配' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device AND status='active' AND expires_at<=stamp) THEN
  UPDATE platform.deep_dub_device_leases SET status='expired' WHERE workspace_id=p_workspace AND device_id=p_device AND status='active' AND expires_at<=stamp;
  UPDATE platform.deep_dub_devices SET state='quarantined',row_version=row_version+1 WHERE id=p_device;
  RETURN jsonb_build_object('acquired',false,'reason','DEVICE_QUARANTINED');
 END IF;
 IF device.state<>'ready' OR device.probed_at IS NULL OR device.probed_at<stamp-interval '5 minutes' THEN
  RETURN jsonb_build_object('acquired',false,'reason','DEVICE_NOT_READY'); END IF;
 SELECT coalesce(sum(slots),0) INTO used FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device AND status='active';
 IF used+p_slots>device.capacity THEN RETURN jsonb_build_object('acquired',false,'reason','DEVICE_BUSY'); END IF;
 UPDATE platform.deep_dub_devices SET fence_seq=fence_seq+1,row_version=row_version+1 WHERE id=p_device RETURNING * INTO device;
 INSERT INTO platform.deep_dub_device_leases(workspace_id,project_id,device_id,stage_attempt_id,owner_key,token_hash,fence,slots,acquired_at,heartbeat_at,expires_at,request_key,request_hash,created_by)
 VALUES(p_workspace,attempt.project_id,p_device,p_attempt,p_owner,encode(sha256(convert_to(p_token,'UTF8')),'hex'),device.fence_seq,p_slots,stamp,stamp,
 stamp+make_interval(secs=>p_seconds),p_request_key,fingerprint,auth.uid()) RETURNING * INTO lease;
 UPDATE platform.deep_dub_stage_attempts SET status='running',started_at=coalesce(started_at,stamp) WHERE id=p_attempt;
 UPDATE platform.deep_dub_stages SET status='running' WHERE id=attempt.stage_id;
 RETURN jsonb_build_object('acquired',true,'lease',to_jsonb(lease)-'token_hash'-'request_hash');
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_heartbeat_lease(p_workspace uuid,p_lease uuid,p_fence bigint,p_token text,p_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE lease platform.deep_dub_device_leases; device uuid; attempt platform.deep_dub_stage_attempts; stamp timestamptz:=clock_timestamp();
BEGIN
 SELECT * INTO lease FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND id=p_lease;
 IF NOT FOUND THEN RAISE EXCEPTION '租约不存在' USING ERRCODE='PT404'; END IF;
 attempt:=platform.deep_dub_lock_attempt(p_workspace,lease.stage_attempt_id);
 PERFORM 1 FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=lease.device_id FOR UPDATE;
 SELECT * INTO lease FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND id=p_lease FOR UPDATE;
 stamp:=clock_timestamp();
 IF lease.created_by<>auth.uid() OR lease.fence IS DISTINCT FROM p_fence OR lease.token_hash IS DISTINCT FROM encode(sha256(convert_to(p_token,'UTF8')),'hex') THEN
  RAISE EXCEPTION '租约访问凭据不正确' USING ERRCODE='42501'; END IF;
 IF p_seconds IS NULL OR p_seconds NOT BETWEEN 5 AND 300 THEN RAISE EXCEPTION '续租时长不合法' USING ERRCODE='22023'; END IF;
 IF lease.status='active' AND lease.expires_at<=stamp THEN
  UPDATE platform.deep_dub_device_leases SET status='expired' WHERE id=p_lease;
  UPDATE platform.deep_dub_devices SET state='quarantined',row_version=row_version+1 WHERE id=lease.device_id;
  RETURN jsonb_build_object('renewed',false,'reason','LEASE_EXPIRED');
 END IF;
 IF lease.status<>'active' THEN RETURN jsonb_build_object('renewed',false,'reason','LEASE_INACTIVE'); END IF;
 UPDATE platform.deep_dub_device_leases SET heartbeat_at=stamp,expires_at=greatest(expires_at,stamp+make_interval(secs=>p_seconds)) WHERE id=p_lease RETURNING * INTO lease;
 RETURN jsonb_build_object('renewed',true,'lease',to_jsonb(lease)-'token_hash'-'request_hash');
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_release_lease(p_workspace uuid,p_lease uuid,p_fence bigint,p_token text,p_operation_stopped boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE lease platform.deep_dub_device_leases;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN RAISE EXCEPTION '无权释放资源' USING ERRCODE='42501'; END IF;
 SELECT * INTO lease FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND id=p_lease;
 IF NOT FOUND THEN RAISE EXCEPTION '租约不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=lease.device_id FOR UPDATE;
 SELECT * INTO lease FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND id=p_lease FOR UPDATE;
 IF lease.created_by<>auth.uid() OR lease.fence IS DISTINCT FROM p_fence OR lease.token_hash IS DISTINCT FROM encode(sha256(convert_to(p_token,'UTF8')),'hex') THEN
  RAISE EXCEPTION '租约访问凭据不正确' USING ERRCODE='42501'; END IF;
 IF p_operation_stopped IS DISTINCT FROM true THEN RAISE EXCEPTION '尚未确认实际运算停止' USING ERRCODE='PT409'; END IF;
 IF lease.status='released' THEN RETURN jsonb_build_object('released',true); END IF;
 IF lease.expires_at<=clock_timestamp() THEN UPDATE platform.deep_dub_devices SET state='quarantined',row_version=row_version+1 WHERE id=lease.device_id; END IF;
 UPDATE platform.deep_dub_device_leases SET status='released',released_at=clock_timestamp(),operation_stopped=true,
 stop_evidence=jsonb_build_object('confirmed_by',auth.uid(),'owner_key',lease.owner_key,'method','owner-confirmed') WHERE id=p_lease;
 RETURN jsonb_build_object('released',true);
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_reconcile_device(
 p_workspace uuid,p_device uuid,p_expected_version bigint,p_lease_ids uuid[],p_probe jsonb
) RETURNS platform.deep_dub_devices LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_devices; stamp timestamptz;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.device.manage') THEN RAISE EXCEPTION '无权核对失联作业' USING ERRCODE='42501'; END IF;
 IF p_probe IS NULL OR p_probe->'all_owned_operations_stopped' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'has_external_processes' IS DISTINCT FROM 'false'::jsonb
 OR jsonb_typeof(p_probe->'host_boot_id') IS DISTINCT FROM 'string' THEN
  RAISE EXCEPTION '缺少实际运算停止和外部进程核对证明' USING ERRCODE='23514'; END IF;
 SELECT * INTO result FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=p_device FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '设备不存在' USING ERRCODE='PT404'; END IF;
 IF result.row_version IS DISTINCT FROM p_expected_version THEN RAISE EXCEPTION '设备状态已改变' USING ERRCODE='PT409'; END IF;
 stamp:=clock_timestamp();
 IF EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device AND status='active' AND expires_at>stamp) THEN
  RAISE EXCEPTION '仍有有效运算，禁止接管设备' USING ERRCODE='PT409'; END IF;
 IF p_lease_ids IS NULL OR cardinality(p_lease_ids)<>(SELECT count(DISTINCT x) FROM unnest(p_lease_ids) x)
 OR EXISTS(SELECT id FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device AND status<>'released' EXCEPT SELECT unnest(p_lease_ids))
 OR EXISTS(SELECT unnest(p_lease_ids) EXCEPT SELECT id FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device AND status<>'released') THEN
  RAISE EXCEPTION '失联租约集合已变化，必须重新核对' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_device_leases SET status='released',operation_stopped=true,released_at=stamp,
 stop_evidence=jsonb_build_object('confirmed_by',auth.uid(),'method','administrator-reconciled','probe',p_probe)
 WHERE workspace_id=p_workspace AND device_id=p_device AND status<>'released';
 UPDATE platform.deep_dub_devices SET state='ready',probe=p_probe,probed_at=stamp,row_version=row_version+1 WHERE id=p_device RETURNING * INTO result;
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_check_run_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
BEGIN
 IF (to_jsonb(OLD)-ARRAY['status','source_id','started_at','technical_verified_at','app_id'])<>(to_jsonb(NEW)-ARRAY['status','source_id','started_at','technical_verified_at','app_id'])
 OR (OLD.source_id IS NOT NULL AND OLD.source_id IS DISTINCT FROM NEW.source_id)
 OR (OLD.started_at IS NOT NULL AND OLD.started_at IS DISTINCT FROM NEW.started_at)
 OR (OLD.technical_verified_at IS NOT NULL AND OLD.technical_verified_at IS DISTINCT FROM NEW.technical_verified_at)
 OR (OLD.status IN ('succeeded','failed','cancelled') AND NEW.status<>OLD.status) THEN
  RAISE EXCEPTION '运行身份、冻结输入和首次时间不能改写' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS run_identity ON platform.deep_dub_runs;
CREATE TRIGGER run_identity BEFORE UPDATE ON platform.deep_dub_runs FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_check_run_identity();
DO $$ DECLARE proc record; BEGIN
 FOR proc IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='platform' AND p.proname=ANY(ARRAY['deep_dub_queue_run','deep_dub_start_run','deep_dub_begin_stage','deep_dub_register_device','deep_dub_confirm_device_probe','deep_dub_acquire_lease','deep_dub_heartbeat_lease','deep_dub_release_lease','deep_dub_reconcile_device']) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon',proc.signature);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated',proc.signature);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_lock_attempt(uuid,uuid) FROM PUBLIC,anon,authenticated;
NOTIFY pgrst,'reload schema';
