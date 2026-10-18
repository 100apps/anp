-- 不可变候选与完整组合修订。写入入口在后续受限 RPC 中授权；普通用户只读。
CREATE OR REPLACE FUNCTION platform.deep_dub_digest(p_value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
 SELECT encode(sha256(convert_to(p_value::text,'UTF8')),'hex');
$$;
CREATE TABLE IF NOT EXISTS platform.deep_dub_speakers (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 speaker_key text NOT NULL,display_name text NOT NULL,row_version bigint NOT NULL DEFAULT 1 CHECK(row_version>0),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id) REFERENCES platform.deep_dub_projects(workspace_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,speaker_key)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_cues (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 source_id uuid NOT NULL,speaker_id uuid,cue_no integer NOT NULL CHECK(cue_no>=0),
 source_text text NOT NULL CHECK(length(btrim(source_text))>0),start_ms bigint NOT NULL CHECK(start_ms>=0),
 end_ms bigint NOT NULL CHECK(end_ms>start_ms),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,source_id) REFERENCES platform.deep_dub_sources(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,speaker_id) REFERENCES platform.deep_dub_speakers(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,source_id,id),UNIQUE(workspace_id,source_id,cue_no)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_segment_sets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 source_id uuid NOT NULL,parent_id uuid,revision integer NOT NULL CHECK(revision>0),sealed boolean NOT NULL DEFAULT false,
 strategy_revision text NOT NULL,input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,source_id) REFERENCES platform.deep_dub_sources(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,source_id,id),
 UNIQUE(workspace_id,project_id,revision),
 FOREIGN KEY(workspace_id,project_id,source_id,parent_id) REFERENCES platform.deep_dub_segment_sets(workspace_id,project_id,source_id,id)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_segments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 source_id uuid NOT NULL,segment_set_id uuid NOT NULL,speaker_id uuid NOT NULL,
 segment_no integer NOT NULL CHECK(segment_no>=0),source_text text NOT NULL CHECK(length(btrim(source_text))>0),
 start_ms bigint NOT NULL CHECK(start_ms>=0),end_ms bigint NOT NULL CHECK(end_ms>start_ms),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,source_id,segment_set_id) REFERENCES platform.deep_dub_segment_sets(workspace_id,project_id,source_id,id),
 FOREIGN KEY(workspace_id,project_id,speaker_id) REFERENCES platform.deep_dub_speakers(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,source_id,id),
 UNIQUE(workspace_id,project_id,segment_set_id,id),UNIQUE(workspace_id,project_id,id,speaker_id),
 UNIQUE(workspace_id,segment_set_id,segment_no)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_segment_cues (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 source_id uuid NOT NULL,segment_id uuid NOT NULL,cue_id uuid NOT NULL,position integer NOT NULL CHECK(position>=0),
 FOREIGN KEY(workspace_id,project_id,source_id,segment_id) REFERENCES platform.deep_dub_segments(workspace_id,project_id,source_id,id),
 FOREIGN KEY(workspace_id,project_id,source_id,cue_id) REFERENCES platform.deep_dub_cues(workspace_id,project_id,source_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,segment_id,cue_id),UNIQUE(workspace_id,segment_id,position)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_translations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,segment_id uuid NOT NULL,
 revision integer NOT NULL CHECK(revision>0),chinese_text text NOT NULL CHECK(length(btrim(chinese_text))>0),
 model_id text NOT NULL,prompt_revision text NOT NULL,input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,segment_id) REFERENCES platform.deep_dub_segments(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,segment_id,id),
 UNIQUE(workspace_id,segment_id,revision)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_voice_references (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 speaker_id uuid NOT NULL,source_id uuid NOT NULL,revision integer NOT NULL CHECK(revision>0),
 start_ms bigint NOT NULL CHECK(start_ms>=0),end_ms bigint NOT NULL CHECK(end_ms>start_ms),
 original_artifact_id uuid NOT NULL,processed_artifact_id uuid NOT NULL,processing jsonb NOT NULL DEFAULT '{}',
 input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,speaker_id) REFERENCES platform.deep_dub_speakers(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,source_id) REFERENCES platform.deep_dub_sources(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,original_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,processed_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,speaker_id,id),
 UNIQUE(workspace_id,speaker_id,revision)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_emotion_references (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 source_id uuid NOT NULL,segment_id uuid NOT NULL,revision integer NOT NULL CHECK(revision>0),
 start_ms bigint NOT NULL CHECK(start_ms>=0),end_ms bigint NOT NULL CHECK(end_ms>start_ms),artifact_id uuid NOT NULL,
 processing jsonb NOT NULL DEFAULT '{}',input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,source_id,segment_id) REFERENCES platform.deep_dub_segments(workspace_id,project_id,source_id,id),
 FOREIGN KEY(workspace_id,project_id,artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,segment_id,id),
 UNIQUE(workspace_id,segment_id,revision)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_audio_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 segment_id uuid NOT NULL,speaker_id uuid NOT NULL,translation_id uuid NOT NULL,
 voice_reference_id uuid NOT NULL,emotion_reference_id uuid NOT NULL,stage_attempt_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('generation','pcm_tempo')),parent_id uuid,
 generation_factor numeric NOT NULL DEFAULT 1 CHECK(generation_factor BETWEEN 0.90 AND 1.35),
 pcm_tempo_factor numeric NOT NULL DEFAULT 1 CHECK(pcm_tempo_factor BETWEEN 0.90 AND 1.10),
 parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object'),
 artifact_id uuid NOT NULL,duration_ms bigint NOT NULL CHECK(duration_ms>0),
 input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((kind='generation' AND parent_id IS NULL AND pcm_tempo_factor=1) OR (kind='pcm_tempo' AND parent_id IS NOT NULL)),
 FOREIGN KEY(workspace_id,project_id,segment_id,speaker_id) REFERENCES platform.deep_dub_segments(workspace_id,project_id,id,speaker_id),
 FOREIGN KEY(workspace_id,project_id,segment_id,translation_id) REFERENCES platform.deep_dub_translations(workspace_id,project_id,segment_id,id),
 FOREIGN KEY(workspace_id,project_id,speaker_id,voice_reference_id) REFERENCES platform.deep_dub_voice_references(workspace_id,project_id,speaker_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id,emotion_reference_id) REFERENCES platform.deep_dub_emotion_references(workspace_id,project_id,segment_id,id),
 FOREIGN KEY(workspace_id,project_id,stage_attempt_id) REFERENCES platform.deep_dub_stage_attempts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,segment_id,translation_id,id),
 UNIQUE(workspace_id,project_id,segment_id,translation_id,voice_reference_id,emotion_reference_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id,translation_id,voice_reference_id,emotion_reference_id,parent_id)
 REFERENCES platform.deep_dub_audio_attempts(workspace_id,project_id,segment_id,translation_id,voice_reference_id,emotion_reference_id,id)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_selections (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 segment_set_id uuid NOT NULL,segment_id uuid NOT NULL,translation_id uuid NOT NULL,audio_attempt_id uuid NOT NULL,
 quality_profile_id uuid NOT NULL,revision integer NOT NULL CHECK(revision>0),project_version bigint NOT NULL,sealed boolean NOT NULL DEFAULT false,
 request_key text NOT NULL,request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(workspace_id,project_id,segment_set_id,segment_id) REFERENCES platform.deep_dub_segments(workspace_id,project_id,segment_set_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id,translation_id) REFERENCES platform.deep_dub_translations(workspace_id,project_id,segment_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_id,translation_id,audio_attempt_id) REFERENCES platform.deep_dub_audio_attempts(workspace_id,project_id,segment_id,translation_id,id),
 FOREIGN KEY(workspace_id,quality_profile_id) REFERENCES platform.deep_dub_quality_profiles(workspace_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,segment_set_id,segment_id,id),
 UNIQUE(workspace_id,segment_id,revision),UNIQUE(workspace_id,project_id,request_key)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_timeline_revisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 source_id uuid NOT NULL,segment_set_id uuid NOT NULL,quality_profile_id uuid NOT NULL,revision integer NOT NULL CHECK(revision>0),sealed boolean NOT NULL DEFAULT false,
 input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),request_key text NOT NULL,
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),project_version bigint NOT NULL,
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(workspace_id,project_id,source_id,segment_set_id) REFERENCES platform.deep_dub_segment_sets(workspace_id,project_id,source_id,id),
 FOREIGN KEY(workspace_id,quality_profile_id) REFERENCES platform.deep_dub_quality_profiles(workspace_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,segment_set_id,id),
 UNIQUE(workspace_id,project_id,source_id,quality_profile_id,id),
 UNIQUE(workspace_id,project_id,revision),UNIQUE(workspace_id,project_id,request_key)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_timeline_clips (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 timeline_id uuid NOT NULL,segment_set_id uuid NOT NULL,segment_id uuid NOT NULL,selection_id uuid NOT NULL,
 start_ms bigint NOT NULL CHECK(start_ms>=0),gain numeric NOT NULL DEFAULT 1 CHECK(gain>0 AND gain<=4),
 FOREIGN KEY(workspace_id,project_id,segment_set_id,timeline_id) REFERENCES platform.deep_dub_timeline_revisions(workspace_id,project_id,segment_set_id,id),
 FOREIGN KEY(workspace_id,project_id,segment_set_id,segment_id,selection_id) REFERENCES platform.deep_dub_selections(workspace_id,project_id,segment_set_id,segment_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,timeline_id,segment_id)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_backgrounds (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,source_id uuid NOT NULL,
 revision integer NOT NULL CHECK(revision>0),original_artifact_id uuid NOT NULL,processed_artifact_id uuid NOT NULL,
 removed_artifact_id uuid,model_id text NOT NULL,parameters jsonb NOT NULL CHECK(jsonb_typeof(parameters)='object'),
 input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,source_id) REFERENCES platform.deep_dub_sources(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,original_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,processed_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,removed_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,source_id,id),UNIQUE(workspace_id,source_id,revision)
);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='platform.deep_dub_runs'::regclass AND conname='deep_dub_run_render_binding') THEN
  ALTER TABLE platform.deep_dub_runs ADD CONSTRAINT deep_dub_run_render_binding UNIQUE(workspace_id,project_id,source_id,quality_profile_id,id);
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS platform.deep_dub_renders (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,run_id uuid NOT NULL,
 source_id uuid NOT NULL,timeline_id uuid NOT NULL,quality_profile_id uuid NOT NULL,background_id uuid,
 revision integer NOT NULL CHECK(revision>0),strategy_key text NOT NULL,
 input_hash text NOT NULL CHECK(input_hash ~ '^[a-f0-9]{64}$'),
 dialogue_artifact_id uuid NOT NULL,mix_artifact_id uuid NOT NULL,subtitle_artifact_id uuid NOT NULL,video_artifact_id uuid NOT NULL,
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(workspace_id,project_id,source_id,quality_profile_id,run_id) REFERENCES platform.deep_dub_runs(workspace_id,project_id,source_id,quality_profile_id,id),
 FOREIGN KEY(workspace_id,project_id,source_id,quality_profile_id,timeline_id) REFERENCES platform.deep_dub_timeline_revisions(workspace_id,project_id,source_id,quality_profile_id,id),
 FOREIGN KEY(workspace_id,quality_profile_id) REFERENCES platform.deep_dub_quality_profiles(workspace_id,id),
 FOREIGN KEY(workspace_id,project_id,source_id,background_id) REFERENCES platform.deep_dub_backgrounds(workspace_id,project_id,source_id,id),
 FOREIGN KEY(workspace_id,project_id,dialogue_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,mix_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,subtitle_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,video_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,run_id,revision)
);
CREATE TABLE IF NOT EXISTS platform.deep_dub_audits (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 translation_id uuid,audio_attempt_id uuid,voice_reference_id uuid,emotion_reference_id uuid,
 background_id uuid,timeline_id uuid,render_id uuid,source_id uuid,
 quality_profile_id uuid NOT NULL,rule_key text NOT NULL,rule_revision text NOT NULL,
 subject_hash text NOT NULL CHECK(subject_hash ~ '^[a-f0-9]{64}$'),
 status text NOT NULL CHECK(status IN ('passed','failed','not_measured')),
 method text NOT NULL CHECK(method IN ('human','independent_model','technical')),
 evidence_artifact_id uuid,details jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(details)='object'),
 request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 200),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 created_by uuid NOT NULL REFERENCES auth.users(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(num_nonnulls(translation_id,audio_attempt_id,voice_reference_id,emotion_reference_id,background_id,timeline_id,render_id,source_id)=1),
 CHECK(status='not_measured' OR evidence_artifact_id IS NOT NULL),
 FOREIGN KEY(workspace_id,project_id,translation_id) REFERENCES platform.deep_dub_translations(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,audio_attempt_id) REFERENCES platform.deep_dub_audio_attempts(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,voice_reference_id) REFERENCES platform.deep_dub_voice_references(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,emotion_reference_id) REFERENCES platform.deep_dub_emotion_references(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,background_id) REFERENCES platform.deep_dub_backgrounds(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,timeline_id) REFERENCES platform.deep_dub_timeline_revisions(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,render_id) REFERENCES platform.deep_dub_renders(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,source_id) REFERENCES platform.deep_dub_sources(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,quality_profile_id) REFERENCES platform.deep_dub_quality_profiles(workspace_id,id),
 FOREIGN KEY(workspace_id,project_id,evidence_artifact_id) REFERENCES platform.deep_dub_artifacts(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,project_id,id),UNIQUE(workspace_id,project_id,request_key)
);
-- 审核集合显式建关联表，不用无外键的 UUID 数组承载长期关系。
CREATE TABLE IF NOT EXISTS platform.deep_dub_selection_audits (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,project_id uuid NOT NULL,
 selection_id uuid NOT NULL,audit_id uuid NOT NULL,
 FOREIGN KEY(workspace_id,project_id,selection_id) REFERENCES platform.deep_dub_selections(workspace_id,project_id,id),
 FOREIGN KEY(workspace_id,project_id,audit_id) REFERENCES platform.deep_dub_audits(workspace_id,project_id,id),
 UNIQUE(workspace_id,id),UNIQUE(workspace_id,selection_id,audit_id)
);
CREATE OR REPLACE FUNCTION platform.deep_dub_check_source_window() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE duration bigint;
BEGIN
 SELECT duration_ms INTO duration FROM platform.deep_dub_sources WHERE workspace_id=NEW.workspace_id AND project_id=NEW.project_id AND id=NEW.source_id;
 IF duration IS NULL OR NEW.end_ms>duration THEN RAISE EXCEPTION '原声窗口超出来源时长' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_check_render_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE closed boolean; background_mode text;
BEGIN
 SELECT sealed INTO closed FROM platform.deep_dub_timeline_revisions WHERE workspace_id=NEW.workspace_id AND id=NEW.timeline_id;
 SELECT rules->>'background_mode' INTO background_mode FROM platform.deep_dub_quality_profiles WHERE workspace_id=NEW.workspace_id AND id=NEW.quality_profile_id;
 IF closed IS DISTINCT FROM true THEN RAISE EXCEPTION '成片只能引用已封存时间线' USING ERRCODE='23514'; END IF;
 IF background_mode IS NULL OR (background_mode='preserve' AND NEW.background_id IS NULL) THEN
  RAISE EXCEPTION '成片缺少质量规则要求的背景版本' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_sealable_revision() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
BEGIN
 IF TG_OP='UPDATE' AND NOT OLD.sealed AND NEW.sealed AND (to_jsonb(OLD)-'sealed')=(to_jsonb(NEW)-'sealed') THEN RETURN NEW; END IF;
 RAISE EXCEPTION '修订仅允许一次封存，不能覆盖或重新打开' USING ERRCODE='23514';
END $$;
CREATE OR REPLACE FUNCTION platform.deep_dub_check_open_parent() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,platform AS $$
DECLARE closed boolean;
BEGIN
 IF TG_TABLE_NAME='deep_dub_segments' THEN
  SELECT sealed INTO closed FROM platform.deep_dub_segment_sets WHERE workspace_id=NEW.workspace_id AND id=NEW.segment_set_id FOR SHARE;
 ELSIF TG_TABLE_NAME='deep_dub_segment_cues' THEN
  SELECT x.sealed INTO closed FROM platform.deep_dub_segments s JOIN platform.deep_dub_segment_sets x
   ON (x.workspace_id,x.id)=(s.workspace_id,s.segment_set_id) WHERE s.workspace_id=NEW.workspace_id AND s.id=NEW.segment_id FOR SHARE OF x;
 ELSIF TG_TABLE_NAME='deep_dub_timeline_clips' THEN
  SELECT sealed INTO closed FROM platform.deep_dub_timeline_revisions WHERE workspace_id=NEW.workspace_id AND id=NEW.timeline_id FOR SHARE;
 ELSIF TG_TABLE_NAME='deep_dub_selection_audits' THEN
  SELECT sealed INTO closed FROM platform.deep_dub_selections WHERE workspace_id=NEW.workspace_id AND id=NEW.selection_id FOR SHARE;
 END IF;
 IF closed IS NULL THEN RAISE EXCEPTION '父修订不存在' USING ERRCODE='23503'; END IF;
 IF closed THEN RAISE EXCEPTION '父修订已封存，不能追加成员' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DO $$ DECLARE name text; BEGIN
 DROP TRIGGER IF EXISTS render_snapshot ON platform.deep_dub_renders;
 CREATE TRIGGER render_snapshot BEFORE INSERT ON platform.deep_dub_renders FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_check_render_snapshot();
 FOREACH name IN ARRAY ARRAY['deep_dub_cues','deep_dub_segments','deep_dub_voice_references','deep_dub_emotion_references'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS source_window ON platform.%I',name);
  EXECUTE format('CREATE TRIGGER source_window BEFORE INSERT ON platform.%I FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_check_source_window()',name);
 END LOOP;
 FOREACH name IN ARRAY ARRAY['deep_dub_speakers','deep_dub_cues','deep_dub_segment_sets','deep_dub_segments','deep_dub_segment_cues','deep_dub_translations','deep_dub_voice_references','deep_dub_emotion_references','deep_dub_audio_attempts','deep_dub_selections','deep_dub_timeline_revisions','deep_dub_timeline_clips','deep_dub_backgrounds','deep_dub_renders','deep_dub_audits','deep_dub_selection_audits'] LOOP
  EXECUTE format('ALTER TABLE platform.%I ENABLE ROW LEVEL SECURITY',name);
  EXECUTE format('GRANT SELECT ON platform.%I TO authenticated',name);
  EXECUTE format('REVOKE INSERT,UPDATE,DELETE ON platform.%I FROM authenticated',name);
  EXECUTE format('GRANT ALL ON platform.%I TO service_role',name);
  EXECUTE format('DROP POLICY IF EXISTS deep_dub_read ON platform.%I',name);
  EXECUTE format('CREATE POLICY deep_dub_read ON platform.%I FOR SELECT TO authenticated USING(platform.has_permission(workspace_id,''deep_dub.project.read''))',name);
  IF name<>'deep_dub_speakers' THEN
   EXECUTE format('DROP TRIGGER IF EXISTS immutable_revision ON platform.%I',name);
   IF name=ANY(ARRAY['deep_dub_segment_sets','deep_dub_selections','deep_dub_timeline_revisions']) THEN
    EXECUTE format('CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON platform.%I FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_sealable_revision()',name);
   ELSE
    EXECUTE format('CREATE TRIGGER immutable_revision BEFORE UPDATE OR DELETE ON platform.%I FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_immutable_revision()',name);
   END IF;
  END IF;
 END LOOP;
 FOREACH name IN ARRAY ARRAY['deep_dub_segments','deep_dub_segment_cues','deep_dub_timeline_clips','deep_dub_selection_audits'] LOOP
  EXECUTE format('DROP TRIGGER IF EXISTS parent_open ON platform.%I',name);
  EXECUTE format('CREATE TRIGGER parent_open BEFORE INSERT ON platform.%I FOR EACH ROW EXECUTE FUNCTION platform.deep_dub_check_open_parent()',name);
 END LOOP;
END $$;
NOTIFY pgrst,'reload schema';
