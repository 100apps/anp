DROP POLICY IF EXISTS member_workspaces ON dubbing.workspaces;
CREATE POLICY member_workspaces ON dubbing.workspaces FOR SELECT TO authenticated USING(
 EXISTS(SELECT 1 FROM dubbing.memberships m WHERE m.workspace_id=workspaces.id AND m.user_id=auth.uid()));
