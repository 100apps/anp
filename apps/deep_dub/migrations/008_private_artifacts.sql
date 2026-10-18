-- 独立逻辑桶，仍由现有 Supabase Storage/RustFS 服务承载；不增加存储实例。
INSERT INTO storage.buckets(id,name,public) VALUES('deep-dub','deep-dub',false) ON CONFLICT(id) DO NOTHING;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM storage.buckets WHERE id='deep-dub' AND public) THEN
  RAISE EXCEPTION 'deep-dub 桶必须私有，拒绝沿用公开桶' USING ERRCODE='23514'; END IF;
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
 AND a.status='running' AND s.current_attempt_id=a.id AND r.status='running' AND t.status='running' AND NOT t.cancel_requested
 AND (s.required_device_kind IS NULL OR EXISTS(SELECT 1 FROM platform.deep_dub_device_leases l
 WHERE l.workspace_id=object_workspace AND l.stage_attempt_id=a.id AND l.status='active' AND l.expires_at>statement_timestamp())));
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_object_access(text,boolean) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_object_access(text,boolean) TO authenticated;
DROP POLICY IF EXISTS deep_dub_object_read ON storage.objects;
CREATE POLICY deep_dub_object_read ON storage.objects FOR SELECT TO authenticated
 USING(bucket_id='deep-dub' AND platform.deep_dub_object_access(name,false));
DROP POLICY IF EXISTS deep_dub_object_insert ON storage.objects;
CREATE POLICY deep_dub_object_insert ON storage.objects FOR INSERT TO authenticated
 WITH CHECK(bucket_id='deep-dub' AND platform.deep_dub_object_access(name,true));
-- 未授予 UPDATE/DELETE；旧 artifacts 桶策略不匹配本桶。

CREATE OR REPLACE FUNCTION platform.deep_dub_register_artifact(
 p_workspace uuid,p_attempt uuid,p_sha256 text,p_bytes bigint,p_media_type text,p_duration_ms bigint,p_sample_rate integer
) RETURNS platform.deep_dub_artifacts LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; result platform.deep_dub_artifacts; path text;
BEGIN
 attempt:=platform.deep_dub_lock_attempt(p_workspace,p_attempt);
 IF p_sha256 IS NULL OR p_sha256 !~ '^[a-f0-9]{64}$' OR p_bytes IS NULL OR p_bytes<0
 OR p_media_type IS NULL OR length(p_media_type) NOT BETWEEN 1 AND 200 OR p_media_type ~ '[[:cntrl:]]'
 OR p_duration_ms<0 OR p_sample_rate<=0 THEN RAISE EXCEPTION '产物元数据不合法' USING ERRCODE='22023'; END IF;
 path:=p_workspace::text||'/'||attempt.project_id::text||'/'||p_attempt::text||'/'||p_sha256;
 SELECT * INTO result FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND object_path='deep-dub/'||path;
 IF FOUND THEN
  IF result.bytes<>p_bytes OR result.media_type<>p_media_type OR result.duration_ms IS DISTINCT FROM p_duration_ms
  OR result.sample_rate IS DISTINCT FROM p_sample_rate THEN RAISE EXCEPTION '相同产物不能改写元数据' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 IF NOT platform.deep_dub_object_access(path,true) THEN RAISE EXCEPTION '尝试已结束或设备租约失效' USING ERRCODE='PT409'; END IF;
 IF NOT EXISTS(SELECT 1 FROM storage.objects WHERE bucket_id='deep-dub' AND name=path
 AND metadata->>'size'=p_bytes::text) THEN RAISE EXCEPTION '尚无匹配字节数的私有对象' USING ERRCODE='23514'; END IF;
 INSERT INTO platform.deep_dub_artifacts(workspace_id,project_id,stage_attempt_id,object_path,sha256,bytes,media_type,duration_ms,sample_rate,created_by)
 VALUES(p_workspace,attempt.project_id,p_attempt,'deep-dub/'||path,p_sha256,p_bytes,p_media_type,p_duration_ms,p_sample_rate,auth.uid())
 ON CONFLICT(workspace_id,object_path) DO NOTHING;
 SELECT * INTO result FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND object_path='deep-dub/'||path;
 IF result.bytes<>p_bytes OR result.media_type<>p_media_type OR result.duration_ms IS DISTINCT FROM p_duration_ms
 OR result.sample_rate IS DISTINCT FROM p_sample_rate THEN RAISE EXCEPTION '相同产物不能改写元数据' USING ERRCODE='23505'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_register_artifact(uuid,uuid,text,bigint,text,bigint,integer) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_register_artifact(uuid,uuid,text,bigint,text,bigint,integer) TO authenticated;
NOTIFY pgrst,'reload schema';
