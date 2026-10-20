"""Supabase PostgreSQL 直连适配器：每次事务绑定可信用户并执行 RLS。"""
import json
from contextlib import contextmanager
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from anp_business.dubbing.settings import required

TABLES = {"workspaces", "memberships", "projects", "assets", "speakers", "segments", "translations",
          "voices", "runs", "attempts", "syntheses", "reviews", "deliverables"}


def actor() -> str:
    return str(UUID(required("ANP_DUB_ACTOR_ID")))


@contextmanager
def connection():
    with psycopg.connect(required("ANP_DUB_DATABASE_URL"), row_factory=dict_row) as conn:
        conn.execute("SET LOCAL ROLE authenticated")
        conn.execute("SELECT set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": actor(), "role": "authenticated"}),))
        conn.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (actor(),))
        yield conn


def table(name):
    if name not in TABLES:
        raise ValueError("不支持的对象类型")
    return sql.Identifier("dubbing", name)


def adapt(value):
    return Jsonb(value) if isinstance(value, (dict, list)) else value


def insert(name, **values):
    with connection() as conn:
        return conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            table(name), sql.SQL(",").join(map(sql.Identifier, values)),
            sql.SQL(",").join(sql.Placeholder() for _ in values)), tuple(map(adapt, values.values()))).fetchone()


def rows(name, **filters):
    with connection() as conn:
        query = sql.SQL("SELECT * FROM {} WHERE {}").format(table(name),
            sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in filters) if filters else sql.SQL("true"))
        return conn.execute(query + sql.SQL(" ORDER BY created_at,id"), tuple(filters.values())).fetchall()


def get(name, identifier):
    result = rows(name, id=str(UUID(str(identifier))))
    if not result:
        raise LookupError("对象不存在或不属于当前用户")
    return result[0]


def update(name, identifier, **values):
    with connection() as conn:
        row = conn.execute(sql.SQL("UPDATE {} SET {} WHERE id=%s RETURNING *").format(table(name),
            sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in values)),
            (*map(adapt, values.values()), str(identifier))).fetchone()
        if not row:
            raise LookupError("对象不存在或不可修改")
        return row


def snapshot(run_id):
    run = get("runs", run_id)
    project = get("projects", run["project_id"])
    result = {"run": run, "project": project}
    for name in ("assets", "speakers", "segments", "translations", "voices"):
        result[name] = rows(name, project_id=str(project["id"]))
    for name in ("attempts", "syntheses", "reviews", "deliverables"):
        result[name] = rows(name, run_id=str(run["id"]))
    return result
