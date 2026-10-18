-- 常驻服务失联恢复只释放已核对的服务槽位；不把共享GPU伪装成无外部进程。
CREATE OR REPLACE FUNCTION platform.deep_dub_reconcile_resident(
 p_workspace uuid,p_device uuid,p_expected_version bigint,p_attempt uuid,p_lease_ids uuid[],p_probe jsonb
) RETURNS platform.deep_dub_devices LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_devices; leases uuid[]; receipt jsonb; identity jsonb;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.device.manage') OR NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN
  RAISE EXCEPTION '无权核对失联常驻作业' USING ERRCODE='42501'; END IF;
 SELECT * INTO result FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=p_device FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '服务设备不存在' USING ERRCODE='PT404'; END IF;
 IF result.row_version IS DISTINCT FROM p_expected_version THEN RAISE EXCEPTION '设备版本已改变' USING ERRCODE='PT409'; END IF;
 identity:=result.probe->'service_identity';receipt:=p_probe->'remote_receipt';
 IF result.device_key IS DISTINCT FROM 'anp-resident-tts:'||result.host_key||':'||(result.probe->>'port')
 OR result.kind<>'gpu' OR result.capacity<>1
 OR result.probe->>'device_scope' IS DISTINCT FROM 'resident-model-service-slot'
 OR p_probe->>'device_scope' IS DISTINCT FROM 'resident-model-service-slot'
 OR p_probe->'all_owned_operations_stopped' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'has_external_processes' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'physical_gpu_exclusive' IS DISTINCT FROM 'false'::jsonb
 OR p_probe->'resident_service_preserved' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'service_identity' IS DISTINCT FROM identity
 OR p_probe->>'host_boot_id' IS DISTINCT FROM identity->>'boot_id'
 OR p_probe->'port' IS DISTINCT FROM result.probe->'port'
 OR receipt->>'job_id' IS DISTINCT FROM p_attempt::text
 OR receipt->'operation_stopped' IS DISTINCT FROM 'true'::jsonb
 OR receipt->'worker_running' IS DISTINCT FROM 'false'::jsonb
 OR receipt->'resident_service_stopped' IS DISTINCT FROM 'false'::jsonb
 OR receipt->'service_identity' IS DISTINCT FROM identity
 OR coalesce(receipt->>'status','') NOT IN ('succeeded','failed')
 OR coalesce(receipt->>'input_sha256','') !~ '^[0-9a-f]{64}$'
 OR coalesce(receipt->>'worker_sha256','') !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION '缺少本次常驻作业的完整停止证明' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device FOR UPDATE;
 SELECT array_agg(id ORDER BY id) INTO leases FROM platform.deep_dub_device_leases
 WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt AND device_id=p_device;
 IF p_lease_ids IS NULL OR cardinality(p_lease_ids) NOT BETWEEN 1 AND 100 OR array_position(p_lease_ids,NULL) IS NOT NULL
 OR cardinality(p_lease_ids)<>(SELECT count(DISTINCT x) FROM unnest(p_lease_ids) x)
 OR leases IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM unnest(p_lease_ids) x)
 OR EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND stage_attempt_id=p_attempt AND device_id<>p_device)
 OR EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace AND device_id=p_device
 AND ((status='active' AND expires_at>clock_timestamp()) OR (status<>'released' AND stage_attempt_id<>p_attempt))) THEN
  RAISE EXCEPTION '租约集合改变或仍有有效owner，禁止恢复' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_device_leases SET status='released',operation_stopped=true,released_at=clock_timestamp(),
 stop_evidence=jsonb_build_object('confirmed_by',auth.uid(),'method','administrator-reconciled','probe',p_probe)
 WHERE workspace_id=p_workspace AND device_id=p_device AND stage_attempt_id=p_attempt AND status<>'released';
 UPDATE platform.deep_dub_devices SET state='ready',probe=p_probe,probed_at=clock_timestamp(),row_version=row_version+1
 WHERE id=p_device RETURNING * INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_reconcile_resident(uuid,uuid,bigint,uuid,uuid[],jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_reconcile_resident(uuid,uuid,bigint,uuid,uuid[],jsonb) TO authenticated;
NOTIFY pgrst,'reload schema';
