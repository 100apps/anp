-- 参考与音频候选；实际生成/PCM 变速阶段必须声明设备并持有效租约。
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['deep_dub_voice_references','deep_dub_emotion_references','deep_dub_audio_attempts'] LOOP
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS stage_attempt_id uuid',name);
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS request_key text',name);
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS request_hash text',name);
  IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=('platform.'||name)::regclass AND conname=name||'_producer') THEN
   EXECUTE format('ALTER TABLE platform.%I ADD CONSTRAINT %I FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id)',name,name||'_producer');
  END IF;
  EXECUTE format('CREATE UNIQUE INDEX IF NOT EXISTS %I ON platform.%I(workspace_id,project_id,request_key)',name||'_request',name);
 END LOOP;
END $$;
ALTER TABLE platform.deep_dub_emotion_references ADD COLUMN IF NOT EXISTS original_artifact_id uuid;
ALTER TABLE platform.deep_dub_audio_attempts ADD COLUMN IF NOT EXISTS evidence_artifact_id uuid;
ALTER TABLE platform.deep_dub_audio_attempts ADD COLUMN IF NOT EXISTS pcm_total_factor numeric NOT NULL DEFAULT 1 CHECK(pcm_total_factor BETWEEN 0.90 AND 1.10);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='platform.deep_dub_emotion_references'::regclass AND conname='deep_dub_emotion_original') THEN
  ALTER TABLE platform.deep_dub_emotion_references ADD CONSTRAINT deep_dub_emotion_original FOREIGN KEY(workspace_id,project_id,original_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id);
  ALTER TABLE platform.deep_dub_audio_attempts ADD CONSTRAINT deep_dub_audio_evidence FOREIGN KEY(workspace_id,project_id,evidence_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id);
 END IF;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_register_reference(
 p_workspace uuid,p_attempt uuid,p_kind text,p_source uuid,p_subject uuid,p_start_ms bigint,p_end_ms bigint,
 p_original uuid,p_processed uuid,p_processing jsonb,p_request_key text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; source platform.deep_dub_sources; segment platform.deep_dub_segments;
 original platform.deep_dub_artifacts; processed platform.deep_dub_artifacts; fingerprint text; result jsonb;
BEGIN
 attempt:=platform.deep_dub_candidate_attempt(p_workspace,p_attempt);
 IF p_kind IS NULL OR p_kind NOT IN ('voice','emotion') OR p_start_ms IS NULL OR p_end_ms IS NULL OR p_start_ms<0 OR p_end_ms<=p_start_ms
 OR p_processing IS NULL OR jsonb_typeof(p_processing)<>'object' OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION '参考候选输入不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO source FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_source;
 IF NOT FOUND OR p_end_ms>source.duration_ms OR NOT EXISTS(SELECT 1 FROM platform.deep_dub_runs r JOIN platform.deep_dub_stages s
 ON (s.workspace_id,s.run_id)=(r.workspace_id,r.id) WHERE s.id=attempt.stage_id AND r.source_id=p_source) THEN
  RAISE EXCEPTION '参考来源或窗口不属于当前轮次' USING ERRCODE='23514'; END IF;
 IF p_kind='voice' THEN
  IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_cues WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND source_id=p_source AND speaker_id=p_subject) THEN
   RAISE EXCEPTION '来源中不存在该角色' USING ERRCODE='23503'; END IF;
 ELSE
  SELECT * INTO segment FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_subject;
  IF NOT FOUND OR segment.source_id<>p_source OR segment.start_ms<>p_start_ms OR segment.end_ms<>p_end_ms THEN
   RAISE EXCEPTION '情绪参考必须保留当前段的准确原声窗口' USING ERRCODE='23514'; END IF;
 END IF;
 SELECT * INTO original FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_original;
 SELECT * INTO processed FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_processed;
 IF original.id IS NULL OR processed.id IS NULL OR original.stage_attempt_id IS DISTINCT FROM p_attempt OR processed.stage_attempt_id IS DISTINCT FROM p_attempt
 OR original.media_type NOT LIKE 'audio/%' OR processed.media_type NOT LIKE 'audio/%' OR original.duration_ms IS NULL OR processed.duration_ms IS NULL
 OR processed.duration_ms<=0 OR abs(original.duration_ms-(p_end_ms-p_start_ms))>50 THEN
  RAISE EXCEPTION '参考必须为本尝试音频，原版时长匹配原声窗口' USING ERRCODE='23514'; END IF;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_attempt,p_kind,p_source,p_subject,p_start_ms,p_end_ms,p_original,p_processed,p_processing));
 IF p_kind='voice' THEN
  SELECT to_jsonb(v) INTO result FROM platform.deep_dub_voice_references v WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 ELSE
  SELECT to_jsonb(v) INTO result FROM platform.deep_dub_emotion_references v WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 END IF;
 IF result IS NOT NULL THEN
  IF result->>'request_hash'<>fingerprint THEN RAISE EXCEPTION '参考幂等内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 IF p_kind='voice' THEN
  INSERT INTO platform.deep_dub_voice_references(workspace_id,project_id,speaker_id,source_id,revision,start_ms,end_ms,
   original_artifact_id,processed_artifact_id,processing,input_hash,created_by,stage_attempt_id,request_key,request_hash)
  SELECT p_workspace,attempt.project_id,p_subject,p_source,coalesce(max(revision),0)+1,p_start_ms,p_end_ms,p_original,p_processed,p_processing,fingerprint,
   auth.uid(),p_attempt,p_request_key,fingerprint FROM platform.deep_dub_voice_references WHERE workspace_id=p_workspace AND speaker_id=p_subject
  RETURNING to_jsonb(deep_dub_voice_references) INTO result;
 ELSE
  INSERT INTO platform.deep_dub_emotion_references(workspace_id,project_id,source_id,segment_id,revision,start_ms,end_ms,
   original_artifact_id,artifact_id,processing,input_hash,created_by,stage_attempt_id,request_key,request_hash)
  SELECT p_workspace,attempt.project_id,p_source,p_subject,coalesce(max(revision),0)+1,p_start_ms,p_end_ms,p_original,p_processed,p_processing,fingerprint,
   auth.uid(),p_attempt,p_request_key,fingerprint FROM platform.deep_dub_emotion_references WHERE workspace_id=p_workspace AND segment_id=p_subject
  RETURNING to_jsonb(deep_dub_emotion_references) INTO result;
 END IF;
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION platform.deep_dub_register_audio(
 p_workspace uuid,p_attempt uuid,p_segment uuid,p_translation uuid,p_voice uuid,p_emotion uuid,p_artifact uuid,p_evidence uuid,
 p_kind text,p_parent uuid,p_generation_factor numeric,p_pcm_tempo_factor numeric,p_parameters jsonb,p_request_key text
) RETURNS platform.deep_dub_audio_attempts LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; segment platform.deep_dub_segments; artifact platform.deep_dub_artifacts;
 parent platform.deep_dub_audio_attempts; result platform.deep_dub_audio_attempts; fingerprint text; total_factor numeric:=1;
BEGIN
 attempt:=platform.deep_dub_candidate_attempt(p_workspace,p_attempt);
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=attempt.stage_id AND required_device_kind IS NOT NULL) THEN
  RAISE EXCEPTION '生成或变速必须声明实际设备租约' USING ERRCODE='23514'; END IF;
 IF p_parameters IS NULL OR jsonb_typeof(p_parameters)<>'object' OR p_parameters='{}'::jsonb
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 OR p_kind IS NULL OR p_kind NOT IN ('generation','pcm_tempo')
 OR p_generation_factor IS NULL OR NOT(p_generation_factor BETWEEN 0.90 AND 1.35)
 OR p_pcm_tempo_factor IS NULL OR NOT(p_pcm_tempo_factor BETWEEN 0.90 AND 1.10) THEN
  RAISE EXCEPTION '音频候选参数不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO segment FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_segment;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM platform.deep_dub_runs r JOIN platform.deep_dub_stages s ON (s.workspace_id,s.run_id)=(r.workspace_id,r.id)
 WHERE s.id=attempt.stage_id AND r.source_id=segment.source_id) THEN RAISE EXCEPTION '音频段不属于当前轮次' USING ERRCODE='23503'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_voice_references WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_voice AND source_id=segment.source_id) THEN
  RAISE EXCEPTION '音色参考必须来自本轮原片' USING ERRCODE='23503'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_evidence
 AND stage_attempt_id=p_attempt AND media_type='application/json') THEN RAISE EXCEPTION '缺少本尝试的提供方/处理回执JSON' USING ERRCODE='23514'; END IF;
 SELECT * INTO artifact FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_artifact;
 IF NOT FOUND OR artifact.stage_attempt_id IS DISTINCT FROM p_attempt OR artifact.media_type NOT LIKE 'audio/%' OR coalesce(artifact.duration_ms,0)<=0 THEN
  RAISE EXCEPTION '音频必须为本尝试的有效时长产物' USING ERRCODE='23514'; END IF;
 IF p_kind='generation' THEN
  IF p_parent IS NOT NULL OR p_pcm_tempo_factor<>1 OR jsonb_typeof(p_parameters->'model_id') IS DISTINCT FROM 'string'
  OR length(btrim(p_parameters->>'model_id')) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION '生成必须记录模型且不能伪装为PCM派生' USING ERRCODE='23514'; END IF;
 ELSE
  SELECT * INTO parent FROM platform.deep_dub_audio_attempts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_parent;
  IF NOT FOUND OR parent.segment_id<>p_segment OR parent.translation_id<>p_translation OR parent.voice_reference_id<>p_voice
  OR parent.emotion_reference_id<>p_emotion OR parent.generation_factor<>p_generation_factor THEN
   RAISE EXCEPTION 'PCM派生不能换文本/参考或改写生成倍率' USING ERRCODE='23514'; END IF;
  total_factor:=parent.pcm_total_factor*p_pcm_tempo_factor;
  IF NOT(total_factor BETWEEN 0.90 AND 1.10) OR abs(artifact.duration_ms-parent.duration_ms/p_pcm_tempo_factor)>greatest(20,parent.duration_ms*0.005) THEN
   RAISE EXCEPTION '累计PCM变速或派生时长超限' USING ERRCODE='23514'; END IF;
 END IF;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_attempt,p_segment,p_translation,p_voice,p_emotion,p_artifact,p_evidence,p_kind,p_parent,
 trim_scale(p_generation_factor),trim_scale(p_pcm_tempo_factor),p_parameters));
 SELECT * INTO result FROM platform.deep_dub_audio_attempts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '音频幂等内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 INSERT INTO platform.deep_dub_audio_attempts(workspace_id,project_id,segment_id,speaker_id,translation_id,voice_reference_id,emotion_reference_id,
 stage_attempt_id,kind,parent_id,generation_factor,pcm_tempo_factor,pcm_total_factor,parameters,artifact_id,duration_ms,input_hash,created_by,evidence_artifact_id,request_key,request_hash)
 VALUES(p_workspace,attempt.project_id,p_segment,segment.speaker_id,p_translation,p_voice,p_emotion,p_attempt,p_kind,p_parent,p_generation_factor,
 p_pcm_tempo_factor,total_factor,p_parameters,p_artifact,artifact.duration_ms,fingerprint,auth.uid(),p_evidence,p_request_key,fingerprint) RETURNING * INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_register_reference(uuid,uuid,text,uuid,uuid,bigint,bigint,uuid,uuid,jsonb,text),
 platform.deep_dub_register_audio(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,numeric,numeric,jsonb,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_register_reference(uuid,uuid,text,uuid,uuid,bigint,bigint,uuid,uuid,jsonb,text),
 platform.deep_dub_register_audio(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,uuid,numeric,numeric,jsonb,text) TO authenticated;
NOTIFY pgrst,'reload schema';
