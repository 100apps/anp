-- Supabase 托管或本地均可执行。所有业务关联通过复合外键约束空间/项目归属。
CREATE SCHEMA IF NOT EXISTS dubbing;
REVOKE ALL ON SCHEMA dubbing FROM PUBLIC, anon;
GRANT USAGE ON SCHEMA dubbing TO authenticated, service_role;

CREATE TABLE IF NOT EXISTS dubbing.workspaces (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dubbing.memberships (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES dubbing.workspaces,
 user_id uuid NOT NULL REFERENCES auth.users, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,user_id)
);
CREATE TABLE IF NOT EXISTS dubbing.projects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES dubbing.workspaces,
 title text NOT NULL, source_language text NOT NULL DEFAULT 'en', target_language text NOT NULL DEFAULT 'zh',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.assets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL,
 kind text NOT NULL, bucket text NOT NULL, object_key text NOT NULL, sha256 text NOT NULL CHECK(length(sha256)=64),
 size_bytes bigint NOT NULL CHECK(size_bytes>0), duration_seconds double precision,
 metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(workspace_id,project_id,id), UNIQUE(bucket,object_key),
 FOREIGN KEY(workspace_id,project_id) REFERENCES dubbing.projects(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.speakers (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL,
 label text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id) REFERENCES dubbing.projects(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.segments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL,
 source_asset_id uuid NOT NULL, speaker_id uuid NOT NULL, ordinal integer NOT NULL,
 start_seconds double precision NOT NULL CHECK(start_seconds>=0), end_seconds double precision NOT NULL,
 text text NOT NULL CHECK(length(text)>0), words jsonb NOT NULL DEFAULT '[]',
 provenance text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(end_seconds>start_seconds), UNIQUE(project_id,ordinal), UNIQUE(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,source_asset_id) REFERENCES dubbing.assets(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,speaker_id) REFERENCES dubbing.speakers(workspace_id,project_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.translations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL,
 segment_id uuid NOT NULL, version integer NOT NULL CHECK(version>0), text text NOT NULL CHECK(length(text)>0),
 model text NOT NULL, evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(segment_id,version), UNIQUE(workspace_id,project_id,id), UNIQUE(workspace_id,project_id,segment_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id) REFERENCES dubbing.segments(workspace_id,project_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.voices (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL,
 speaker_id uuid NOT NULL, asset_id uuid NOT NULL, source_start double precision NOT NULL,
 source_end double precision NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(source_end>source_start), UNIQUE(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,speaker_id) REFERENCES dubbing.speakers(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,asset_id) REFERENCES dubbing.assets(workspace_id,project_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL,
 request_key uuid NOT NULL, request jsonb NOT NULL, status text NOT NULL DEFAULT 'queued'
 CHECK(status IN ('queued','running','failed','cancelled','ready_for_review','approved')),
 current_stage text, error text, started_at timestamptz, finished_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(workspace_id,request_key), UNIQUE(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id) REFERENCES dubbing.projects(workspace_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL, run_id uuid NOT NULL,
 stage text NOT NULL, attempt integer NOT NULL CHECK(attempt>0), input_sha256 text NOT NULL,
 status text NOT NULL CHECK(status IN ('running','succeeded','failed')), output jsonb, error text,
 finished_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run_id,stage,attempt),
 FOREIGN KEY(workspace_id,project_id,run_id) REFERENCES dubbing.runs(workspace_id,project_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.syntheses (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL, run_id uuid NOT NULL,
 segment_id uuid NOT NULL, translation_id uuid NOT NULL, voice_id uuid NOT NULL, asset_id uuid NOT NULL,
 generation_factor double precision NOT NULL CHECK(generation_factor BETWEEN .9 AND 1.35),
 duration_seconds double precision NOT NULL CHECK(duration_seconds>0), selected boolean NOT NULL DEFAULT false,
 evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,run_id) REFERENCES dubbing.runs(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id,translation_id) REFERENCES dubbing.translations(workspace_id,project_id,segment_id,id),
 FOREIGN KEY(workspace_id,project_id,voice_id) REFERENCES dubbing.voices(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,asset_id) REFERENCES dubbing.assets(workspace_id,project_id,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS dubbing_one_selected_audio ON dubbing.syntheses(run_id,segment_id) WHERE selected;
CREATE TABLE IF NOT EXISTS dubbing.reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL, run_id uuid NOT NULL,
 kind text NOT NULL, subject_sha256 text NOT NULL, verdict text NOT NULL CHECK(verdict IN ('passed','failed','not_measured')),
 evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,run_id) REFERENCES dubbing.runs(workspace_id,project_id,id)
);
CREATE TABLE IF NOT EXISTS dubbing.deliverables (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL, project_id uuid NOT NULL, run_id uuid NOT NULL,
 asset_id uuid NOT NULL, manifest jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,run_id) REFERENCES dubbing.runs(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,asset_id) REFERENCES dubbing.assets(workspace_id,project_id,id)
);

-- 成员表策略不自引用，避免无限递归。匿名无表权限，后台仍以 authenticated 角色执行。
ALTER TABLE dubbing.memberships ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS own_memberships ON dubbing.memberships;
CREATE POLICY own_memberships ON dubbing.memberships FOR SELECT TO authenticated USING(user_id=auth.uid());
ALTER TABLE dubbing.workspaces ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS member_workspaces ON dubbing.workspaces;
CREATE POLICY member_workspaces ON dubbing.workspaces FOR SELECT TO authenticated USING(
 EXISTS(SELECT 1 FROM dubbing.memberships m WHERE m.workspace_id=workspaces.id AND m.user_id=auth.uid()));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['projects','assets','speakers','segments','translations','voices','runs','attempts','syntheses','reviews','deliverables'] LOOP
 EXECUTE format('ALTER TABLE dubbing.%I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('DROP POLICY IF EXISTS workspace_member ON dubbing.%I',t);
 EXECUTE format('CREATE POLICY workspace_member ON dubbing.%I TO authenticated USING (EXISTS(SELECT 1 FROM dubbing.memberships m WHERE m.workspace_id=%I.workspace_id AND m.user_id=auth.uid())) WITH CHECK (EXISTS(SELECT 1 FROM dubbing.memberships m WHERE m.workspace_id=%I.workspace_id AND m.user_id=auth.uid()))',t,t,t);
 EXECUTE format('GRANT SELECT,INSERT,UPDATE ON dubbing.%I TO authenticated',t);
 END LOOP;
END $$;
GRANT SELECT ON dubbing.workspaces,dubbing.memberships TO authenticated;
-- 原文、候选、审核与产物不可覆盖；只有运行状态与音频选版可更新。
REVOKE UPDATE ON dubbing.assets,dubbing.speakers,dubbing.segments,dubbing.translations,dubbing.voices,dubbing.reviews,dubbing.deliverables FROM authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA dubbing TO service_role;
NOTIFY pgrst, 'reload schema';
