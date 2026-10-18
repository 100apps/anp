-- 来源与原文分段的不可变登记。来源身份/原文可信性仍由独立审核决定。
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['deep_dub_sources','deep_dub_segment_sets'] LOOP
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS stage_attempt_id uuid',name);
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS request_key text',name);
  EXECUTE format('ALTER TABLE platform.%I ADD COLUMN IF NOT EXISTS request_hash text',name);
  IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=('platform.'||name)::regclass AND conname=name||'_producer') THEN
   EXECUTE format('ALTER TABLE platform.%I ADD CONSTRAINT %I FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id)',name,name||'_producer');
  END IF;
  EXECUTE format('CREATE UNIQUE INDEX IF NOT EXISTS %I ON platform.%I(workspace_id,project_id,request_key)',name||'_request',name);
 END LOOP;
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
 IF result.status<>'running' THEN RAISE EXCEPTION '候选只能在实际执行阶段登记' USING ERRCODE='PT409'; END IF;
 SELECT required_device_kind INTO kind FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND id=result.stage_id;
 IF kind IS NOT NULL AND NOT EXISTS(SELECT 1 FROM platform.deep_dub_device_leases WHERE workspace_id=p_workspace
 AND stage_attempt_id=p_attempt AND status='active' AND expires_at>clock_timestamp()) THEN
  RAISE EXCEPTION '设备租约无效，拒绝候选提交' USING ERRCODE='PT409'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_candidate_attempt(uuid,uuid) FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION platform.deep_dub_register_source(
 p_workspace uuid,p_attempt uuid,p_artifact uuid,p_duration_ms bigint,p_identity jsonb,p_request_key text
) RETURNS platform.deep_dub_sources LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; artifact platform.deep_dub_artifacts; run platform.deep_dub_runs;
 result platform.deep_dub_sources; fingerprint text; snapshot_url text;
BEGIN
 attempt:=platform.deep_dub_candidate_attempt(p_workspace,p_attempt);
 IF p_duration_ms IS NULL OR p_duration_ms<=0 OR p_identity IS NULL OR jsonb_typeof(p_identity)<>'object'
 OR p_identity='{}'::jsonb OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION '来源输入不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=(SELECT run_id FROM platform.deep_dub_stages WHERE id=attempt.stage_id);
 SELECT input->>'source_url' INTO snapshot_url FROM platform.tasks WHERE workspace_id=p_workspace AND id=run.task_id;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_attempt,p_artifact,p_duration_ms,p_identity,snapshot_url));
 SELECT * INTO result FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '来源幂等内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 IF run.source_id IS NOT NULL THEN RAISE EXCEPTION '本轮来源已绑定，不能换源' USING ERRCODE='PT409'; END IF;
 SELECT * INTO artifact FROM platform.deep_dub_artifacts WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_artifact;
 IF NOT FOUND OR artifact.stage_attempt_id IS DISTINCT FROM p_attempt OR artifact.media_type NOT LIKE 'video/%'
 OR artifact.duration_ms IS DISTINCT FROM p_duration_ms THEN RAISE EXCEPTION '原片必须由本尝试产生且时长一致' USING ERRCODE='23514'; END IF;
 INSERT INTO platform.deep_dub_sources(workspace_id,project_id,revision,source_url,artifact_id,duration_ms,identity_evidence,created_by,stage_attempt_id,request_key,request_hash)
 SELECT p_workspace,attempt.project_id,coalesce(max(revision),0)+1,snapshot_url,p_artifact,p_duration_ms,p_identity,auth.uid(),p_attempt,p_request_key,fingerprint
 FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND project_id=attempt.project_id RETURNING * INTO result;
 UPDATE platform.deep_dub_runs SET source_id=result.id WHERE id=run.id;
 RETURN result;
END $$;

CREATE OR REPLACE FUNCTION platform.deep_dub_register_transcript(
 p_workspace uuid,p_attempt uuid,p_source uuid,p_strategy text,p_cues jsonb,p_segments jsonb,p_request_key text
) RETURNS platform.deep_dub_segment_sets LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE attempt platform.deep_dub_stage_attempts; result platform.deep_dub_segment_sets; fingerprint text; source platform.deep_dub_sources;
 cue jsonb; segment jsonb; speaker uuid; existing platform.deep_dub_cues; segment_id uuid; cue_id uuid;
 source_text text; start_ms bigint; end_ms bigint; numbers integer[]; position integer; number integer; cue_number integer;
BEGIN
 attempt:=platform.deep_dub_candidate_attempt(p_workspace,p_attempt);
 IF p_strategy IS NULL OR length(p_strategy) NOT BETWEEN 1 AND 200 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200
 OR p_cues IS NULL OR jsonb_typeof(p_cues)<>'array' OR p_segments IS NULL OR jsonb_typeof(p_segments)<>'array' THEN
  RAISE EXCEPTION '原文与分段输入不合法' USING ERRCODE='22023'; END IF;
 IF jsonb_array_length(p_cues) NOT BETWEEN 1 AND 100000 OR jsonb_array_length(p_segments) NOT BETWEEN 1 AND 10000 THEN
  RAISE EXCEPTION '原文或分段数量超限' USING ERRCODE='22023'; END IF;
 SELECT * INTO source FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND id=p_source;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM platform.deep_dub_runs r JOIN platform.deep_dub_stages s ON (s.workspace_id,s.run_id)=(r.workspace_id,r.id)
 WHERE s.id=attempt.stage_id AND r.source_id=p_source) THEN RAISE EXCEPTION '原文不属于当前轮次来源' USING ERRCODE='23503'; END IF;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_attempt,p_source,p_strategy,p_cues,p_segments));
 SELECT * INTO result FROM platform.deep_dub_segment_sets WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '原文幂等内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 -- 字幕不在客户端先合并成单段；保留每个 cue 和所属角色，原文字段由可信 cue 组合派生。
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_cues) c WHERE jsonb_typeof(c)<>'object'
 OR c-ARRAY['cue_no','speaker_key','source_text','start_ms','end_ms']<>'{}'::jsonb
 OR NOT(c ?& ARRAY['cue_no','speaker_key','source_text','start_ms','end_ms'])
 OR jsonb_typeof(c->'cue_no')<>'number' OR (c->>'cue_no') !~ '^[0-9]+$'
 OR jsonb_typeof(c->'start_ms')<>'number' OR (c->>'start_ms') !~ '^[0-9]+$'
 OR jsonb_typeof(c->'end_ms')<>'number' OR (c->>'end_ms') !~ '^[0-9]+$'
 OR jsonb_typeof(c->'source_text')<>'string' OR length(btrim(c->>'source_text')) NOT BETWEEN 1 AND 100000
 OR jsonb_typeof(c->'speaker_key')<>'string' OR length(btrim(c->>'speaker_key')) NOT BETWEEN 1 AND 200) THEN
  RAISE EXCEPTION 'cue字段不合法' USING ERRCODE='22023'; END IF;
 IF (SELECT count(DISTINCT c->>'cue_no') FROM jsonb_array_elements(p_cues) c)<>jsonb_array_length(p_cues) THEN
  RAISE EXCEPTION 'cue序号重复' USING ERRCODE='23514'; END IF;
 FOR cue IN SELECT value FROM jsonb_array_elements(p_cues) LOOP
  INSERT INTO platform.deep_dub_speakers(workspace_id,project_id,speaker_key,display_name,created_by)
  VALUES(p_workspace,attempt.project_id,cue->>'speaker_key',cue->>'speaker_key',auth.uid()) ON CONFLICT(workspace_id,project_id,speaker_key) DO NOTHING;
  SELECT id INTO speaker FROM platform.deep_dub_speakers WHERE workspace_id=p_workspace AND project_id=attempt.project_id AND speaker_key=cue->>'speaker_key';
  INSERT INTO platform.deep_dub_cues(workspace_id,project_id,source_id,speaker_id,cue_no,source_text,start_ms,end_ms,created_by)
  VALUES(p_workspace,attempt.project_id,p_source,speaker,(cue->>'cue_no')::integer,cue->>'source_text',(cue->>'start_ms')::bigint,(cue->>'end_ms')::bigint,auth.uid())
  ON CONFLICT(workspace_id,source_id,cue_no) DO NOTHING;
  SELECT * INTO existing FROM platform.deep_dub_cues WHERE workspace_id=p_workspace AND source_id=p_source AND cue_no=(cue->>'cue_no')::integer;
  IF existing.speaker_id<>speaker OR existing.source_text<>cue->>'source_text' OR existing.start_ms<>(cue->>'start_ms')::bigint OR existing.end_ms<>(cue->>'end_ms')::bigint THEN
   RAISE EXCEPTION '来源cue不可覆盖，请建立新来源修订' USING ERRCODE='23505'; END IF;
 END LOOP;
 IF (SELECT count(*) FROM platform.deep_dub_cues WHERE workspace_id=p_workspace AND source_id=p_source)<>jsonb_array_length(p_cues) THEN
  RAISE EXCEPTION '同一来源修订不能遗漏已有cue' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_segments) s WHERE jsonb_typeof(s)<>'object' OR s-ARRAY['cue_numbers']<>'{}'::jsonb
 OR NOT(s ? 'cue_numbers') OR jsonb_typeof(s->'cue_numbers')<>'array') THEN RAISE EXCEPTION '分段字段不合法' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_segments) s WHERE jsonb_array_length(s->'cue_numbers')=0)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_segments) s CROSS JOIN LATERAL jsonb_array_elements(s->'cue_numbers') n
 WHERE jsonb_typeof(n)<>'number' OR n::text !~ '^[0-9]+$') THEN RAISE EXCEPTION '分段cue序号不合法' USING ERRCODE='22023'; END IF;
 IF (SELECT count(*) FROM jsonb_array_elements(p_segments) s CROSS JOIN LATERAL jsonb_array_elements(s->'cue_numbers') n)<>jsonb_array_length(p_cues)
 OR (SELECT count(DISTINCT n::text) FROM jsonb_array_elements(p_segments) s CROSS JOIN LATERAL jsonb_array_elements(s->'cue_numbers') n)<>jsonb_array_length(p_cues)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_segments) s CROSS JOIN LATERAL jsonb_array_elements(s->'cue_numbers') n
 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_cues) c WHERE c->'cue_no'=n)) THEN
  RAISE EXCEPTION '分段必须恰好覆盖全部本次cue，不能遗漏或重复' USING ERRCODE='23514'; END IF;
 INSERT INTO platform.deep_dub_segment_sets(workspace_id,project_id,source_id,revision,strategy_revision,input_hash,created_by,stage_attempt_id,request_key,request_hash)
 SELECT p_workspace,attempt.project_id,p_source,coalesce(max(revision),0)+1,p_strategy,fingerprint,auth.uid(),p_attempt,p_request_key,fingerprint
 FROM platform.deep_dub_segment_sets WHERE workspace_id=p_workspace AND project_id=attempt.project_id RETURNING * INTO result;
 number:=0;
 FOR segment IN SELECT value FROM jsonb_array_elements(p_segments) LOOP
  SELECT array_agg(value::text::integer ORDER BY ordinality) INTO numbers FROM jsonb_array_elements(segment->'cue_numbers') WITH ORDINALITY;
  IF numbers IS DISTINCT FROM (SELECT array_agg(n ORDER BY n) FROM unnest(numbers) n) THEN RAISE EXCEPTION 'cue顺序必须递增' USING ERRCODE='23514'; END IF;
  IF (SELECT count(DISTINCT speaker_id) FROM platform.deep_dub_cues WHERE workspace_id=p_workspace AND source_id=p_source AND cue_no=ANY(numbers))<>1 THEN
   RAISE EXCEPTION '不能把不同角色合并为同一语义段' USING ERRCODE='23514'; END IF;
  SELECT string_agg(c.source_text,' ' ORDER BY array_position(numbers,c.cue_no)),min(c.start_ms),max(c.end_ms) INTO source_text,start_ms,end_ms
  FROM platform.deep_dub_cues c WHERE c.workspace_id=p_workspace AND c.source_id=p_source AND c.cue_no=ANY(numbers);
  SELECT speaker_id INTO speaker FROM platform.deep_dub_cues WHERE workspace_id=p_workspace AND source_id=p_source AND cue_no=numbers[1];
  INSERT INTO platform.deep_dub_segments(workspace_id,project_id,source_id,segment_set_id,speaker_id,segment_no,source_text,start_ms,end_ms,created_by)
  VALUES(p_workspace,attempt.project_id,p_source,result.id,speaker,number,source_text,start_ms,end_ms,auth.uid()) RETURNING id INTO segment_id;
  position:=0;
  FOREACH cue_number IN ARRAY numbers LOOP
   SELECT id INTO cue_id FROM platform.deep_dub_cues WHERE workspace_id=p_workspace AND source_id=p_source AND cue_no=cue_number;
   INSERT INTO platform.deep_dub_segment_cues(workspace_id,project_id,source_id,segment_id,cue_id,position)
   VALUES(p_workspace,attempt.project_id,p_source,segment_id,cue_id,position);position:=position+1;
  END LOOP;
  SELECT count(*)::integer INTO number FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND segment_set_id=result.id;
 END LOOP;
 UPDATE platform.deep_dub_segment_sets SET sealed=true WHERE id=result.id RETURNING * INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_register_source(uuid,uuid,uuid,bigint,jsonb,text),
 platform.deep_dub_register_transcript(uuid,uuid,uuid,text,jsonb,jsonb,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_register_source(uuid,uuid,uuid,bigint,jsonb,text),
 platform.deep_dub_register_transcript(uuid,uuid,uuid,text,jsonb,jsonb,text) TO authenticated;
NOTIFY pgrst,'reload schema';
