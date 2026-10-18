-- 来源正文覆盖必须来自人工或独立内容复核；样本覆盖/相似度等技术测量不能替代。
-- 不更新或删除历史审核；消费端拒绝历史 technical/passed，且不退回更早的通过记录。
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
 OR (p_kind='source' AND p_rule='transcript_coverage')
 OR (p_kind='voice_reference' AND p_rule='speaker_identity') OR (p_kind='background' AND p_rule='event_preservation')) THEN
  RAISE EXCEPTION '正文、语义、身份或事件保留通过必须来自人工或独立模型复核' USING ERRCODE='23514'; END IF;
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
  IF latest.id IS NULL OR latest.status<>'passed' OR latest.evidence_artifact_id IS NULL
   OR (p_kind='source' AND rule='transcript_coverage' AND latest.method NOT IN ('human','independent_model')) THEN
   RAISE EXCEPTION '缺少当前版本的必需通过审核：%/%',p_kind,rule USING ERRCODE='23514'; END IF;
 END LOOP;
END $$;
