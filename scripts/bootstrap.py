"""显式初始化所选 PG 和 S3。不会由 dev/up 隐式执行。"""
import os

import psycopg
from check import ENV, resources
from psycopg import sql

url = os.getenv('ANP_BOOTSTRAP_DATABASE_URL')
params = {} if url else {'host': '127.0.0.1', 'port': int(ENV['PG_PORT']), 'user': 'supabase_admin',
                       'password': ENV['POSTGRES_PASSWORD'], 'dbname': 'postgres'}
with psycopg.connect(url or '', autocommit=True, **params) as conn:
    for role in ('authenticator', 'pgbouncer', 'supabase_auth_admin', 'supabase_storage_admin'):
        conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(role), sql.Literal(ENV['POSTGRES_PASSWORD'])))
    for name in ('langfuse', 'litellm', 'agent_runtime'):
        if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (name,)).fetchone():
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(name), sql.Literal(ENV['POSTGRES_PASSWORD'])))
        if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone():
            conn.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name), sql.Identifier(name)))
            conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
    conn.execute(sql.SQL('ALTER DATABASE postgres SET "app.settings.jwt_secret" TO {}').format(sql.Literal(ENV['JWT_SECRET'])))
    conn.execute('ALTER DATABASE postgres SET "app.settings.jwt_exp" TO \'3600\'')
_, s3 = resources()
existing = {item['Name'] for item in s3.list_buckets()['Buckets']}
for name in ('langfuse', 'supabase'):
    if name not in existing:
        s3.create_bucket(Bucket=name)
print('服务数据库、角色密码及对象桶初始化完成；平台自身迁移仍由对应上游入口执行。')
