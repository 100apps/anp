-- 任务的身份与原始输入不可通过直接调用 REST 篡改。
CREATE OR REPLACE FUNCTION platform.guard_task_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path=platform,pg_temp AS $$
BEGIN
 IF (NEW.id,NEW.workspace_id,NEW.app_id,NEW.created_by,NEW.input,NEW.request_key)
 IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.app_id,OLD.created_by,OLD.input,OLD.request_key)
 THEN RAISE EXCEPTION '任务身份和原始输入不可修改' USING ERRCODE='42501'; END IF;
 RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS task_identity ON platform.tasks;
CREATE TRIGGER task_identity BEFORE UPDATE ON platform.tasks
FOR EACH ROW EXECUTE FUNCTION platform.guard_task_identity();
REVOKE ALL ON FUNCTION platform.guard_task_identity() FROM PUBLIC;
