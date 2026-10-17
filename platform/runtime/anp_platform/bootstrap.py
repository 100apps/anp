"""平台内部初始化任务，不借用第五个存储镜像或额外初始化容器。"""
import json
import hashlib
import re
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
        for name,spec in (settings['databases'] if settings.get('initialize_databases',True) else {}).items():
            role=spec['role']
            if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal(spec['password'])))
            if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone():
                connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(role)))
        connection.execute('CREATE SCHEMA IF NOT EXISTS platform')
        connection.execute('GRANT USAGE ON SCHEMA platform TO anon,authenticated,service_role')
        # 仅预建 REST 暴露的命名空间；业务表及业务迁移仍由业务项目管理。
        for name in settings.get('application_schemas',[]):
            if not re.fullmatch(r'app_[a-z][a-z0-9_]*',name):raise ValueError('无效业务命名空间')
            connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(name)))
    if not settings.get('initialize_buckets',True):return
    s3=boto3.client('s3',endpoint_url=storage['s3_url'],region_name=storage.get('s3_region','us-east-1'),
        aws_access_key_id=storage['s3_access_key'],aws_secret_access_key=storage['s3_secret_key'],
        config=Config(s3={'addressing_style':'path'}))
    existing={item['Name'] for item in s3.list_buckets()['Buckets']}
    for name in ['supabase-storage','langfuse']:
        if name not in existing:s3.create_bucket(Bucket=name)


def schema(settings):
    with psycopg.connect(settings['storage']['postgres_url']) as connection:
        for path in sorted(Path(os.environ.get('ANP_PLATFORM_MIGRATIONS','/opt/anp/migrations')).glob('*.sql')):
            connection.execute(path.read_text(encoding='utf8'))
    provision_identity(settings)


def provision_identity(settings):
    """创建默认管理身份，不重置已有用户的密码；不依赖尚未启动的 REST。"""
    admin=settings.get('bootstrap_admin')
    if not admin:return
    auth_url=settings['services']['auth']['health_url'].removesuffix('/health')
    headers={'Authorization':'Bearer '+admin['service_key'],'apikey':admin['service_key']}
    with psycopg.connect(settings['storage']['postgres_url']) as connection:
        row=connection.execute('SELECT id FROM auth.users WHERE email=%s',(admin['email'],)).fetchone()
        if row:user_id=row[0]
        else:
            with httpx.Client(base_url=auth_url,headers=headers,trust_env=False,timeout=30) as client:
                response=client.post('/admin/users',json={'email':admin['email'],'password':admin['password'],'email_confirm':True})
                response.raise_for_status();user_id=response.json()['id']
        workspace='00000000-0000-4000-8000-000000000001'
        connection.execute('INSERT INTO platform.profiles(user_id,email,display_name) VALUES(%s,%s,%s) ON CONFLICT(user_id) DO NOTHING',
            (user_id,admin['email'],admin['email'].split('@')[0]))
        connection.execute("INSERT INTO platform.members(workspace_id,user_id,role) VALUES(%s,%s,'admin') ON CONFLICT DO NOTHING",(workspace,user_id))
        connection.execute('INSERT INTO platform.admins(user_id) VALUES(%s) ON CONFLICT DO NOTHING',(user_id,))
    storage_url=settings['services']['storage']['health_url'].removesuffix('/status')
    with httpx.Client(base_url=storage_url,headers=headers,trust_env=False,timeout=30) as client:
        response=client.get('/bucket/artifacts')
        if response.status_code in (400,404):
            client.post('/bucket',json={'id':'artifacts','name':'artifacts','public':False}).raise_for_status()
        else:response.raise_for_status()


def configure_dbx(settings):
    """只 upsert 平台拥有的连接，保留用户额外添加的连接。"""
    spec=settings['services']['dbx']
    url=spec['health_url'].removesuffix('/api/auth/check')
    with httpx.Client(base_url=url,trust_env=False,timeout=30) as client:
        client.post('/api/auth/login',json={'password':spec['environment']['DBX_PASSWORD']}).raise_for_status()
        client.post('/api/connection/save',json={'configs':settings['dbx_connections'],'removedIds':[]}).raise_for_status()


def configure_litellm(settings):
    """给 Lens 独立受限的分析 Key；不让它持有网关管理员 Key。"""
    spec=settings['services']['litellm']
    base=spec['health_url'].removesuffix('/health/liveliness')
    key=settings['services']['lens']['environment']['LENS_ANALYSIS_API_KEY']
    with httpx.Client(base_url=base,headers={'Authorization':'Bearer '+spec['environment']['LITELLM_MASTER_KEY']},
        trust_env=False,timeout=30) as client:
        # 查询使用单向摘要，不把原始 Key 写入 URL 或访问日志。
        response=client.get('/key/info',params={'key':hashlib.sha256(key.encode()).hexdigest()})
        if response.status_code==404:
            client.post('/key/generate',json={'key':key,'key_alias':'anp-v3-lens-auto',
                'models':['auto'],'rpm_limit':10,'tpm_limit':20000,
                'metadata':{'purpose':'本机 Lens 手动分析，仅允许 auto'}}).raise_for_status()
        else:
            response.raise_for_status()
            if response.json()['info']['models']!=['auto']:
                raise ValueError('Lens 分析 Key 模型权限与配置不一致')


if __name__=='__main__':
    settings=json.loads(Path(os.environ.get('ANP_PLATFORM_CONFIG','/run/anp/config.json')).read_text())
    databases(settings)
