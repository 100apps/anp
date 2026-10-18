-- 第一阶段关系基础；后续迁移补充逐段候选、审核、设备租约与发布。
-- 所有关系使用工作空间复合外键，业务执行不得使用迁移账号。
CREATE TABLE IF NOT EXISTS platform.deep_dub_projects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id),
 created_by uuid NOT NULL REFERENCES auth.users(id),
 name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 120),
 source_url text NOT NULL CHECK(length(source_url) BETWEEN 1 AND 4096),
 target_language text NOT NULL DEFAULT 'zh-CN' CHECK(target_language='zh-CN'),
 request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 200),
 row_version bigint NOT NULL DEFAULT 1 CHECK(row_version>0),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,request_key)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_quality_profiles (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id),
 profile_key text NOT NULL CHECK(profile_key IN ('high','medium','low')),
 revision integer NOT NULL CHECK(revision>0),
 rules jsonb NOT NULL CHECK(jsonb_typeof(rules)='object'),
 rules_hash text NOT NULL CHECK(rules_hash ~ '^[a-f0-9]{64}$'),
 provenance text NOT NULL,
 created_by uuid NOT NULL REFERENCES auth.users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,profile_key,revision)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_artifacts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id),
 project_id uuid NOT NULL,
 object_path text NOT NULL CHECK(object_path !~ '(^/|(^|/)\.\.(/|$)|^[A-Za-z]:)'),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 bytes bigint NOT NULL CHECK(bytes>=0), media_type text NOT NULL,
 duration_ms bigint CHECK(duration_ms>=0), sample_rate integer CHECK(sample_rate>0),
 created_by uuid NOT NULL REFERENCES auth.users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id) REFERENCES platform.deep_dub_projects(workspace_id,id),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,project_id,id),
 UNIQUE(workspace_id,object_path)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_sources (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id),
 project_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
 source_url text NOT NULL, artifact_id uuid NOT NULL,
 duration_ms bigint NOT NULL CHECK(duration_ms>0),
 identity_evidence jsonb NOT NULL CHECK(jsonb_typeof(identity_evidence)='object'),
 created_by uuid NOT NULL REFERENCES auth.users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id) REFERENCES platform.deep_dub_projects(workspace_id,id),
 FOREIGN KEY(workspace_id,project_id,artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,project_id,id), UNIQUE(workspace_id,project_id,revision)
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='platform.tasks'::regclass AND conname='tasks_workspace_id_unique') THEN
  ALTER TABLE platform.tasks ADD CONSTRAINT tasks_workspace_id_unique UNIQUE(workspace_id,id);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='platform.tasks'::regclass AND conname='tasks_workspace_app_id_unique') THEN
  ALTER TABLE platform.tasks ADD CONSTRAINT tasks_workspace_app_id_unique UNIQUE(workspace_id,id,app_id);
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS platform.deep_dub_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id), project_id uuid NOT NULL,
 task_id uuid NOT NULL, app_id text GENERATED ALWAYS AS ('deep_dub'::text) STORED,
 source_id uuid, quality_profile_id uuid NOT NULL,
 flow_revision text NOT NULL,
 input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 200),
 mode text NOT NULL CHECK(mode IN ('local','http')),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','waiting_review','succeeded','failed','cancelled')),
 queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 started_at timestamptz, technical_verified_at timestamptz,
 created_by uuid NOT NULL REFERENCES auth.users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id) REFERENCES platform.deep_dub_projects(workspace_id,id),
 FOREIGN KEY(workspace_id,task_id,app_id) REFERENCES platform.tasks(workspace_id,id,app_id),
 FOREIGN KEY(workspace_id,project_id,source_id) REFERENCES platform.deep_dub_sources(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,quality_profile_id) REFERENCES platform.deep_dub_quality_profiles(workspace_id,id),
 CHECK(started_at IS NULL OR started_at>=queued_at),
 CHECK(technical_verified_at IS NULL OR technical_verified_at>=queued_at),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,project_id,id), UNIQUE(workspace_id,project_id,request_key),
 UNIQUE(workspace_id,task_id)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_stages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id), project_id uuid NOT NULL,run_id uuid NOT NULL,
 node text NOT NULL, partition_key text NOT NULL DEFAULT '',
 input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','waiting_review','succeeded','failed','cancelled')),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,run_id) REFERENCES platform.deep_dub_runs(workspace_id,project_id,id),
 UNIQUE(workspace_id,id), UNIQUE(workspace_id,project_id,id), UNIQUE(workspace_id,run_id,node,partition_key,input_hash)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_stage_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspaces(id),project_id uuid NOT NULL,stage_id uuid NOT NULL,
 attempt_no integer NOT NULL CHECK(attempt_no>0),
 queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),loaded_at timestamptz,started_at timestamptz,finished_at timestamptz,
 monotonic_seconds double precision CHECK(monotonic_seconds>=0 AND monotonic_seconds<'Infinity'::float8),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','waiting_review','succeeded','failed','cancelled')),
 error jsonb, outputs jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(outputs)='array'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,stage_id) REFERENCES platform.deep_dub_stages(workspace_id,project_id,id),
 CHECK(finished_at IS NULL OR finished_at>=queued_at),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,stage_id,attempt_no)
);
CREATE OR REPLACE FUNCTION platform.deep_dub_immutable_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
BEGIN RAISE EXCEPTION '不可变修订必须新增版本' USING ERRCODE='23514'; END $$;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['deep_dub_quality_profiles','deep_dub_sources','deep_dub_artifacts'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS immutable_revision ON platform.%I',name);
  EXECUTE format('CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON platform.%I FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_immutable_revision()',name);
 END LOOP;
 FOREACH name IN ARRAY ARRAY['deep_dub_projects','deep_dub_quality_profiles','deep_dub_artifacts','deep_dub_sources','deep_dub_runs','deep_dub_stages','deep_dub_stage_attempts'] LOOP
  EXECUTE format('ALTER TABLE platform.%I ENABLE ROW LEVEL SECURITY',name);
  EXECUTE format('GRANT SELECT ON platform.%I TO authenticated',name);
  EXECUTE format('GRANT ALL ON platform.%I TO service_role',name);
  EXECUTE format('DROP POLICY IF EXISTS deep_dub_read ON platform.%I',name);
  EXECUTE format('CREATE POLICY deep_dub_read ON platform.%I FOR SELECT TO authenticated USING(platform.has_permission(workspace_id,''deep_dub.project.read''))',name);
 END LOOP;
END $$;
-- 写入经受限 RPC；不给 authenticated 直接改表，防止回填生产起点或篡改版本。
REVOKE INSERT,UPDATE,DELETE ON platform.deep_dub_projects,platform.deep_dub_quality_profiles,
 platform.deep_dub_artifacts,platform.deep_dub_sources,platform.deep_dub_runs,
 platform.deep_dub_stages,platform.deep_dub_stage_attempts FROM authenticated;
CREATE OR REPLACE FUNCTION platform.deep_dub_create_project(
 p_workspace uuid,p_name text,p_source_url text,p_request_key text
) RETURNS platform.deep_dub_projects
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform AS $$
DECLARE result platform.deep_dub_projects;
BEGIN
 IF NOT platform.has_permission(p_workspace,'deep_dub.project.create') THEN
  RAISE EXCEPTION '无权创建译配项目' USING ERRCODE='42501'; END IF;
 IF p_name IS NULL OR length(btrim(p_name)) NOT BETWEEN 1 AND 120
 OR p_source_url IS NULL OR length(p_source_url) NOT BETWEEN 1 AND 4096
 OR p_request_key IS NULL OR length(p_request_key) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION '项目输入不合法' USING ERRCODE='22023'; END IF;
 INSERT INTO platform.deep_dub_projects(workspace_id,created_by,name,source_url,request_key)
 VALUES(p_workspace,auth.uid(),p_name,p_source_url,p_request_key)
 ON CONFLICT(workspace_id,request_key) DO NOTHING;
 SELECT * INTO result FROM platform.deep_dub_projects WHERE workspace_id=p_workspace AND request_key=p_request_key;
 IF result.name<>p_name OR result.source_url<>p_source_url THEN
  RAISE EXCEPTION '相同幂等键不能提交不同内容' USING ERRCODE='23505'; END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION platform.deep_dub_create_project(uuid,text,text,text) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION platform.deep_dub_create_project(uuid,text,text,text) TO authenticated;
INSERT INTO platform.role_permissions(role,permission)
SELECT role,permission FROM unnest(ARRAY['admin','developer']) role CROSS JOIN unnest(
 ARRAY['deep_dub.project.read','deep_dub.project.create','deep_dub.project.edit','deep_dub.task.run','deep_dub.audit','deep_dub.publish']) permission
ON CONFLICT DO NOTHING;
INSERT INTO platform.role_permissions(role,permission) VALUES('viewer','deep_dub.project.read') ON CONFLICT DO NOTHING;
NOTIFY pgrst,'reload schema';
