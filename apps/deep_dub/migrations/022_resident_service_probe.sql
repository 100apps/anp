-- 常驻模型的执行槽位是共享GPU服务，不能伪造“无外部进程”的物理池证明。
CREATE OR REPLACE FUNCTION platform.deep_dub_confirm_resident_probe(
 p_workspace uuid,p_device uuid,p_expected_version bigint,p_probe jsonb
) RETURNS platform.deep_dub_devices LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_devices;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.device.manage') THEN
  RAISE EXCEPTION '无权核对常驻服务槽位' USING ERRCODE='42501'; END IF;
 IF p_probe IS NULL OR jsonb_typeof(p_probe)<>'object'
 OR p_probe->>'device_scope' IS DISTINCT FROM 'resident-model-service-slot'
 OR p_probe->'all_owned_operations_stopped' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'has_external_processes' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'resident_service_preserved' IS DISTINCT FROM 'true'::jsonb
 OR p_probe->'physical_gpu_exclusive' IS DISTINCT FROM 'false'::jsonb
 OR jsonb_typeof(p_probe->'host_boot_id') IS DISTINCT FROM 'string'
 OR length(p_probe->>'host_boot_id') NOT BETWEEN 1 AND 200
 OR jsonb_typeof(p_probe->'service_identity') IS DISTINCT FROM 'object'
 OR jsonb_typeof(p_probe->'service_identity'->'pid') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_probe->'service_identity'->'start_ticks') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_probe->'port') IS DISTINCT FROM 'number'
 OR p_probe->'service_identity'->>'boot_id' IS DISTINCT FROM p_probe->>'host_boot_id'
 OR coalesce(p_probe->'service_identity'->>'executable_sha256','') !~ '^[0-9a-f]{64}$'
 OR coalesce(p_probe->'service_identity'->>'weights_sha256','') !~ '^[0-9a-f]{64}$'
 OR coalesce(p_probe->'service_identity'->>'pid','') !~ '^[1-9][0-9]*$'
 OR coalesce(p_probe->'service_identity'->>'start_ticks','') !~ '^[1-9][0-9]*$'
 OR coalesce(p_probe->>'port','') !~ '^[1-9][0-9]{3,4}$' THEN
  RAISE EXCEPTION '缺少常驻槽位身份、保留服务与本工作区停止证明' USING ERRCODE='23514'; END IF;
 SELECT * INTO result FROM platform.deep_dub_devices WHERE workspace_id=p_workspace AND id=p_device FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '设备不存在' USING ERRCODE='PT404'; END IF;
 IF result.row_version IS DISTINCT FROM p_expected_version THEN RAISE EXCEPTION '设备状态已改变' USING ERRCODE='PT409'; END IF;
 IF result.device_key IS DISTINCT FROM 'anp-resident-tts:'||result.host_key||':'||(p_probe->>'port')
 OR result.kind<>'gpu' OR result.capacity<>1 OR (p_probe->>'port')::integer NOT BETWEEN 1024 AND 65535 THEN
  RAISE EXCEPTION '探针只能确认指定单槽位，不能确认物理设备' USING ERRCODE='23514'; END IF;
 IF result.state='quarantined' OR EXISTS(SELECT 1 FROM platform.deep_dub_device_leases
  WHERE workspace_id=p_workspace AND device_id=p_device AND status<>'released') THEN
  RAISE EXCEPTION '仍有未确认停止的租约，禁止自动恢复常驻槽位' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_devices SET state='ready',probe=p_probe,probed_at=clock_timestamp(),row_version=row_version+1
 WHERE id=p_device RETURNING * INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_confirm_resident_probe(uuid,uuid,bigint,jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_confirm_resident_probe(uuid,uuid,bigint,jsonb) TO authenticated;
NOTIFY pgrst,'reload schema';
