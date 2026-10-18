-- 这里只建立门禁和回执账本，不执行投稿。上传适配必须另行取得真实站点回执。
CREATE OR REPLACE FUNCTION platform.deep_dub_assert_latest_audits(
 p_workspace uuid,p_project uuid,p_kind text,p_subject uuid,p_profile uuid,p_rules text[]
) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE fingerprint text; rule text; latest platform.deep_dub_audits;
BEGIN
 fingerprint:=platform.deep_dub_subject_hash(p_workspace,p_kind,p_subject);
 FOREACH rule IN ARRAY p_rules LOOP
  SELECT * INTO latest FROM platform.deep_dub_audits a WHERE a.workspace_id=p_workspace AND a.project_id=p_project
   AND a.quality_profile_id=p_profile AND a.subject_hash=fingerprint AND a.rule_key=rule
   AND ((p_kind='render' AND a.render_id=p_subject) OR (p_kind='source' AND a.source_id=p_subject)
   OR (p_kind='background' AND a.background_id=p_subject) OR (p_kind='voice_reference' AND a.voice_reference_id=p_subject)
   OR (p_kind='timeline' AND a.timeline_id=p_subject)) ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
  IF latest.id IS NULL OR latest.status<>'passed' OR latest.evidence_artifact_id IS NULL THEN
   RAISE EXCEPTION '缺少当前版本的必需通过审核：%/%',p_kind,rule USING ERRCODE='23514'; END IF;
 END LOOP;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_assert_render_ready(p_workspace uuid,p_render uuid)
RETURNS platform.deep_dub_renders LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_renders; timeline platform.deep_dub_timeline_revisions; profile platform.deep_dub_quality_profiles; item record;
BEGIN
 SELECT * INTO result FROM platform.deep_dub_renders WHERE workspace_id=p_workspace AND id=p_render;
 IF NOT FOUND THEN RAISE EXCEPTION '成片不存在' USING ERRCODE='PT404'; END IF;
 SELECT * INTO timeline FROM platform.deep_dub_timeline_revisions WHERE workspace_id=p_workspace AND id=result.timeline_id;
 IF NOT timeline.sealed THEN RAISE EXCEPTION '时间线尚未封存' USING ERRCODE='23514'; END IF;
 SELECT * INTO profile FROM platform.deep_dub_quality_profiles WHERE workspace_id=p_workspace AND id=result.quality_profile_id;
 PERFORM platform.deep_dub_assert_latest_audits(p_workspace,result.project_id,'source',result.source_id,profile.id,ARRAY['identity','duration','tracks','transcript_coverage']);
 PERFORM platform.deep_dub_assert_latest_audits(p_workspace,result.project_id,'timeline',timeline.id,profile.id,ARRAY['coverage','collision','bounds']);
 FOR item IN SELECT c.selection_id,a.voice_reference_id FROM platform.deep_dub_timeline_clips c
 JOIN platform.deep_dub_selections s ON (s.workspace_id,s.id)=(c.workspace_id,c.selection_id)
 JOIN platform.deep_dub_audio_attempts a ON (a.workspace_id,a.id)=(s.workspace_id,s.audio_attempt_id)
 WHERE c.workspace_id=p_workspace AND c.timeline_id=timeline.id LOOP
  PERFORM platform.deep_dub_assert_selection_current(p_workspace,item.selection_id);
  IF profile.profile_key='high' THEN PERFORM platform.deep_dub_assert_latest_audits(p_workspace,result.project_id,'voice_reference',item.voice_reference_id,
   profile.id,ARRAY['speaker_identity','reference_quality']); END IF;
 END LOOP;
 IF profile.rules->>'background_mode'='preserve' THEN
  IF result.background_id IS NULL THEN RAISE EXCEPTION '缺少背景版本' USING ERRCODE='23514'; END IF;
  PERFORM platform.deep_dub_assert_latest_audits(p_workspace,result.project_id,'background',result.background_id,profile.id,ARRAY['decode','source_language_leakage','event_preservation']);
 END IF;
 PERFORM platform.deep_dub_assert_latest_audits(p_workspace,result.project_id,'render',p_render,profile.id,
 ARRAY['full_decode','loudness','duration','subtitles','snapshot','content','collision','bounds']);
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_verify_run(p_workspace uuid,p_run uuid,p_render uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE run platform.deep_dub_runs; render platform.deep_dub_renders; task platform.tasks; duration bigint; stamp timestamptz; ratio numeric;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.audit') OR NOT platform.has_permission(p_workspace,'deep_dub.task.run') THEN
  RAISE EXCEPTION '无权确认整片验收' USING ERRCODE='42501'; END IF;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run;
 IF NOT FOUND THEN RAISE EXCEPTION '运行不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=run.project_id FOR UPDATE;
 SELECT * INTO task FROM platform.tasks WHERE workspace_id=p_workspace AND id=run.task_id FOR UPDATE;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run FOR UPDATE;
 render:=platform.deep_dub_assert_render_ready(p_workspace,p_render);
 IF render.run_id<>p_run THEN RAISE EXCEPTION '不能使用其他运行的成片' USING ERRCODE='23503'; END IF;
 IF run.reuse_policy='none' AND EXISTS(
  WITH RECURSIVE lineage(id,parent_id,stage_attempt_id,path,cycle) AS (
   SELECT a.id,a.parent_id,a.stage_attempt_id,ARRAY[a.id],false FROM platform.deep_dub_timeline_clips c
   JOIN platform.deep_dub_selections s ON (s.workspace_id,s.id)=(c.workspace_id,c.selection_id)
   JOIN platform.deep_dub_audio_attempts a ON (a.workspace_id,a.id)=(s.workspace_id,s.audio_attempt_id)
   WHERE c.workspace_id=p_workspace AND c.timeline_id=render.timeline_id
   UNION ALL SELECT a.id,a.parent_id,a.stage_attempt_id,l.path||a.id,a.id=ANY(l.path)
   FROM lineage l JOIN platform.deep_dub_audio_attempts a ON a.workspace_id=p_workspace AND a.id=l.parent_id WHERE NOT l.cycle)
  SELECT 1 FROM lineage l JOIN platform.deep_dub_stage_attempts a ON a.workspace_id=p_workspace AND a.id=l.stage_attempt_id
  JOIN platform.deep_dub_stages s ON (s.workspace_id,s.id)=(a.workspace_id,a.stage_id)
  WHERE l.cycle OR s.run_id<>p_run
 ) THEN RAISE EXCEPTION '全新生产不能复用其他轮次的音频或派生父版本' USING ERRCODE='23514'; END IF;
 IF run.status='succeeded' THEN
  IF task.result->>'render_id' IS DISTINCT FROM p_render::text THEN RAISE EXCEPTION '验收回执不能换成其他成片' USING ERRCODE='23505'; END IF;
  SELECT duration_ms INTO duration FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND id=run.source_id;
  ratio:=extract(epoch FROM run.technical_verified_at-run.queued_at)*1000/duration;
  RETURN jsonb_build_object('verified',true,'run',to_jsonb(run),'rtf',ratio,'performance_passed',ratio<=0.5 AND run.reuse_policy='none');
 END IF;
 IF run.status<>'running' OR task.status<>'running' OR task.cancel_requested THEN RAISE EXCEPTION '运行已停止或取消' USING ERRCODE='PT409'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND run_id=p_run)
 OR EXISTS(SELECT 1 FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND run_id=p_run AND (status<>'succeeded' OR current_attempt_id IS NULL)) THEN
  RAISE EXCEPTION '仍有未完成或待审阶段' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM platform.deep_dub_device_leases l JOIN platform.deep_dub_stage_attempts a ON (a.workspace_id,a.id)=(l.workspace_id,l.stage_attempt_id)
 JOIN platform.deep_dub_stages s ON (s.workspace_id,s.id)=(a.workspace_id,a.stage_id)
 WHERE s.workspace_id=p_workspace AND s.run_id=p_run AND (l.status<>'released' OR NOT l.operation_stopped)) THEN
  RAISE EXCEPTION '仍有未确认停止的真实运算' USING ERRCODE='23514'; END IF;
 SELECT duration_ms INTO duration FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND id=run.source_id;
 stamp:=clock_timestamp();ratio:=extract(epoch FROM stamp-run.queued_at)*1000/duration;
 UPDATE platform.deep_dub_runs SET status='succeeded',technical_verified_at=stamp WHERE id=p_run RETURNING * INTO run;
 UPDATE platform.tasks SET status='succeeded',updated_at=stamp,result=jsonb_build_object('run_id',p_run,'render_id',p_render,'rtf',ratio,'performance_passed',ratio<=0.5 AND run.reuse_policy='none') WHERE id=run.task_id;
 RETURN jsonb_build_object('verified',true,'run',to_jsonb(run),'rtf',ratio,'performance_passed',ratio<=0.5 AND run.reuse_policy='none');
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_request_publication(p_workspace uuid,p_render uuid,p_request_key text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE render platform.deep_dub_renders; run platform.deep_dub_runs; result platform.deep_dub_publications; fingerprint text; video_hash text; duration bigint;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.publish') THEN RAISE EXCEPTION '无权申请发布' USING ERRCODE='42501'; END IF;
 IF p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION '发布幂等键不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO render FROM platform.deep_dub_renders WHERE workspace_id=p_workspace AND id=p_render;
 IF NOT FOUND THEN RAISE EXCEPTION '成片不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=render.project_id FOR UPDATE;
 render:=platform.deep_dub_assert_render_ready(p_workspace,p_render);
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=render.run_id;
 SELECT duration_ms INTO duration FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND id=run.source_id;
 IF run.purpose<>'production' OR run.reuse_policy<>'none' OR run.status<>'succeeded' OR run.technical_verified_at IS NULL
 OR extract(epoch FROM run.technical_verified_at-run.queued_at)*1000/duration>0.5 THEN
  RAISE EXCEPTION '仅完整技术和性能门禁通过的生产轮次允许发布' USING ERRCODE='23514'; END IF;
 SELECT sha256 INTO video_hash FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND id=render.video_artifact_id;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_render,video_hash,'bilibili'));
 SELECT * INTO result FROM platform.deep_dub_publications WHERE workspace_id=p_workspace AND project_id=render.project_id AND request_key=p_request_key;
 IF FOUND AND result.request_hash<>fingerprint THEN RAISE EXCEPTION '发布幂等键内容冲突' USING ERRCODE='23505'; END IF;
 SELECT * INTO result FROM platform.deep_dub_publications WHERE workspace_id=p_workspace AND render_id=p_render AND platform='bilibili';
 IF FOUND THEN RETURN to_jsonb(result)-'claim_token_hash'; END IF;
 INSERT INTO platform.deep_dub_publications(workspace_id,project_id,render_id,platform,revision,request_key,request_hash,video_sha256,created_by)
 VALUES(p_workspace,render.project_id,p_render,'bilibili',1,p_request_key,fingerprint,video_hash,auth.uid()) RETURNING * INTO result;
 RETURN to_jsonb(result)-'claim_token_hash';
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_claim_publication(p_workspace uuid,p_publication uuid,p_token text,p_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_publications;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.publish') THEN RAISE EXCEPTION '无权执行发布' USING ERRCODE='42501'; END IF;
 IF p_token IS NULL OR octet_length(p_token) NOT BETWEEN 32 AND 512 OR p_seconds IS NULL OR p_seconds NOT BETWEEN 30 AND 3600 THEN
  RAISE EXCEPTION '上传租约参数不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO result FROM platform.deep_dub_publications WHERE workspace_id=p_workspace AND id=p_publication;
 IF NOT FOUND THEN RAISE EXCEPTION '发布记录不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=result.project_id FOR UPDATE;
 SELECT * INTO result FROM platform.deep_dub_publications WHERE workspace_id=p_workspace AND id=p_publication FOR UPDATE;
 IF result.status='uploading' AND result.claim_expires_at<=clock_timestamp() THEN
  UPDATE platform.deep_dub_publications SET status='receipt_unknown' WHERE id=p_publication;
  RETURN jsonb_build_object('claimed',false,'reason','RECEIPT_UNKNOWN'); END IF;
 IF result.status='uploading' THEN RETURN jsonb_build_object('claimed',false,'reason','UPLOAD_IN_PROGRESS'); END IF;
 IF result.status<>'requested' THEN RETURN jsonb_build_object('claimed',false,'reason',upper(result.status)); END IF;
 PERFORM platform.deep_dub_assert_render_ready(p_workspace,result.render_id);
 UPDATE platform.deep_dub_publications SET status='uploading',claim_token_hash=encode(sha256(convert_to(p_token,'UTF8')),'hex'),
 claimed_by=auth.uid(),claim_expires_at=clock_timestamp()+make_interval(secs=>p_seconds) WHERE id=p_publication RETURNING * INTO result;
 RETURN jsonb_build_object('claimed',true,'publication',to_jsonb(result)-'claim_token_hash');
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_record_publication_receipt(
 p_workspace uuid,p_publication uuid,p_token text,p_video_hash text,p_receipt jsonb,p_evidence uuid
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_publications;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.publish') THEN RAISE EXCEPTION '无权登记发布回执' USING ERRCODE='42501'; END IF;
 SELECT * INTO result FROM platform.deep_dub_publications WHERE workspace_id=p_workspace AND id=p_publication FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '发布记录不存在' USING ERRCODE='PT404'; END IF;
 IF result.claimed_by IS DISTINCT FROM auth.uid() OR result.claim_token_hash IS DISTINCT FROM encode(sha256(convert_to(p_token,'UTF8')),'hex') THEN
  RAISE EXCEPTION '上传访问凭据不正确' USING ERRCODE='42501'; END IF;
 IF result.video_sha256 IS DISTINCT FROM p_video_hash OR p_receipt IS NULL OR jsonb_typeof(p_receipt)<>'object'
 OR NOT coalesce(p_receipt->>'bvid' ~ '^BV[0-9A-Za-z]{10}$',false) OR p_receipt->>'provider_code' IS DISTINCT FROM '0'
 OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_receipt) k WHERE k NOT IN ('bvid','aid','provider_code')) THEN
  RAISE EXCEPTION '回执身份或内容摘要不匹配，禁止写入账户凭据' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=result.project_id AND id=p_evidence AND media_type='application/json') THEN
  RAISE EXCEPTION '缺少回执证据对象' USING ERRCODE='23503'; END IF;
 IF result.status IN ('submitted','published') THEN
  IF result.receipt<>p_receipt OR result.receipt_artifact_id IS DISTINCT FROM p_evidence THEN RAISE EXCEPTION '回执不能覆盖' USING ERRCODE='23505'; END IF;
  RETURN to_jsonb(result)-'claim_token_hash'; END IF;
 IF result.status NOT IN ('uploading','receipt_unknown') THEN RAISE EXCEPTION '发布状态不允许登记回执' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_publications SET status='submitted',receipt=p_receipt,receipt_artifact_id=p_evidence,submitted_at=clock_timestamp()
 WHERE id=p_publication RETURNING * INTO result;
 RETURN to_jsonb(result)-'claim_token_hash';
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_confirm_publication(p_workspace uuid,p_publication uuid,p_public_url text,p_evidence uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_publications;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.publish') THEN RAISE EXCEPTION '无权核实公开状态' USING ERRCODE='42501'; END IF;
 SELECT * INTO result FROM platform.deep_dub_publications WHERE workspace_id=p_workspace AND id=p_publication FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '发布记录不存在' USING ERRCODE='PT404'; END IF;
 IF result.status NOT IN ('submitted','published') THEN RAISE EXCEPTION '尚未取得投稿回执' USING ERRCODE='PT409'; END IF;
 IF p_public_url IS DISTINCT FROM 'https://www.bilibili.com/video/'||(result.receipt->>'bvid')||'/' THEN
  RAISE EXCEPTION '公开地址与回执不匹配' USING ERRCODE='23514'; END IF;
 IF p_evidence IS NOT DISTINCT FROM result.receipt_artifact_id THEN
  RAISE EXCEPTION '公开状态必须独立查证，不能复用投稿回执作证明' USING ERRCODE='23514'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=result.project_id AND id=p_evidence AND media_type='application/json') THEN
  RAISE EXCEPTION '缺少公开可见性查证证据' USING ERRCODE='23503'; END IF;
 IF result.status='published' THEN
  IF result.public_evidence_artifact_id IS DISTINCT FROM p_evidence THEN RAISE EXCEPTION '公开回执不能覆盖' USING ERRCODE='23505'; END IF;
  RETURN to_jsonb(result)-'claim_token_hash'; END IF;
 UPDATE platform.deep_dub_publications SET status='published',public_url=p_public_url,public_evidence_artifact_id=p_evidence,published_at=clock_timestamp()
 WHERE id=p_publication RETURNING * INTO result;
 RETURN to_jsonb(result)-'claim_token_hash';
END $$;
DO $$ DECLARE proc record; BEGIN
 FOR proc IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='platform' AND p.proname=ANY(ARRAY['deep_dub_verify_run','deep_dub_request_publication','deep_dub_claim_publication','deep_dub_record_publication_receipt','deep_dub_confirm_publication']) LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,anon',proc.signature);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated',proc.signature);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_assert_latest_audits(uuid,uuid,text,uuid,uuid,text[]),platform.deep_dub_assert_render_ready(uuid,uuid) FROM PUBLIC,anon,authenticated;
NOTIFY pgrst,'reload schema';
