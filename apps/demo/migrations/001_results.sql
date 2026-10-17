CREATE SCHEMA IF NOT EXISTS app_demo;
GRANT USAGE ON SCHEMA app_demo TO authenticated,service_role;
CREATE TABLE IF NOT EXISTS app_demo.results (
 task_id uuid PRIMARY KEY REFERENCES platform.tasks(id) ON DELETE CASCADE,
 report jsonb NOT NULL, object_path text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE app_demo.results ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS result_read ON app_demo.results;
CREATE POLICY result_read ON app_demo.results FOR SELECT TO authenticated USING(EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id=task_id AND platform.has_permission(t.workspace_id,'demo.task.read')));
DROP POLICY IF EXISTS result_insert ON app_demo.results;
CREATE POLICY result_insert ON app_demo.results FOR INSERT TO authenticated WITH CHECK(EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id=task_id AND platform.has_permission(t.workspace_id,'demo.task.run')));
DROP POLICY IF EXISTS result_update ON app_demo.results;
CREATE POLICY result_update ON app_demo.results FOR UPDATE TO authenticated USING(EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id=task_id AND platform.has_permission(t.workspace_id,'demo.task.run')))
 WITH CHECK(EXISTS(SELECT 1 FROM platform.tasks t WHERE t.id=task_id AND platform.has_permission(t.workspace_id,'demo.task.run')));
GRANT SELECT,INSERT,UPDATE ON app_demo.results TO authenticated;
GRANT ALL ON app_demo.results TO service_role;
NOTIFY pgrst,'reload schema';
