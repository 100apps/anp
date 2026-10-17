-- 按调用者身份和 RLS 统计完整任务集合，不受 REST 列表行数上限影响。
CREATE OR REPLACE FUNCTION platform.task_summary(p_workspace uuid,p_app text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,platform AS $$
BEGIN
 IF NOT platform.has_permission(p_workspace,p_app||'.task.read') THEN
  RAISE EXCEPTION '无权读取任务统计' USING ERRCODE='42501';
 END IF;
 RETURN (SELECT jsonb_build_object(
  'total',count(*),
  'pending',count(*) FILTER (WHERE status='pending'),
  'running',count(*) FILTER (WHERE status='running'),
  'succeeded',count(*) FILTER (WHERE status='succeeded'),
  'failed',count(*) FILTER (WHERE status='failed'),
  'cancelled',count(*) FILTER (WHERE status='cancelled'))
 FROM platform.tasks WHERE workspace_id=p_workspace AND app_id=p_app);
END $$;
REVOKE ALL ON FUNCTION platform.task_summary(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.task_summary(uuid,text) TO authenticated;
NOTIFY pgrst, 'reload schema';
