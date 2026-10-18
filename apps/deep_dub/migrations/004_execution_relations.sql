-- 运行身份、资源分配和观测账本。所有时钟由数据库记录，不接受调用者回填。
ALTER TABLE platform.deep_dub_runs ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'production' CHECK(purpose IN ('production','acceptance'));
ALTER TABLE platform.deep_dub_runs ADD COLUMN IF NOT EXISTS request_hash text CHECK(request_hash ~ '^[a-f0-9]{64}$');
ALTER TABLE platform.deep_dub_runs ADD COLUMN IF NOT EXISTS reuse_policy text NOT NULL DEFAULT 'none' CHECK(reuse_policy IN ('none','approved_versions'));
ALTER TABLE platform.deep_dub_stages ADD COLUMN IF NOT EXISTS required_device_kind text CHECK(required_device_kind IN ('cpu','gpu'));
ALTER TABLE platform.deep_dub_stages ADD COLUMN IF NOT EXISTS current_attempt_id uuid;
ALTER TABLE platform.deep_dub_stage_attempts ADD COLUMN IF NOT EXISTS request_key text;
ALTER TABLE platform.deep_dub_stage_attempts ADD COLUMN IF NOT EXISTS commit_hash text CHECK(commit_hash ~ '^[a-f0-9]{64}$');
ALTER TABLE platform.deep_dub_artifacts ADD COLUMN IF NOT EXISTS stage_attempt_id uuid;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='platform.deep_dub_stage_attempts'::regclass AND conname='deep_dub_attempt_stage_binding') THEN
  ALTER TABLE platform.deep_dub_stage_attempts ADD CONSTRAINT deep_dub_attempt_stage_binding UNIQUE(workspace_id,project_id,stage_id,id);
  ALTER TABLE platform.deep_dub_stage_attempts ADD CONSTRAINT deep_dub_attempt_request UNIQUE(workspace_id,stage_id,request_key);
  ALTER TABLE platform.deep_dub_stages ADD CONSTRAINT deep_dub_stage_current_attempt FOREIGN KEY(workspace_id,project_id,id,current_attempt_id)
   REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,stage_id,id) DEFERRABLE INITIALLY DEFERRED;
  ALTER TABLE platform.deep_dub_artifacts ADD CONSTRAINT deep_dub_artifact_attempt FOREIGN KEY(workspace_id,project_id,stage_attempt_id)
   REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id);
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS platform.deep_dub_devices (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES platform.workspaces(id),
 device_key text NOT NULL CHECK(length(device_key) BETWEEN 1 AND 200),host_key text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('cpu','gpu')),capacity integer NOT NULL CHECK(capacity BETWEEN 1 AND 128),
 state text NOT NULL DEFAULT 'unverified' CHECK(state IN ('unverified','ready','quarantined','disabled')),
 fence_seq bigint NOT NULL DEFAULT 0 CHECK(fence_seq>=0),row_version bigint NOT NULL DEFAULT 1 CHECK(row_version>0),
 probe jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(probe)='object'),probed_at timestamptz,
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(kind<>'gpu' OR capacity=1),UNIQUE(workspace_id,id),UNIQUE(workspace_id,device_key)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_device_leases (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 device_id uuid NOT NULL,stage_attempt_id uuid NOT NULL,owner_key text NOT NULL CHECK(length(owner_key) BETWEEN 1 AND 200),
 token_hash text NOT NULL CHECK(token_hash ~ '^[a-f0-9]{64}$'),fence bigint NOT NULL CHECK(fence>0),
 slots integer NOT NULL CHECK(slots BETWEEN 1 AND 128),
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','expired','released')),
 acquired_at timestamptz NOT NULL DEFAULT clock_timestamp(),expires_at timestamptz NOT NULL,
 heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),released_at timestamptz,
 operation_stopped boolean NOT NULL DEFAULT false,stop_evidence jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(stop_evidence)='object'),request_key text NOT NULL,
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),
 FOREIGN KEY(workspace_id,device_id) REFERENCES platform.deep_dub_devices(workspace_id,id),
 FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id),
 CHECK(expires_at>acquired_at),CHECK(status<>'released' OR (released_at IS NOT NULL AND operation_stopped)),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,device_id,fence),
 UNIQUE(workspace_id,stage_attempt_id,request_key),UNIQUE(workspace_id,stage_attempt_id)
);
CREATE INDEX IF NOT EXISTS deep_dub_active_device_leases ON platform.deep_dub_device_leases(workspace_id,device_id,expires_at) WHERE status='active';
CREATE TABLE IF NOT EXISTS platform.deep_dub_scores (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 stage_attempt_id uuid NOT NULL,metric text NOT NULL,rule_revision text NOT NULL,
 subject_hash text NOT NULL CHECK(subject_hash ~ '^[a-f0-9]{64}$'),
 measurement_status text NOT NULL CHECK(measurement_status IN ('measured','not_measured')),
 value numeric,evidence_artifact_id uuid,details jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(details)='object'),
 request_key text NOT NULL,request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((measurement_status='not_measured' AND value IS NULL) OR
       (measurement_status='measured' AND value IS NOT NULL AND value BETWEEN -1e100 AND 1e100 AND evidence_artifact_id IS NOT NULL)),
 FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,evidence_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,request_key)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_observation_outbox (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 stage_attempt_id uuid NOT NULL,score_id uuid,event_kind text NOT NULL CHECK(event_kind IN ('stage','score')),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent')),
 delivery_attempts integer NOT NULL DEFAULT 0 CHECK(delivery_attempts>=0),
 claim_token_hash text,claimed_by uuid REFERENCES auth.users(id),claim_expires_at timestamptz,next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 sent_at timestamptz,last_error text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((event_kind='score')=(score_id IS NOT NULL)),
 FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,score_id) REFERENCES platform.deep_dub_scores(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,stage_attempt_id,event_kind,score_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS deep_dub_one_stage_event ON platform.deep_dub_observation_outbox(workspace_id,stage_attempt_id) WHERE event_kind='stage';
CREATE TABLE IF NOT EXISTS platform.deep_dub_publications (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,render_id uuid NOT NULL,
 platform text NOT NULL CHECK(platform='bilibili'),revision integer NOT NULL CHECK(revision>0),
 request_key text NOT NULL,request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','uploading','receipt_unknown','submitted','published','failed')),
 video_sha256 text NOT NULL CHECK(video_sha256 ~ '^[a-f0-9]{64}$'),
 claim_token_hash text,claimed_by uuid REFERENCES auth.users(id),claim_expires_at timestamptz,receipt jsonb,public_url text,
 receipt_artifact_id uuid,public_evidence_artifact_id uuid,
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 submitted_at timestamptz,published_at timestamptz,
 FOREIGN KEY(workspace_id,project_id,render_id) REFERENCES platform.deep_dub_renders(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,receipt_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,public_evidence_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 CHECK(status NOT IN ('submitted','published') OR (receipt IS NOT NULL AND submitted_at IS NOT NULL AND receipt_artifact_id IS NOT NULL)),
 CHECK(status<>'published' OR (public_url IS NOT NULL AND published_at IS NOT NULL AND public_evidence_artifact_id IS NOT NULL)),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,render_id,platform,revision),
 UNIQUE(workspace_id,project_id,request_key),UNIQUE(workspace_id,render_id,platform)
);
CREATE UNIQUE INDEX IF NOT EXISTS deep_dub_publication_bvid_once ON platform.deep_dub_publications(workspace_id,(receipt->>'bvid')) WHERE receipt IS NOT NULL;
DO $$ DECLARE name text; BEGIN
 FOREACH name IN ARRAY ARRAY['deep_dub_devices','deep_dub_device_leases','deep_dub_scores','deep_dub_observation_outbox','deep_dub_publications'] LOOP
  EXECUTE format('ALTER TABLE platform.%I ENABLE ROW LEVEL SECURITY',name);
  EXECUTE format('REVOKE ALL ON platform.%I FROM authenticated',name);
  EXECUTE format('GRANT ALL ON platform.%I TO service_role',name);
  EXECUTE format('DROP POLICY IF EXISTS deep_dub_read ON platform.%I',name);
  EXECUTE format('CREATE POLICY deep_dub_read ON platform.%I FOR SELECT TO authenticated USING(platform.has_permission(workspace_id,''deep_dub.project.read''))',name);
 END LOOP;
END $$;
GRANT SELECT ON platform.deep_dub_devices,platform.deep_dub_scores TO authenticated;
GRANT SELECT(id,workspace_id,project_id,device_id,stage_attempt_id,owner_key,fence,slots,status,acquired_at,expires_at,heartbeat_at,released_at,operation_stopped,stop_evidence,request_key,created_by)
 ON platform.deep_dub_device_leases TO authenticated;
GRANT SELECT(id,workspace_id,project_id,stage_attempt_id,score_id,event_kind,status,delivery_attempts,next_attempt_at,sent_at,last_error,created_at)
 ON platform.deep_dub_observation_outbox TO authenticated;
GRANT SELECT(id,workspace_id,project_id,render_id,platform,revision,request_key,status,video_sha256,receipt,public_url,receipt_artifact_id,public_evidence_artifact_id,created_by,created_at,submitted_at,published_at)
 ON platform.deep_dub_publications TO authenticated;
DROP TRIGGER IF EXISTS immutable_revision ON platform.deep_dub_scores;
CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON platform.deep_dub_scores FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_immutable_revision();
INSERT INTO platform.role_permissions(role,permission) VALUES('admin','deep_dub.device.manage') ON CONFLICT DO NOTHING;
INSERT INTO platform.role_permissions(role,permission)
SELECT role,permission FROM unnest(ARRAY['admin','developer']) role CROSS JOIN unnest(ARRAY['deep_dub.task.read','deep_dub.task.create','deep_dub.task.cancel']) permission
ON CONFLICT DO NOTHING;
INSERT INTO platform.role_permissions(role,permission) VALUES('viewer','deep_dub.task.read') ON CONFLICT DO NOTHING;
NOTIFY pgrst,'reload schema';
