-- 人工编辑不冒充某个执行尝试；独立追加回执，避免改动已有审核对象的字段/摘要。
CREATE TABLE IF NOT EXISTS platform.deep_dub_translation_edits (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 segment_id uuid NOT NULL,base_translation_id uuid,translation_id uuid NOT NULL,
 expected_project_version bigint NOT NULL,project_version bigint NOT NULL,
 request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 200),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(workspace_id,project_id) REFERENCES platform.deep_dub_projects(workspace_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id) REFERENCES platform.deep_dub_segments(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id,base_translation_id) REFERENCES platform.deep_dub_translations(workspace_id,project_id,segment_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id,translation_id) REFERENCES platform.deep_dub_translations(workspace_id,project_id,segment_id,id),
 CHECK(expected_project_version>0 AND project_version=expected_project_version+1),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,request_key),UNIQUE(workspace_id,translation_id)
);
ALTER TABLE platform.deep_dub_translation_edits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON platform.deep_dub_translation_edits FROM PUBLIC,anon,authenticated;
GRANT SELECT ON platform.deep_dub_translation_edits TO authenticated;
GRANT ALL ON platform.deep_dub_translation_edits TO service_role;
DROP POLICY IF EXISTS deep_dub_read ON platform.deep_dub_translation_edits;
CREATE POLICY deep_dub_read ON platform.deep_dub_translation_edits FOR SELECT TO authenticated
 USING(platform.has_permission(workspace_id,'deep_dub.project.read'));
DROP TRIGGER IF EXISTS immutable_revision ON platform.deep_dub_translation_edits;
CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON platform.deep_dub_translation_edits
 FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_immutable_revision();

CREATE OR REPLACE FUNCTION platform.deep_dub_edit_translation(
 p_workspace uuid,p_project uuid,p_segment uuid,p_base uuid,p_expected_revision integer,
 p_expected_project_version bigint,p_chinese text,p_request_key text
) RETURNS platform.deep_dub_translation_edits
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE project platform.deep_dub_projects;segment platform.deep_dub_segments;latest platform.deep_dub_translations;
 result platform.deep_dub_translation_edits;new_id uuid;fingerprint text;inputs text;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.project.read')
 OR NOT platform.has_permission(p_workspace,'deep_dub.project.edit') THEN
  RAISE EXCEPTION '无权编辑此工作空间的译稿' USING ERRCODE='42501'; END IF;
 IF p_chinese IS NULL OR length(btrim(p_chinese)) NOT BETWEEN 1 AND 100000
 OR p_expected_project_version IS NULL OR p_expected_project_version<1
 OR p_expected_revision IS NULL OR p_expected_revision<0
 OR (p_expected_revision=0) IS DISTINCT FROM (p_base IS NULL)
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION '译稿编辑输入不合法' USING ERRCODE='22023'; END IF;
 SELECT * INTO project FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=p_project FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '项目不存在' USING ERRCODE='PT404'; END IF;
 fingerprint:=platform.deep_dub_digest(jsonb_build_array(auth.uid(),p_segment,p_base,p_expected_revision,p_expected_project_version,p_chinese));
 SELECT * INTO result FROM platform.deep_dub_translation_edits WHERE workspace_id=p_workspace AND project_id=p_project AND request_key=p_request_key;
 IF FOUND THEN
  IF result.request_hash<>fingerprint THEN RAISE EXCEPTION '译稿编辑幂等内容冲突' USING ERRCODE='PT409'; END IF;
  RETURN result;
 END IF;
 IF project.row_version<>p_expected_project_version THEN RAISE EXCEPTION '项目版本已更新，请重新读取后保存' USING ERRCODE='PT409'; END IF;
 SELECT * INTO segment FROM platform.deep_dub_segments WHERE workspace_id=p_workspace AND project_id=p_project AND id=p_segment;
 IF NOT FOUND THEN RAISE EXCEPTION '段不属于此项目' USING ERRCODE='PT404'; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.deep_dub_segment_sets WHERE workspace_id=p_workspace AND project_id=p_project AND id=segment.segment_set_id AND sealed) THEN
  RAISE EXCEPTION '原文分段尚未封存，不能保存译稿' USING ERRCODE='23514'; END IF;
 SELECT * INTO latest FROM platform.deep_dub_translations WHERE workspace_id=p_workspace AND project_id=p_project AND segment_id=p_segment ORDER BY revision DESC LIMIT 1;
 IF coalesce(latest.revision,0)<>p_expected_revision OR latest.id IS DISTINCT FROM p_base THEN
  RAISE EXCEPTION '译稿版本已更新，请重新读取后保存' USING ERRCODE='PT409'; END IF;
 inputs:=platform.deep_dub_digest(jsonb_build_array(segment.id,segment.source_text,segment.start_ms,segment.end_ms,p_base,latest.input_hash,p_chinese));
 INSERT INTO platform.deep_dub_translations(workspace_id,project_id,segment_id,revision,chinese_text,model_id,prompt_revision,input_hash,created_by)
 VALUES(p_workspace,p_project,p_segment,p_expected_revision+1,p_chinese,'human:editor','manual-edit-v1',inputs,auth.uid()) RETURNING id INTO new_id;
 UPDATE platform.deep_dub_projects SET row_version=row_version+1 WHERE workspace_id=p_workspace AND id=p_project;
 INSERT INTO platform.deep_dub_translation_edits(workspace_id,project_id,segment_id,base_translation_id,translation_id,
  expected_project_version,project_version,request_key,request_hash,created_by)
 VALUES(p_workspace,p_project,p_segment,p_base,new_id,p_expected_project_version,p_expected_project_version+1,p_request_key,fingerprint,auth.uid()) RETURNING * INTO result;
 -- 不写审核、选版、音频和时间线；旧组合只能继续引用它实际绑定的旧译稿。
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_edit_translation(uuid,uuid,uuid,uuid,integer,bigint,text,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_edit_translation(uuid,uuid,uuid,uuid,integer,bigint,text,text) TO authenticated;
NOTIFY pgrst,'reload schema';
