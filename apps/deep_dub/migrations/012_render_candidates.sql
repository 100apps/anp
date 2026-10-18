DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['deep_dub_backgrounds','deep_dub_renders'] LOOP
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS stage_attempt_id uuid',name);
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS evidence_artifact_id uuid',name);
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS request_key text',name);
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS request_hash text',name);
  IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=('platform.'||name)::regclass AND conname=name||'_producer') THEN
   EXECUTE format('ALTER TABLE platform.%I ADD CONSTRAINT %I FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id)',name,name||'_producer');
   EXECUTE format('ALTER TABLE platform.%I ADD CONSTRAINT %I FOREIGN KEY(workspace_id,project_id,evidence_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id)',name,name||'_evidence');
  END IF;
  EXECUTE format('CREATE UNIQUE INDEX IF NOT EXISTS %I ON platform.%I(workspace_id,project_id,request_key)',name||'_request',name);
 END LOOP;
END $$;
ALTER TABLE platform.deep_dub_renders ADD COLUMN IF NOT EXISTS parameters jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(parameters)='object');

CREATE OR REPLACE FUNCTION platform.deep_dub_require_media_artifact(
 p_workspace uuid,p_attempt uuid,p_artifact uuid,p_type text,p_duration bigint
) RETURNS platform.deep_dub_artifacts LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_artifacts;
BEGIN
 SELECT * INTO result FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND id=p_artifact;
 IF NOT FOUND OR result.stage_attempt_id IS DISTINCT FROM p_attempt OR result.bytes<=0 OR result.media_type NOT LIKE p_type
 OR (p_duration IS NOT NULL AND (result.duration_ms IS NULL OR abs(result.duration_ms-p_duration)>100)) THEN
  RAISE EXCEPTION '产物归属、类型、字节数或全片时长不匹配' USING ERRCODE='23514'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_require_media_artifact(uuid,uuid,uuid,text,bigint) FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION platform.deep_dub_register_background(
 p_workspace uuid,p_attempt uuid,p_original uuid,p_processed uuid,p_removed uuid,p_evidence uuid,
 p_model text,p_parameters jsonb,p_request_key text
) RETURNS platform.deep_dub_backgrounds LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; source platform.deep_dub_sources; result platform.deep_dub_backgrounds;
 fingerprint text;
BEGIN
 attempt:=platform.deep_dub_candidate_attempt(p_workspace,p_attempt);
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id AND required_device_kind IS NOT NULL) THEN
  RAISE EXCEPTION '背景处理必须声明实际设备租约' USING ERRCODE='23514'; END IF;
 IF p_model IS NULL OR length(btrim(p_model)) NOT BETWEEN 1 AND 200 OR p_parameters IS NULL OR jsonb_typeof(p_parameters)<>'object'
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION '背景候选参数不合法' USING ERRCODE='22023'; END IF;
 SELECT v.* INTO source FROM platform.deep_dub_sources v JOIN platform.deep_dub_runs r ON (r.workspace_id,r.source_id)=(v.workspace_id,v.id)
 JOIN platform.deep_dub_stages s ON (s.workspace_id,s.run_id)=(r.workspace_id,r.id) WHERE s.id=attempt.stage_id;
 IF NOT FOUND THEN RAISE EXCEPTION '当前轮次尚无绑定来源' USING ERRCODE='PT409'; END IF;
 PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_original,'audio/%',source.duration_ms);
 PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_processed,'audio/%',source.duration_ms);
 IF p_removed IS NOT NULL THEN
  IF p_removed IN (p_original,p_processed) THEN RAISE EXCEPTION '诊断移除轨不能兼作实际背景' USING ERRCODE='23514'; END IF;
  PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_removed,'audio/%',source.duration_ms);
 END IF;
 PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_evidence,'application/json',NULL);
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_attempt,source.id,p_original,p_processed,p_removed,p_evidence,p_model,p_parameters));
 SELECT * INTO result FROM platform.deep_dub_backgrounds WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '背景幂等内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 INSERT INTO platform.deep_dub_backgrounds(workspace_id,project_id,source_id,revision,original_artifact_id,processed_artifact_id,removed_artifact_id,
 model_id,parameters,input_hash,created_by,stage_attempt_id,evidence_artifact_id,request_key,request_hash)
 SELECT p_workspace,attempt.project_id,source.id,coalesce(max(revision),0)+1,p_original,p_processed,p_removed,p_model,p_parameters,fingerprint,
 auth.uid(),p_attempt,p_evidence,p_request_key,fingerprint FROM platform.deep_dub_backgrounds WHERE workspace_id=p_workspace AND source_id=source.id RETURNING * INTO result;
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION platform.deep_dub_register_render(
 p_workspace uuid,p_attempt uuid,p_timeline uuid,p_background uuid,p_strategy text,p_parameters jsonb,
 p_dialogue uuid,p_mix uuid,p_subtitle uuid,p_video uuid,p_evidence uuid,p_request_key text
) RETURNS platform.deep_dub_renders LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; run platform.deep_dub_runs; timeline platform.deep_dub_timeline_revisions;
 source platform.deep_dub_sources; profile platform.deep_dub_quality_profiles; background platform.deep_dub_backgrounds;
 subtitle platform.deep_dub_artifacts; result platform.deep_dub_renders; fingerprint text; inputs text;
BEGIN
 attempt:=platform.deep_dub_candidate_attempt(p_workspace,p_attempt);
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id AND required_device_kind IS NOT NULL) THEN
  RAISE EXCEPTION '整片渲染必须声明实际设备租约' USING ERRCODE='23514'; END IF;
 IF p_strategy IS NULL OR length(btrim(p_strategy)) NOT BETWEEN 1 AND 200 OR p_parameters IS NULL OR jsonb_typeof(p_parameters)<>'object'
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION '成片候选参数不合法' USING ERRCODE='22023'; END IF;
 SELECT r.* INTO run FROM platform.deep_dub_runs r JOIN platform.deep_dub_stages s ON (s.workspace_id,s.run_id)=(r.workspace_id,r.id) WHERE s.id=attempt.stage_id;
 SELECT * INTO timeline FROM platform.deep_dub_timeline_revisions WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_timeline;
 IF NOT FOUND OR NOT timeline.sealed OR timeline.source_id IS DISTINCT FROM run.source_id OR timeline.quality_profile_id<>run.quality_profile_id THEN
  RAISE EXCEPTION '成片必须使用本轮来源与质量规则的封存时间线' USING ERRCODE='23514'; END IF;
 SELECT * INTO source FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND id=run.source_id;
 SELECT * INTO profile FROM platform.deep_dub_quality_profiles WHERE workspace_id=p_workspace AND id=run.quality_profile_id;
 IF p_background IS NOT NULL THEN
  SELECT * INTO background FROM platform.deep_dub_backgrounds WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_background;
  IF NOT FOUND OR background.source_id<>run.source_id THEN RAISE EXCEPTION '背景不属于本轮来源' USING ERRCODE='23503'; END IF;
  IF background.removed_artifact_id IN (p_dialogue,p_mix) THEN RAISE EXCEPTION '诊断移除轨不能加入成片' USING ERRCODE='23514'; END IF;
 ELSIF profile.rules->>'background_mode'='preserve' THEN RAISE EXCEPTION '当前档位要求保留背景版本' USING ERRCODE='23514';
 END IF;
 PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_dialogue,'audio/%',source.duration_ms);
 PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_mix,'audio/%',source.duration_ms);
 PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_video,'video/%',source.duration_ms);
 subtitle:=platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_subtitle,'%',NULL);
 IF subtitle.media_type NOT IN ('text/vtt','application/x-subrip','text/plain') THEN RAISE EXCEPTION '字幕产物类型不合法' USING ERRCODE='23514'; END IF;
 PERFORM platform.deep_dub_require_media_artifact(p_workspace,p_attempt,p_evidence,'application/json',NULL);
 inputs:=platform.deep_dub_digest(jsonb_build_array(run.input_hash,timeline.input_hash,background.input_hash,p_strategy,p_parameters));
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_attempt,p_timeline,p_background,inputs,p_dialogue,p_mix,p_subtitle,p_video,p_evidence));
 SELECT * INTO result FROM platform.deep_dub_renders WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '成片幂等内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 INSERT INTO platform.deep_dub_renders(workspace_id,project_id,run_id,source_id,timeline_id,quality_profile_id,background_id,revision,strategy_key,parameters,
 input_hash,dialogue_artifact_id,mix_artifact_id,subtitle_artifact_id,video_artifact_id,created_by,stage_attempt_id,evidence_artifact_id,request_key,request_hash)
 SELECT p_workspace,attempt.project_id,run.id,source.id,p_timeline,run.quality_profile_id,p_background,coalesce(max(revision),0)+1,p_strategy,p_parameters,
 inputs,p_dialogue,p_mix,p_subtitle,p_video,auth.uid(),p_attempt,p_evidence,p_request_key,fingerprint FROM platform.deep_dub_renders WHERE workspace_id=p_workspace AND run_id=run.id RETURNING * INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_register_background(uuid,uuid,uuid,uuid,uuid,uuid,text,jsonb,text),
 platform.deep_dub_register_render(uuid,uuid,uuid,uuid,text,jsonb,uuid,uuid,uuid,uuid,uuid,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_register_background(uuid,uuid,uuid,uuid,uuid,uuid,text,jsonb,text),
 platform.deep_dub_register_render(uuid,uuid,uuid,uuid,text,jsonb,uuid,uuid,uuid,uuid,uuid,text) TO authenticated;
NOTIFY pgrst,'reload schema';
