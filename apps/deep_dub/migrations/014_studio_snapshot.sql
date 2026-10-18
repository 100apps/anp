-- 单个 PostgREST 事务快照；普通用户身份执行，所有底表继续受 RLS 约束。
CREATE OR REPLACE FUNCTION platform.deep_dub_studio_snapshot(
 p_workspace uuid,p_project uuid,p_source uuid DEFAULT NULL,p_segment_set uuid DEFAULT NULL,
 p_profile uuid DEFAULT NULL,p_timeline uuid DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,platform AS $$
DECLARE project platform.deep_dub_projects;source platform.deep_dub_sources;parts platform.deep_dub_segment_sets;
 profile platform.deep_dub_quality_profiles;timeline platform.deep_dub_timeline_revisions;
 result jsonb;segments jsonb;translations jsonb;audio jsonb;selected_profile uuid;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.project.read') THEN RAISE EXCEPTION '无权查看此工作空间的工作台' USING ERRCODE='42501'; END IF;
 SELECT * INTO project FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=p_project;
 IF NOT FOUND THEN RAISE EXCEPTION '项目不存在或无权查看' USING ERRCODE='PT404'; END IF;
 SELECT * INTO source FROM platform.deep_dub_sources WHERE workspace_id=p_workspace AND project_id=p_project
  AND (p_source IS NULL OR id=p_source) ORDER BY revision DESC LIMIT 1;
 IF p_source IS NOT NULL AND source.id IS NULL THEN RAISE EXCEPTION '来源不属于此项目' USING ERRCODE='PT404'; END IF;
 SELECT * INTO parts FROM platform.deep_dub_segment_sets WHERE workspace_id=p_workspace AND project_id=p_project AND source_id=source.id AND sealed
  AND (p_segment_set IS NULL OR id=p_segment_set) ORDER BY revision DESC LIMIT 1;
 IF p_segment_set IS NOT NULL AND parts.id IS NULL THEN RAISE EXCEPTION '分段版本未封存或不属于此来源' USING ERRCODE='PT404'; END IF;
 IF p_timeline IS NOT NULL THEN
  SELECT * INTO timeline FROM platform.deep_dub_timeline_revisions WHERE workspace_id=p_workspace AND project_id=p_project
   AND source_id=source.id AND segment_set_id=parts.id AND id=p_timeline AND sealed;
  IF NOT FOUND THEN RAISE EXCEPTION '时间线未封存或不属于所选来源和分段' USING ERRCODE='PT404'; END IF;
  IF p_profile IS NOT NULL AND p_profile<>timeline.quality_profile_id THEN RAISE EXCEPTION '时间线与所选质量档位不一致' USING ERRCODE='PT409'; END IF;
 END IF;
 selected_profile:=coalesce(p_profile,timeline.quality_profile_id,(SELECT quality_profile_id FROM platform.deep_dub_runs
  WHERE workspace_id=p_workspace AND project_id=p_project AND source_id=source.id ORDER BY created_at DESC,id DESC LIMIT 1));
 SELECT * INTO profile FROM platform.deep_dub_quality_profiles WHERE workspace_id=p_workspace AND
  (id=selected_profile OR (selected_profile IS NULL AND profile_key='high')) ORDER BY revision DESC LIMIT 1;
 IF selected_profile IS NOT NULL AND profile.id IS NULL THEN RAISE EXCEPTION '质量档位不存在或无权查看' USING ERRCODE='PT404'; END IF;
 IF timeline.id IS NULL THEN
  SELECT * INTO timeline FROM platform.deep_dub_timeline_revisions WHERE workspace_id=p_workspace AND project_id=p_project
   AND source_id=source.id AND segment_set_id=parts.id AND quality_profile_id=profile.id AND sealed ORDER BY revision DESC LIMIT 1;
 END IF;
 SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.segment_no,s.id),'[]') INTO segments FROM platform.deep_dub_segments s
  WHERE s.workspace_id=p_workspace AND s.project_id=p_project AND s.segment_set_id=parts.id;
 SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.segment_id,t.revision),'[]') INTO translations FROM platform.deep_dub_translations t
  WHERE t.workspace_id=p_workspace AND t.project_id=p_project AND t.segment_id IN
   (SELECT s.id FROM platform.deep_dub_segments s WHERE s.workspace_id=p_workspace AND s.segment_set_id=parts.id);
 SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.segment_id,a.created_at,a.id),'[]') INTO audio FROM platform.deep_dub_audio_attempts a
  WHERE a.workspace_id=p_workspace AND a.project_id=p_project AND a.segment_id IN
   (SELECT s.id FROM platform.deep_dub_segments s WHERE s.workspace_id=p_workspace AND s.segment_set_id=parts.id);
 IF jsonb_array_length(segments)>10000 OR jsonb_array_length(translations)>50000 OR jsonb_array_length(audio)>50000 THEN
  RAISE EXCEPTION '所选快照超过单页上限，请使用记录分页接口；不会截断后冒充完整快照' USING ERRCODE='22023'; END IF;
 result:=jsonb_build_object('schema_version',1,'project',to_jsonb(project),'source',CASE WHEN source.id IS NOT NULL THEN to_jsonb(source) END,
  'segment_set',CASE WHEN parts.id IS NOT NULL THEN to_jsonb(parts) END,'quality_profile',CASE WHEN profile.id IS NOT NULL THEN to_jsonb(profile) END,
  'timeline',CASE WHEN timeline.id IS NOT NULL THEN to_jsonb(timeline) END,'segments',segments,'translations',translations,'audio_attempts',audio,
  'speakers',(SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.speaker_key),'[]') FROM platform.deep_dub_speakers s WHERE s.workspace_id=p_workspace AND s.project_id=p_project),
  'translation_edits',(SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY e.created_at,e.id),'[]') FROM platform.deep_dub_translation_edits e WHERE e.workspace_id=p_workspace AND e.project_id=p_project
   AND e.segment_id IN (SELECT s.id FROM platform.deep_dub_segments s WHERE s.workspace_id=p_workspace AND s.segment_set_id=parts.id)),
  'voice_references',(SELECT coalesce(jsonb_agg(to_jsonb(v) ORDER BY v.speaker_id,v.revision),'[]') FROM platform.deep_dub_voice_references v WHERE v.workspace_id=p_workspace AND v.project_id=p_project AND v.source_id=source.id),
  'emotion_references',(SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY e.segment_id,e.revision),'[]') FROM platform.deep_dub_emotion_references e WHERE e.workspace_id=p_workspace AND e.project_id=p_project AND e.source_id=source.id
   AND e.segment_id IN (SELECT s.id FROM platform.deep_dub_segments s WHERE s.workspace_id=p_workspace AND s.segment_set_id=parts.id)),
  'selections',(SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.segment_id,s.revision),'[]') FROM platform.deep_dub_selections s WHERE s.workspace_id=p_workspace AND s.project_id=p_project AND s.segment_set_id=parts.id AND s.quality_profile_id=profile.id AND s.sealed),
  'timeline_clips',(SELECT coalesce(jsonb_agg(to_jsonb(c) ORDER BY c.start_ms,c.segment_id),'[]') FROM platform.deep_dub_timeline_clips c WHERE c.workspace_id=p_workspace AND c.project_id=p_project AND c.timeline_id=timeline.id),
  'backgrounds',(SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY b.revision),'[]') FROM platform.deep_dub_backgrounds b WHERE b.workspace_id=p_workspace AND b.project_id=p_project AND b.source_id=source.id),
  'renders',(SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY r.revision),'[]') FROM platform.deep_dub_renders r WHERE r.workspace_id=p_workspace AND r.project_id=p_project AND r.timeline_id=timeline.id),
  'audits',(SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.created_at,a.id),'[]') FROM platform.deep_dub_audits a WHERE a.workspace_id=p_workspace AND a.project_id=p_project AND a.quality_profile_id=profile.id),
  'artifacts',(SELECT coalesce(jsonb_agg(to_jsonb(a)-'object_path' ORDER BY a.created_at,a.id),'[]') FROM platform.deep_dub_artifacts a WHERE a.workspace_id=p_workspace AND a.project_id=p_project),
  'runs',(SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY r.created_at,r.id),'[]') FROM platform.deep_dub_runs r WHERE r.workspace_id=p_workspace AND r.project_id=p_project AND r.source_id=source.id));
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_studio_snapshot(uuid,uuid,uuid,uuid,uuid,uuid) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_studio_snapshot(uuid,uuid,uuid,uuid,uuid,uuid) TO authenticated;
NOTIFY pgrst,'reload schema';
