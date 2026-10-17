CREATE SCHEMA IF NOT EXISTS platform;
GRANT USAGE ON SCHEMA platform TO authenticated, service_role;

CREATE TABLE IF NOT EXISTS platform.workspaces (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL
);
CREATE TABLE IF NOT EXISTS platform.profiles (
 user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
 email text NOT NULL UNIQUE, display_name text NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS platform.members (
 workspace_id uuid REFERENCES platform.workspaces(id) ON DELETE CASCADE,
 user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
 role text NOT NULL CHECK(role IN ('admin','developer','viewer')),
 PRIMARY KEY(workspace_id,user_id)
);
CREATE TABLE IF NOT EXISTS platform.admins (
 user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS platform.role_permissions (
 role text NOT NULL, permission text NOT NULL, PRIMARY KEY(role,permission)
);
CREATE TABLE IF NOT EXISTS platform.tasks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id),
 created_by uuid NOT NULL REFERENCES auth.users(id),
 app_id text NOT NULL, title text NOT NULL, input jsonb NOT NULL,
 request_key text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
 mode text, attempt int NOT NULL DEFAULT 0, cancel_requested boolean NOT NULL DEFAULT false,
 thread_id text, run_id text, error text, result jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,app_id,request_key)
);
CREATE TABLE IF NOT EXISTS platform.node_operations (
 operation_id text PRIMARY KEY, task_id uuid NOT NULL REFERENCES platform.tasks(id),
 node text NOT NULL, input_hash text NOT NULL, output jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS platform.migrations (
 name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION platform.is_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
 SELECT EXISTS(SELECT 1 FROM platform.admins WHERE user_id=auth.uid());
$$;
CREATE OR REPLACE FUNCTION platform.is_member(p_workspace uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
 SELECT EXISTS(SELECT 1 FROM platform.members WHERE workspace_id=p_workspace AND user_id=auth.uid());
$$;
CREATE OR REPLACE FUNCTION platform.has_permission(p_workspace uuid,p_permission text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
 SELECT EXISTS(SELECT 1 FROM platform.members m JOIN platform.role_permissions p ON p.role=m.role
 WHERE m.workspace_id=p_workspace AND m.user_id=auth.uid() AND p.permission=p_permission);
$$;
CREATE OR REPLACE FUNCTION platform.begin_task(p_task uuid,p_mode text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE t platform.tasks;
BEGIN
 SELECT * INTO t FROM platform.tasks WHERE id=p_task FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '任务不存在' USING ERRCODE='P0002'; END IF;
 IF NOT platform.has_permission(t.workspace_id,t.app_id||'.task.run') THEN
  RAISE EXCEPTION '无权执行任务' USING ERRCODE='42501'; END IF;
 IF t.status IN ('running','succeeded') THEN RETURN jsonb_build_object('task',to_jsonb(t),'acquired',false); END IF;
 UPDATE platform.tasks SET status='running', mode=p_mode, attempt=attempt+1,
 cancel_requested=false,error=null,updated_at=now(),run_id=null,thread_id=null
 WHERE id=p_task RETURNING * INTO t;
 RETURN jsonb_build_object('task',to_jsonb(t),'acquired',true);
END $$;
CREATE OR REPLACE FUNCTION platform.cancel_task(p_task uuid) RETURNS platform.tasks
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE t platform.tasks;
BEGIN
 SELECT * INTO t FROM platform.tasks WHERE id=p_task FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION '任务不存在' USING ERRCODE='P0002'; END IF;
 IF NOT platform.has_permission(t.workspace_id,t.app_id||'.task.cancel') THEN
  RAISE EXCEPTION '无权取消任务' USING ERRCODE='42501'; END IF;
 IF t.status IN ('succeeded','failed','cancelled') THEN RETURN t; END IF;
 UPDATE platform.tasks SET cancel_requested=true,status='cancelled',updated_at=now()
 WHERE id=p_task RETURNING * INTO t;
 RETURN t;
END $$;
CREATE OR REPLACE FUNCTION platform.set_member_role(p_workspace uuid,p_user uuid,p_role text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
BEGIN
 IF NOT platform.has_permission(p_workspace,'workspace.members.manage') THEN
  RAISE EXCEPTION '无权管理成员' USING ERRCODE='42501'; END IF;
 IF p_role NOT IN ('admin','developer','viewer') THEN RAISE EXCEPTION '无效角色'; END IF;
 IF p_user=auth.uid() AND p_role <> 'admin' THEN RAISE EXCEPTION '不能降级自己的管理员角色'; END IF;
 INSERT INTO platform.members VALUES(p_workspace,p_user,p_role)
 ON CONFLICT(workspace_id,user_id) DO UPDATE SET role=excluded.role;
END $$;

ALTER TABLE platform.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.members ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.node_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.migrations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_read ON platform.workspaces;
CREATE POLICY workspace_read ON platform.workspaces FOR SELECT TO authenticated
 USING(platform.is_member(id));
DROP POLICY IF EXISTS profile_read ON platform.profiles;
CREATE POLICY profile_read ON platform.profiles FOR SELECT TO authenticated USING(user_id=auth.uid() OR EXISTS(
 SELECT 1 FROM platform.members m WHERE m.user_id=platform.profiles.user_id AND platform.is_member(m.workspace_id)));
DROP POLICY IF EXISTS member_read ON platform.members;
CREATE POLICY member_read ON platform.members FOR SELECT TO authenticated USING(platform.is_member(workspace_id));
DROP POLICY IF EXISTS permission_read ON platform.role_permissions;
CREATE POLICY permission_read ON platform.role_permissions FOR SELECT TO authenticated USING(true);
DROP POLICY IF EXISTS admin_read ON platform.admins;
CREATE POLICY admin_read ON platform.admins FOR SELECT TO authenticated USING(user_id=auth.uid());
DROP POLICY IF EXISTS task_read ON platform.tasks;
CREATE POLICY task_read ON platform.tasks FOR SELECT TO authenticated
 USING(platform.has_permission(workspace_id,app_id||'.task.read'));
DROP POLICY IF EXISTS task_create ON platform.tasks;
CREATE POLICY task_create ON platform.tasks FOR INSERT TO authenticated
 WITH CHECK(created_by=auth.uid() AND platform.has_permission(workspace_id,app_id||'.task.create')
 AND status='pending' AND attempt=0);
DROP POLICY IF EXISTS task_update ON platform.tasks;
CREATE POLICY task_update ON platform.tasks FOR UPDATE TO authenticated
 USING(platform.has_permission(workspace_id,app_id||'.task.run'))
 WITH CHECK(platform.has_permission(workspace_id,app_id||'.task.run'));
DROP POLICY IF EXISTS node_read ON platform.node_operations;
CREATE POLICY node_read ON platform.node_operations FOR SELECT TO authenticated USING(EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id=task_id AND platform.has_permission(t.workspace_id,t.app_id||'.task.read')));
DROP POLICY IF EXISTS node_write ON platform.node_operations;
CREATE POLICY node_write ON platform.node_operations FOR INSERT TO authenticated WITH CHECK(EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id=task_id AND platform.has_permission(t.workspace_id,t.app_id||'.task.run')));

GRANT SELECT ON platform.workspaces,platform.profiles,platform.members,platform.admins,platform.role_permissions TO authenticated;
GRANT SELECT,INSERT,UPDATE ON platform.tasks TO authenticated;
GRANT SELECT,INSERT ON platform.node_operations TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA platform TO service_role;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA platform FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform.is_admin(),platform.is_member(uuid),platform.has_permission(uuid,text),
 platform.begin_task(uuid,text),platform.cancel_task(uuid),platform.set_member_role(uuid,uuid,text) TO authenticated,service_role;

INSERT INTO platform.workspaces(id,name) VALUES('00000000-0000-4000-8000-000000000001','默认工作空间') ON CONFLICT DO NOTHING;
INSERT INTO platform.role_permissions VALUES('admin','workspace.members.manage'),('admin','platform.connections.manage') ON CONFLICT DO NOTHING;

DROP POLICY IF EXISTS artifact_read ON storage.objects;
CREATE POLICY artifact_read ON storage.objects FOR SELECT TO authenticated USING(bucket_id='artifacts' AND EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id::text=(storage.foldername(name))[3]
 AND t.workspace_id::text=(storage.foldername(name))[1] AND t.app_id=(storage.foldername(name))[2]
 AND platform.has_permission(t.workspace_id,t.app_id||'.task.read')));
DROP POLICY IF EXISTS artifact_write ON storage.objects;
CREATE POLICY artifact_write ON storage.objects FOR INSERT TO authenticated WITH CHECK(bucket_id='artifacts' AND EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id::text=(storage.foldername(name))[3]
 AND t.workspace_id::text=(storage.foldername(name))[1] AND t.app_id=(storage.foldername(name))[2]
 AND platform.has_permission(t.workspace_id,t.app_id||'.task.run')));
DROP POLICY IF EXISTS artifact_update ON storage.objects;
CREATE POLICY artifact_update ON storage.objects FOR UPDATE TO authenticated USING(bucket_id='artifacts' AND EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id::text=(storage.foldername(name))[3]
 AND t.workspace_id::text=(storage.foldername(name))[1] AND t.app_id=(storage.foldername(name))[2]
 AND platform.has_permission(t.workspace_id,t.app_id||'.task.run')))
 WITH CHECK(bucket_id='artifacts' AND EXISTS(
 SELECT 1 FROM platform.tasks t WHERE t.id::text=(storage.foldername(name))[3]
 AND t.workspace_id::text=(storage.foldername(name))[1] AND t.app_id=(storage.foldername(name))[2]
 AND platform.has_permission(t.workspace_id,t.app_id||'.task.run')));
NOTIFY pgrst,'reload schema';
