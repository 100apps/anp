"""CLI、HTTP 与 LangGraph 共用对象服务；运行锁防止重复付费生成。"""
from urllib.parse import urlparse
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from anp_business.dubbing import media
from anp_business.dubbing import repository as db
from anp_business.dubbing.settings import local_input, required


def create_run(source, title="视频译配", subtitle=None, request_key=None):
    if not source or len(source) > 4000 or not title or len(title) > 200:
        raise ValueError("需要有效视频地址和项目名称")
    request = {"input": source, "subtitle": subtitle}
    if urlparse(source).scheme not in ("https", "http"):
        request["input_sha256"] = media.digest(local_input(source))
    if subtitle:
        request["subtitle_sha256"] = media.digest(local_input(subtitle))
    workspace = required("ANP_DUB_WORKSPACE_ID")
    request_key = str(UUID(str(request_key))) if request_key else str(uuid4())
    with db.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (workspace + request_key,))
        existing = conn.execute("SELECT * FROM dubbing.runs WHERE workspace_id=%s AND request_key=%s", (workspace, request_key)).fetchone()
        if existing:
            if existing["request"] != request:
                raise ValueError("同一个幂等键不能用于不同输入")
            return existing
        project = conn.execute("INSERT INTO dubbing.projects(workspace_id,title) VALUES (%s,%s) RETURNING *", (workspace, title)).fetchone()
        return conn.execute("INSERT INTO dubbing.runs(workspace_id,project_id,request_key,request) VALUES (%s,%s,%s,%s) RETURNING *",
                             (workspace, project["id"], request_key, Jsonb(request))).fetchone()


def execute(run_id):
    from anp_business.dubbing.graph import graph, now
    run = db.get("runs", run_id)
    with db.connection() as lock:
        acquired = lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS acquired", (str(run_id),)).fetchone()["acquired"]
        if not acquired:
            raise ValueError("本次运行已有执行者")
        try:
            if run["status"] in ("ready_for_review", "approved"):
                return describe(run_id)
            if run["status"] == "cancelled":
                raise ValueError("已取消运行不能恢复生成")
            # 只有明确重试才使上次失联尝试失败；不把未知上游状态记为已取消/已停止。
            for attempt in db.rows("attempts", run_id=str(run_id)):
                if attempt["status"] == "running":
                    db.update("attempts", attempt["id"], status="failed", error="前次执行者失联；上游完成状态未知，显式重试可能重新调用", finished_at=now())
            db.update("runs", run_id, status="running", error=None, started_at=run["started_at"] or now(), finished_at=None)
            try:
                graph.invoke({"run_id": str(run_id)})
                db.update("runs", run_id, status="ready_for_review", current_stage="finish", finished_at=now())
            except Exception as exc:
                if db.get("runs", run_id)["status"] != "cancelled":
                    db.update("runs", run_id, status="failed", error=str(exc)[:500], finished_at=now())
                raise
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (str(run_id),))
    return describe(run_id)


def list_runs():
    return db.rows("runs", workspace_id=required("ANP_DUB_WORKSPACE_ID"))[-100:][::-1]


def describe(run_id):
    return db.snapshot(run_id)


def cancel(run_id):
    run = db.get("runs", run_id)
    if run["status"] in ("ready_for_review", "approved"):
        raise ValueError("已封装的运行不能当作执行中任务取消")
    return db.update("runs", run_id, status="cancelled")
