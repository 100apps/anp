"""平台内部初始化任务，不借用第五个存储镜像或额外初始化容器。"""
import json
import os
from pathlib import Path
import psycopg
from psycopg import sql
import boto3
import httpx
from botocore.config import Config


def databases(settings):
    storage=settings['storage']
    with psycopg.connect(storage['postgres_url'],autocommit=True) as connection:
        for name,spec in settings['databases'].items():
            role=spec['role']
            if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(spec['password'])))
            if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
                connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(role)))
        connection.execute('CREATE SCHEMA IF NOT EXISTS platform')
        connection.execute('GRANT USAGE ON SCHEMA platform TO anon,authenticated,service_role')
    s3=boto3.client('s3',endpoint_url=storage['s3_url'],region_name='us-east-1',
        aws_access_key_id=storage['s3_access_key'],aws_secret_access_key=storage['s3_secret_key'],
        config=Config(s3={'addressing_style':'path'}))
    existing={item['Name'] for item in s3.list_buckets()['Buckets']}
    for name in ['supabase-storage','langfuse']:
        if name not in existing:s3.create_bucket(Bucket=name)


def schema(settings):
    with psycopg.connect(settings['storage']['postgres_url']) as connection:
        for path in sorted(Path('/opt/anp/migrations').glob('*.sql')):
            connection.execute(path.read_text(encoding='utf8'))


def configure_dbx(settings):
    """只 upsert 平台拥有的连接，保留用户额外添加的连接。"""
    spec=settings['services']['dbx']
    url=spec['health_url'].removesuffix('/api/auth/check')
    with httpx.Client(base_url=url,trust_env=False,timeout=30) as client:
        client.post('/api/auth/login',json={'password':spec['environment']['DBX_PASSWORD']}).raise_for_status()
        client.post('/api/connection/save',json={'configs':settings['dbx_connections'],'removedIds':[]}).raise_for_status()


if __name__=='__main__':
    settings=json.loads(Path(os.environ.get('ANP_PLATFORM_CONFIG','/run/anp/config.json')).read_text())
    databases(settings)
