-- 规则与审核始终绑定不可变候选；选择和时间线提交使用同一项目乐观锁。
CREATE OR REPLACE FUNCTION platform.deep_dub_subject_hash(p_workspace uuid,p_kind text,p_id uuid)
RETURNS text LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE name text; doc jsonb;
BEGIN
 name:=CASE p_kind WHEN 'translation' THEN 'deep_dub_translations' WHEN 'audio' THEN 'deep_dub_audio_attempts'
 WHEN 'voice_reference' THEN 'deep_dub_voice_references' WHEN 'emotion_reference' THEN 'deep_dub_emotion_references'
 WHEN 'background' THEN 'deep_dub_backgrounds' WHEN 'timeline' THEN 'deep_dub_timeline_revisions'
 WHEN 'render' THEN 'deep_dub_renders' WHEN 'source' THEN 'deep_dub_sources' ELSE NULL END;
 IF name IS NULL THEN RAISE EXCEPTION '未知审核对象类型' USING ERRCODE='22023'; END IF;
 EXECUTE format('SELECT to_jsonb(t)-''created_at'' FROM platform.%I t WHERE workspace_id=$1 AND id=$2',name) INTO doc USING p_workspace,p_id;
 IF doc IS NULL THEN RAISE EXCEPTION '审核对象不存在' USING ERRCODE='PT404'; END IF;
 RETURN platform.deep_dub_digest(doc);
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_subject_hash(uuid,text,uuid) FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION platform.deep_dub_create_quality_profile(
 p_workspace uuid,p_key text,p_revision integer,p_rules jsonb,p_provenance text
) RETURNS platform.deep_dub_quality_profiles
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_quality_profiles;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.project.edit') THEN RAISE EXCEPTION '无权定义质量规则' USING ERRCODE='42501'; END IF;
 IF p_rules IS NULL OR jsonb_typeof(p_rules)<>'object'
 OR jsonb_typeof(p_rules->'translation_required') IS DISTINCT FROM 'array'
 OR jsonb_typeof(p_rules->'audio_required') IS DISTINCT FROM 'array'
 OR NOT (p_rules->'translation_required' @> '["meaning","facts","numbers","negation","names","complete"]'::jsonb)
 OR NOT (p_rules->'audio_required' @> '["content","decode","nonsilent","timing","collision","bounds"]'::jsonb)
 OR jsonb_typeof(p_rules->'max_advance_ms') IS DISTINCT FROM 'number'
 OR jsonb_typeof(p_rules->'collision_tolerance_ms') IS DISTINCT FROM 'number' THEN
  RAISE EXCEPTION '质量规则缺少共同硬门禁' USING ERRCODE='22023'; END IF;
 IF (p_rules->>'max_advance_ms')::numeric NOT BETWEEN 0 AND 500
 OR (p_rules->>'collision_tolerance_ms')::numeric NOT BETWEEN 0 AND 50
 OR mod((p_rules->>'max_advance_ms')::numeric,1)<>0 OR mod((p_rules->>'collision_tolerance_ms')::numeric,1)<>0 THEN
  RAISE EXCEPTION '质量规则不能静默放宽时间边界' USING ERRCODE='22023'; END IF;
 IF NOT coalesce(p_rules->>'background_mode'=ANY(ARRAY['preserve','omit-by-quality-choice']),false)
 OR NOT coalesce(p_rules->>'voice_preference'=ANY(ARRAY['episode-reference','stable-chinese-voice']),false)
 OR (p_key IN ('high','medium') AND p_rules->>'background_mode'<>'preserve')
 OR (p_key='high' AND p_rules->>'voice_preference'<>'episode-reference') THEN
  RAISE EXCEPTION '质量档位与背景或音色策略不匹配' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_workspace::text||':'||p_key,0));
 SELECT * INTO result FROM platform.deep_dub_quality_profiles WHERE workspace_id=p_workspace AND profile_key=p_key AND revision=p_revision;
 IF FOUND THEN
  IF result.rules<>p_rules OR result.provenance<>p_provenance THEN RAISE EXCEPTION '规则修订不能覆盖' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 IF p_revision IS DISTINCT FROM (SELECT coalesce(max(revision),0)+1 FROM platform.deep_dub_quality_profiles WHERE workspace_id=p_workspace AND profile_key=p_key) THEN
  RAISE EXCEPTION '质量规则修订冲突' USING ERRCODE='PT409'; END IF;
 INSERT INTO platform.deep_dub_quality_profiles(workspace_id,profile_key,revision,rules,rules_hash,provenance,created_by)
 VALUES(p_workspace,p_key,p_revision,p_rules,platform.deep_dub_digest(p_rules),p_provenance,auth.uid()) RETURNING * INTO result;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_create_quality_profile(uuid,text,integer,jsonb,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_create_quality_profile(uuid,text,integer,jsonb,text) TO authenticated;

CREATE OR REPLACE FUNCTION platform.deep_dub_record_audit(
 p_workspace uuid,p_project uuid,p_kind text,p_subject uuid,p_profile uuid,p_rule text,p_rule_revision text,
 p_status text,p_method text,p_evidence uuid,p_details jsonb,p_request_key text
) RETURNS platform.deep_dub_audits
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_audits; fingerprint text; request_fingerprint text; target_project uuid;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.audit') THEN RAISE EXCEPTION '无权登记审核' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=p_project FOR KEY SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION '项目不存在' USING ERRCODE='PT404'; END IF;
 fingerprint:=platform.deep_dub_subject_hash(p_workspace,p_kind,p_subject);
 -- 类型分支只选择已声明的外键列，不使用客户端提供的表名。
 IF p_kind='translation' THEN SELECT project_id INTO target_project FROM platform.deep_dub_translations WHERE workspace_id=p_workspace AND id=p_subject;
 ELSIF p_kind='audio' THEN SELECT project_id INTO target_project FROM platform.deep_dub_audio_attempts WHERE workspace_id=p_workspace AND id=p_subject;
 ELSIF p_kind='voice_reference' THEN SELECT project_id INTO target_project FROM platform.deep_dub_voice_references WHERE workspace_id=p_workspace AND id=p_subject;
 ELSIF p_kind='emotion_reference' THEN SELECT project_id INTO target_project FROM platform.deep_dub_emotion_references WHERE workspace_id=p_workspace AND id=p_subject;
 ELSIF p_kind='background' THEN SELECT project_id INTO target_project FROM platform.deep_dub_backgrounds WHERE workspace_id=p_workspace AND id=p_subject;
 ELSIF p_kind='timeline' THEN SELECT project_id INTO target_project FROM platform.deep_dub_timeline_revisions WHERE workspace_id=p_workspace AND id=p_subject;
 ELSIF p_kind='render' THEN SELECT project_id INTO target_project FROM platform.deep_dub_renders WHERE workspace_id=p_workspace AND id=p_subject;
 ELSIF p_kind='source' THEN SELECT project_id INTO target_project FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND id=p_subject;
 END IF;
 IF target_project IS DISTINCT FROM p_project THEN RAISE EXCEPTION '审核对象不属于项目' USING ERRCODE='42501'; END IF;
 IF p_rule IS NULL OR btrim(p_rule)='' OR p_rule_revision IS NULL OR btrim(p_rule_revision)='' THEN
  RAISE EXCEPTION '审核必须带指标及规则版本' USING ERRCODE='22023'; END IF;
 IF p_status='passed' AND p_method='technical' AND (p_kind='translation'
 OR (p_kind='voice_reference' AND p_rule='speaker_identity') OR (p_kind='background' AND p_rule='event_preservation')) THEN
  RAISE EXCEPTION '语义、身份或事件保留通过必须来自人工或独立模型复核' USING ERRCODE='23514'; END IF;
 request_fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_project,p_kind,p_subject,p_profile,p_rule,p_rule_revision,p_status,p_method,p_evidence,p_details,fingerprint));
 INSERT INTO platform.deep_dub_audits(workspace_id,project_id,translation_id,audio_attempt_id,voice_reference_id,
 emotion_reference_id,background_id,timeline_id,render_id,source_id,quality_profile_id,rule_key,rule_revision,
 subject_hash,status,method,evidence_artifact_id,details,request_key,request_hash,created_by)
 VALUES(p_workspace,p_project,CASE WHEN p_kind='translation' THEN p_subject END,CASE WHEN p_kind='audio' THEN p_subject END,
 CASE WHEN p_kind='voice_reference' THEN p_subject END,CASE WHEN p_kind='emotion_reference' THEN p_subject END,
 CASE WHEN p_kind='background' THEN p_subject END,CASE WHEN p_kind='timeline' THEN p_subject END,
 CASE WHEN p_kind='render' THEN p_subject END,CASE WHEN p_kind='source' THEN p_subject END,
 p_profile,p_rule,p_rule_revision,fingerprint,p_status,p_method,p_evidence,p_details,p_request_key,request_fingerprint,auth.uid())
 ON CONFLICT(workspace_id,project_id,request_key) DO NOTHING;
 SELECT * INTO result FROM platform.deep_dub_audits WHERE workspace_id=p_workspace AND project_id=p_project AND request_key=p_request_key;
 IF result.request_hash<>request_fingerprint THEN RAISE EXCEPTION '审核幂等键内容冲突' USING ERRCODE='23505'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_record_audit(uuid,uuid,text,uuid,uuid,text,text,text,text,uuid,jsonb,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_record_audit(uuid,uuid,text,uuid,uuid,text,text,text,text,uuid,jsonb,text) TO authenticated;

CREATE OR REPLACE FUNCTION platform.deep_dub_select_version(
 p_workspace uuid,p_project uuid,p_segment uuid,p_translation uuid,p_audio uuid,p_profile uuid,
 p_audits uuid[],p_expected_version bigint,p_request_key text
) RETURNS platform.deep_dub_selections
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE project platform.deep_dub_projects; segment platform.deep_dub_segments; result platform.deep_dub_selections;
 rules jsonb; canonical_audits jsonb; request_fingerprint text; trans_hash text; audio_hash text; required text; audit_id uuid; latest platform.deep_dub_audits;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.project.edit') THEN RAISE EXCEPTION '无权选版' USING ERRCODE='42501'; END IF;
 IF p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 OR p_audits IS NULL OR cardinality(p_audits) NOT BETWEEN 1 AND 64
 OR EXISTS(SELECT 1 FROM unnest(p_audits) a WHERE a IS NULL) OR cardinality(p_audits)<>(SELECT count(DISTINCT a) FROM unnest(p_audits) a) THEN
  RAISE EXCEPTION '选版请求或审核集合不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO project FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=p_project FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '项目不存在' USING ERRCODE='PT404'; END IF;
 SELECT jsonb_agg(a ORDER BY a) INTO canonical_audits FROM unnest(p_audits) a;
 request_fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_segment,p_translation,p_audio,p_profile,canonical_audits,p_expected_version));
 SELECT * INTO result FROM platform.deep_dub_selections WHERE workspace_id=p_workspace AND project_id=p_project AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>request_fingerprint THEN RAISE EXCEPTION '选版幂等键内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 IF project.row_version IS DISTINCT FROM p_expected_version THEN RAISE EXCEPTION '项目版本冲突，请刷新后重试' USING ERRCODE='PT409'; END IF;
 SELECT * INTO segment FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND project_id=p_project AND id=p_segment;
 IF NOT FOUND THEN RAISE EXCEPTION '分段不属于项目' USING ERRCODE='23503'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_segment_sets WHERE workspace_id=p_workspace AND id=segment.segment_set_id AND sealed) THEN
  RAISE EXCEPTION '分段集尚未封存' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM platform.deep_dub_audio_attempts WHERE workspace_id=p_workspace AND project_id=p_project AND id=p_audio AND segment_id=p_segment AND translation_id=p_translation;
 IF NOT FOUND THEN RAISE EXCEPTION '音频与译文或分段不匹配' USING ERRCODE='23503'; END IF;
 SELECT q.rules INTO rules FROM platform.deep_dub_quality_profiles q WHERE workspace_id=p_workspace AND id=p_profile;
 IF rules IS NULL OR jsonb_typeof(rules->'translation_required') IS DISTINCT FROM 'array'
 OR jsonb_typeof(rules->'audio_required') IS DISTINCT FROM 'array'
 OR NOT coalesce(rules->'translation_required' @> '["meaning","facts","numbers","negation","names","complete"]'::jsonb,false)
 OR NOT coalesce(rules->'audio_required' @> '["content","decode","nonsilent","timing","collision","bounds"]'::jsonb,false) THEN
  RAISE EXCEPTION '质量规则缺少必需门禁' USING ERRCODE='23514'; END IF;
 trans_hash:=platform.deep_dub_subject_hash(p_workspace,'translation',p_translation);
 audio_hash:=platform.deep_dub_subject_hash(p_workspace,'audio',p_audio);
 IF (SELECT count(*) FROM platform.deep_dub_audits a WHERE a.workspace_id=p_workspace AND a.project_id=p_project
     AND a.id=ANY(p_audits) AND a.quality_profile_id=p_profile AND a.status='passed'
     AND ((a.translation_id=p_translation AND a.subject_hash=trans_hash) OR (a.audio_attempt_id=p_audio AND a.subject_hash=audio_hash)))<>cardinality(p_audits) THEN
  RAISE EXCEPTION '审核不是当前候选及规则的通过证据' USING ERRCODE='23514'; END IF;
 FOR required IN SELECT jsonb_array_elements_text(rules->'translation_required') LOOP
  SELECT * INTO latest FROM platform.deep_dub_audits a WHERE a.workspace_id=p_workspace AND a.project_id=p_project
   AND a.translation_id=p_translation AND a.quality_profile_id=p_profile AND a.subject_hash=trans_hash AND a.rule_key=required
   ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
  IF latest.id IS NULL OR latest.status<>'passed' OR NOT(latest.id=ANY(p_audits)) THEN
   RAISE EXCEPTION '译文缺少最新必需通过审核：%',required USING ERRCODE='23514'; END IF;
 END LOOP;
 FOR required IN SELECT jsonb_array_elements_text(rules->'audio_required') LOOP
  SELECT * INTO latest FROM platform.deep_dub_audits a WHERE a.workspace_id=p_workspace AND a.project_id=p_project
   AND a.audio_attempt_id=p_audio AND a.quality_profile_id=p_profile AND a.subject_hash=audio_hash AND a.rule_key=required
   ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
  IF latest.id IS NULL OR latest.status<>'passed' OR NOT(latest.id=ANY(p_audits)) THEN
   RAISE EXCEPTION '音频缺少最新必需通过审核：%',required USING ERRCODE='23514'; END IF;
 END LOOP;
 INSERT INTO platform.deep_dub_selections(workspace_id,project_id,segment_set_id,segment_id,translation_id,audio_attempt_id,
 quality_profile_id,revision,project_version,request_key,request_hash,created_by)
 SELECT p_workspace,p_project,segment.segment_set_id,p_segment,p_translation,p_audio,p_profile,
 coalesce(max(revision),0)+1,project.row_version+1,p_request_key,request_fingerprint,auth.uid()
 FROM platform.deep_dub_selections WHERE workspace_id=p_workspace AND segment_id=p_segment RETURNING * INTO result;
 INSERT INTO platform.deep_dub_selection_audits(workspace_id,project_id,selection_id,audit_id)
 SELECT p_workspace,p_project,result.id,a FROM unnest(p_audits) a;
 UPDATE platform.deep_dub_selections SET sealed=true WHERE id=result.id RETURNING * INTO result;
 UPDATE platform.deep_dub_projects SET row_version=row_version+1 WHERE workspace_id=p_workspace AND id=p_project;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_select_version(uuid,uuid,uuid,uuid,uuid,uuid,uuid[],bigint,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_select_version(uuid,uuid,uuid,uuid,uuid,uuid,uuid[],bigint,text) TO authenticated;

CREATE OR REPLACE FUNCTION platform.deep_dub_assert_selection_current(p_workspace uuid,p_selection uuid)
RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE chosen platform.deep_dub_selections; rules jsonb; required text; kind text; fingerprint text; latest platform.deep_dub_audits;
BEGIN
 SELECT * INTO chosen FROM platform.deep_dub_selections WHERE workspace_id=p_workspace AND id=p_selection;
 IF NOT FOUND THEN RAISE EXCEPTION '选版不存在' USING ERRCODE='PT404'; END IF;
 IF NOT chosen.sealed THEN RAISE EXCEPTION '选版尚未封存' USING ERRCODE='23514'; END IF;
 SELECT q.rules INTO rules FROM platform.deep_dub_quality_profiles q WHERE workspace_id=p_workspace AND id=chosen.quality_profile_id;
 IF rules IS NULL OR jsonb_typeof(rules->'translation_required') IS DISTINCT FROM 'array'
 OR jsonb_typeof(rules->'audio_required') IS DISTINCT FROM 'array'
 OR NOT coalesce(rules->'translation_required' @> '["meaning","facts","numbers","negation","names","complete"]'::jsonb,false)
 OR NOT coalesce(rules->'audio_required' @> '["content","decode","nonsilent","timing","collision","bounds"]'::jsonb,false) THEN
  RAISE EXCEPTION '选版质量规则缺少必需门禁' USING ERRCODE='23514'; END IF;
 FOREACH kind IN ARRAY ARRAY['translation','audio'] LOOP
  fingerprint:=platform.deep_dub_subject_hash(p_workspace,kind,CASE WHEN kind='translation' THEN chosen.translation_id ELSE chosen.audio_attempt_id END);
  FOR required IN SELECT jsonb_array_elements_text(rules->(CASE WHEN kind='translation' THEN 'translation_required' ELSE 'audio_required' END)) LOOP
   SELECT * INTO latest FROM platform.deep_dub_audits a WHERE a.workspace_id=p_workspace AND a.project_id=chosen.project_id
    AND a.quality_profile_id=chosen.quality_profile_id AND a.subject_hash=fingerprint AND a.rule_key=required
    AND ((kind='translation' AND a.translation_id=chosen.translation_id) OR (kind='audio' AND a.audio_attempt_id=chosen.audio_attempt_id))
    ORDER BY a.created_at DESC,a.id DESC LIMIT 1;
   IF latest.id IS NULL OR latest.status<>'passed' OR NOT EXISTS(SELECT 1 FROM platform.deep_dub_selection_audits
    WHERE workspace_id=p_workspace AND selection_id=chosen.id AND audit_id=latest.id) THEN
    RAISE EXCEPTION '选版审核已变化，必须重新审核和选版：%',required USING ERRCODE='23514'; END IF;
  END LOOP;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_assert_selection_current(uuid,uuid) FROM PUBLIC,anon,authenticated;

CREATE OR REPLACE FUNCTION platform.deep_dub_commit_timeline(
 p_workspace uuid,p_project uuid,p_segment_set uuid,p_profile uuid,p_clips jsonb,p_expected_version bigint,p_request_key text
) RETURNS platform.deep_dub_timeline_revisions
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE project platform.deep_dub_projects; result platform.deep_dub_timeline_revisions; canonical jsonb; request_fingerprint text;
 clip record; chosen platform.deep_dub_selections; segment platform.deep_dub_segments; source platform.deep_dub_sources;
 rules jsonb; duration bigint; expected_count integer;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.project.edit') THEN RAISE EXCEPTION '无权修改时间线' USING ERRCODE='42501'; END IF;
 IF p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 OR p_clips IS NULL
 OR jsonb_typeof(p_clips)<>'array' OR jsonb_array_length(p_clips) NOT BETWEEN 1 AND 10000 THEN
  RAISE EXCEPTION '时间线请求不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO project FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=p_project FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '项目不存在' USING ERRCODE='PT404'; END IF;
 SELECT jsonb_agg(jsonb_build_object('segment_id',c.segment_id,'selection_id',c.selection_id,'start_ms',c.start_ms,'gain',trim_scale(c.gain)) ORDER BY c.segment_id)
 INTO canonical FROM jsonb_to_recordset(p_clips) AS c(segment_id uuid,selection_id uuid,start_ms bigint,gain numeric);
 request_fingerprint:=platform.deep_dub_digest(jsonb_build_array(p_segment_set,p_profile,canonical,p_expected_version));
 SELECT * INTO result FROM platform.deep_dub_timeline_revisions WHERE workspace_id=p_workspace AND project_id=p_project AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>request_fingerprint THEN RAISE EXCEPTION '时间线幂等键内容冲突' USING ERRCODE='23505'; END IF;
  RETURN result;
 END IF;
 IF project.row_version IS DISTINCT FROM p_expected_version THEN RAISE EXCEPTION '项目版本冲突，请刷新后重试' USING ERRCODE='PT409'; END IF;
 SELECT s.* INTO source FROM platform.deep_dub_segment_sets x JOIN platform.deep_dub_sources s
 ON (s.workspace_id,s.project_id,s.id)=(x.workspace_id,x.project_id,x.source_id)
 WHERE x.workspace_id=p_workspace AND x.project_id=p_project AND x.id=p_segment_set;
 IF source.id IS NULL THEN RAISE EXCEPTION '分段集不属于项目' USING ERRCODE='23503'; END IF;
 SELECT q.rules INTO rules FROM platform.deep_dub_quality_profiles q WHERE workspace_id=p_workspace AND id=p_profile;
 IF rules IS NULL OR jsonb_typeof(rules->'max_advance_ms') IS DISTINCT FROM 'number' THEN RAISE EXCEPTION '质量规则无效' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO expected_count FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND project_id=p_project AND segment_set_id=p_segment_set;
 IF expected_count<>jsonb_array_length(canonical) OR expected_count<>(SELECT count(DISTINCT c.segment_id) FROM jsonb_to_recordset(canonical) c(segment_id uuid)) THEN
  RAISE EXCEPTION '完整时间线必须恰好覆盖分段集' USING ERRCODE='23514'; END IF;
 FOR clip IN SELECT * FROM jsonb_to_recordset(canonical) AS c(segment_id uuid,selection_id uuid,start_ms bigint,gain numeric) LOOP
  SELECT * INTO segment FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND project_id=p_project AND segment_set_id=p_segment_set AND id=clip.segment_id;
  SELECT * INTO chosen FROM platform.deep_dub_selections WHERE workspace_id=p_workspace AND project_id=p_project AND segment_set_id=p_segment_set AND segment_id=clip.segment_id AND id=clip.selection_id;
  IF segment.id IS NULL OR chosen.id IS NULL OR chosen.quality_profile_id<>p_profile THEN RAISE EXCEPTION '时间线选版归属或规则不一致' USING ERRCODE='23503'; END IF;
  IF EXISTS(SELECT 1 FROM platform.deep_dub_selections WHERE workspace_id=p_workspace AND segment_id=clip.segment_id AND revision>chosen.revision) THEN
   RAISE EXCEPTION '分段已有更新选版，请刷新后提交' USING ERRCODE='PT409'; END IF;
  PERFORM platform.deep_dub_assert_selection_current(p_workspace,chosen.id);
  SELECT duration_ms INTO duration FROM platform.deep_dub_audio_attempts WHERE workspace_id=p_workspace AND id=chosen.audio_attempt_id;
  IF clip.start_ms IS NULL OR clip.start_ms<greatest(0,segment.start_ms-(rules->>'max_advance_ms')::bigint)
  OR clip.start_ms+duration>source.duration_ms OR clip.gain IS NULL OR NOT(clip.gain>0 AND clip.gain<=4) THEN
   RAISE EXCEPTION '配音位置或增益超出规则边界' USING ERRCODE='23514'; END IF;
 END LOOP;
 INSERT INTO platform.deep_dub_timeline_revisions(workspace_id,project_id,source_id,segment_set_id,quality_profile_id,revision,
 input_hash,request_key,request_hash,project_version,created_by)
 SELECT p_workspace,p_project,source.id,p_segment_set,p_profile,coalesce(max(revision),0)+1,
 platform.deep_dub_digest(jsonb_build_array(p_segment_set,p_profile,canonical)),p_request_key,request_fingerprint,project.row_version+1,auth.uid()
 FROM platform.deep_dub_timeline_revisions WHERE workspace_id=p_workspace AND project_id=p_project RETURNING * INTO result;
 INSERT INTO platform.deep_dub_timeline_clips(workspace_id,project_id,timeline_id,segment_set_id,segment_id,selection_id,start_ms,gain)
 SELECT p_workspace,p_project,result.id,p_segment_set,c.segment_id,c.selection_id,c.start_ms,c.gain
 FROM jsonb_to_recordset(canonical) AS c(segment_id uuid,selection_id uuid,start_ms bigint,gain numeric);
 UPDATE platform.deep_dub_timeline_revisions SET sealed=true WHERE id=result.id RETURNING * INTO result;
 UPDATE platform.deep_dub_projects SET row_version=row_version+1 WHERE workspace_id=p_workspace AND id=p_project;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_commit_timeline(uuid,uuid,uuid,uuid,jsonb,bigint,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_commit_timeline(uuid,uuid,uuid,uuid,jsonb,bigint,text) TO authenticated;
NOTIFY pgrst,'reload schema';
