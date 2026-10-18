-- 来源准备的持久化待审状态；不能由客户端直接宣告源、字幕或整片通过。
CREATE OR REPLACE FUNCTION platform.deep_dub_wait_for_source_review(p_workspace uuid,p_run uuid,p_attempt uuid)
RETURNS platform.deep_dub_runs LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE run platform.deep_dub_runs; task platform.tasks; stage platform.deep_dub_stages; attempt platform.deep_dub_stage_attempts;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.task.run') OR NOT platform.has_permission(p_workspace,'deep_dub.audit') THEN
  RAISE EXCEPTION '无权登记来源待复核' USING ERRCODE='42501'; END IF;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run;
 IF NOT FOUND THEN RAISE EXCEPTION '运行不存在' USING ERRCODE='PT404'; END IF;
 PERFORM 1 FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND id=run.project_id FOR KEY SHARE;
 SELECT * INTO task FROM platform.tasks WHERE workspace_id=p_workspace AND id=run.task_id FOR UPDATE;
 SELECT * INTO run FROM platform.deep_dub_runs WHERE workspace_id=p_workspace AND id=p_run FOR UPDATE;
 SELECT * INTO attempt FROM platform.deep_dub_stage_attempts WHERE workspace_id=p_workspace AND project_id=run.project_id AND id=p_attempt;
 IF NOT FOUND THEN RAISE EXCEPTION '来源复核尝试不存在' USING ERRCODE='PT404'; END IF;
 SELECT * INTO stage FROM platform.deep_dub_stages WHERE workspace_id=p_workspace AND run_id=p_run AND id=attempt.stage_id;
 IF NOT FOUND OR stage.node<>'source_preparation_review' OR stage.current_attempt_id<>attempt.id
 OR attempt.created_by<>auth.uid() OR attempt.status<>'waiting_review' OR stage.status<>'waiting_review'
 OR jsonb_array_length(attempt.outputs)<>1 OR run.source_id IS NULL THEN
  RAISE EXCEPTION '缺少当前执行者的冻结来源待审回执' USING ERRCODE='PT409'; END IF;
 IF task.status<>'running' OR task.cancel_requested OR run.status NOT IN ('running','waiting_review') OR run.technical_verified_at IS NOT NULL THEN
  RAISE EXCEPTION '运行已停止、取消或状态不允许' USING ERRCODE='PT409'; END IF;
 UPDATE platform.deep_dub_runs SET status='waiting_review' WHERE id=p_run RETURNING * INTO run;
 RETURN run;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_wait_for_source_review(uuid,uuid,uuid) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_wait_for_source_review(uuid,uuid,uuid) TO authenticated;
NOTIFY pgrst,'reload schema';
