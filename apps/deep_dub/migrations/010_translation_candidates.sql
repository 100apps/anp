ALTER TABLE platform.deep_dub_translations ADD COLUMN IF NOT EXISTS stage_attempt_id uuid;
ALTER TABLE platform.deep_dub_translations ADD COLUMN IF NOT EXISTS context_artifact_id uuid;
ALTER TABLE platform.deep_dub_translations ADD COLUMN IF NOT EXISTS request_key text;
ALTER TABLE platform.deep_dub_translations ADD COLUMN IF NOT EXISTS request_hash text;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='platform.deep_dub_translations'::regclass AND conname='deep_dub_translation_producer') THEN
  ALTER TABLE platform.deep_dub_translations ADD CONSTRAINT deep_dub_translation_producer
   FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id);
  ALTER TABLE platform.deep_dub_translations ADD CONSTRAINT deep_dub_translation_context
   FOREIGN KEY(workspace_id,project_id,context_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id);
 END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS deep_dub_translation_request ON platform.deep_dub_translations(workspace_id,project_id,request_key);
CREATE OR REPLACE FUNCTION platform.deep_dub_register_translation(
 p_workspace uuid,p_attempt uuid,p_segment uuid,p_context uuid,p_chinese text,p_model text,p_prompt text,p_request_key text
) RETURNS platform.deep_dub_translations LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; segment platform.deep_dub_segments; context platform.deep_dub_artifacts;
 result platform.deep_dub_translations; fingerprint text; inputs text;
BEGIN
 attempt:=platform.deep_dub_candidate_attempt(p_workspace,p_attempt);
 IF p_chinese IS NULL OR length(btrim(p_chinese)) NOT BETWEEN 1 AND 100000
 OR p_model IS NULL OR length(p_model) NOT BETWEEN 1 AND 200 OR p_prompt IS NULL OR length(p_prompt) NOT BETWEEN 1 AND 200
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION '译文候选输入不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO segment FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_segment;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM platform.deep_dub_runs r JOIN platform.deep_dub_stages s ON (s.workspace_id,s.run_id)=(r.workspace_id,r.id)
 WHERE s.id=attempt.stage_id AND r.source_id=segment.source_id) THEN RAISE EXCEPTION '段不属于当前轮次来源' USING ERRCODE='23503'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_segment_sets WHERE workspace_id=p_workspace AND id=segment.segment_set_id AND sealed) THEN
  RAISE EXCEPTION '分段尚未封存' USING ERRCODE='23514'; END IF;
 SELECT * INTO context FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_context;
 IF NOT FOUND OR context.stage_attempt_id IS DISTINCT FROM p_attempt OR context.media_type<>'application/json' THEN
  RAISE EXCEPTION '上下文必须为本尝试的私有JSON产物' USING ERRCODE='23514'; END IF;
 inputs:=platform.deep_dub_digest(jsonb_build_array(p_segment,segment.source_text,context.sha256,p_model,p_prompt));
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_attempt,inputs,p_context,p_chinese));
 SELECT * INTO result FROM platform.deep_dub_translations WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '译文幂等内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 INSERT INTO platform.deep_dub_translations(workspace_id,project_id,segment_id,revision,chinese_text,model_id,prompt_revision,input_hash,
 created_by,stage_attempt_id,context_artifact_id,request_key,request_hash)
 SELECT p_workspace,attempt.project_id,p_segment,coalesce(max(revision),0)+1,p_chinese,p_model,p_prompt,inputs,
 auth.uid(),p_attempt,p_context,p_request_key,fingerprint FROM platform.deep_dub_translations WHERE workspace_id=p_workspace AND segment_id=p_segment RETURNING * INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_register_translation(uuid,uuid,uuid,uuid,text,text,text,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_register_translation(uuid,uuid,uuid,uuid,text,text,text,text) TO authenticated;
NOTIFY pgrst,'reload schema';
