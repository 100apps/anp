"""在独立 PostgreSQL 临时库真实验证迁移、权限与跨空间关系；原业务库不写入。"""
from pathlib import Path
from uuid import uuid4
import json
import os
import subprocess
import tomllib
from string import Template

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from anp import ROOT
from anp.config import secret

pytestmark = pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SMOKE') != '1', reason='需显式开启独立库验收')
MIGRATION = ROOT/'apps/deep_dub/migrations/001_foundation.sql'


@pytest.fixture(scope='module')
def database():
    deployment = tomllib.loads((ROOT/'config/deployment.local.toml').read_text())
    source = Template(deployment['storage']['postgres_host_url']).substitute(POSTGRES_PASSWORD=secret('POSTGRES_PASSWORD'))
    name = 'anp_deep_dub_test_'+uuid4().hex
    backup = json.loads((ROOT/'.local/v3-latest-postgres-backup.json').read_text())
    archive = ROOT/backup['backup_directory']/next(x['file'] for x in backup['databases'] if x['database']=='postgres')
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with archive.open('rb') as data:
            restored = subprocess.run(['docker','exec','-i','anp-storage-postgres-1','pg_restore',
                '--exit-on-error','--username','supabase_admin','--dbname',name], stdin=data, capture_output=True)
        assert restored.returncode == 0, '独立库恢复失败，原始诊断不输出凭据'
        target = make_conninfo(source, dbname=name)
        with psycopg.connect(target) as connection:
            connection.execute(MIGRATION.read_text(), prepare=False)
            connection.execute(MIGRATION.read_text(), prepare=False)
            users = [x[0] for x in connection.execute('SELECT id FROM auth.users ORDER BY id LIMIT 2')]
            assert len(users) == 2
            workspaces = [uuid4(), uuid4()]
            for workspace in workspaces:
                connection.execute('INSERT INTO platform.workspaces(id,name) VALUES(%s,%s)', (workspace,'deep_dub 独立库验收'))
            connection.execute("INSERT INTO platform.members VALUES(%s,%s,'developer'),(%s,%s,'viewer'),(%s,%s,'developer')",
                               (workspaces[0],users[0],workspaces[0],users[1],workspaces[1],users[1]))
        def connect():
            return psycopg.connect(target)
        yield connect,users,workspaces
    finally:
        assert name.startswith('anp_deep_dub_test_') and len(name)==50
        with psycopg.connect(source, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def actor(connection, user):
    connection.execute('SET LOCAL ROLE authenticated')
    connection.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(user),))


def create(connection, workspace, key, name='新译配项目'):
    return connection.execute('SELECT id FROM platform.deep_dub_create_project(%s,%s,%s,%s)',
        (workspace,name,'https://www.youtube.com/watch?v=QXXp1bHd6gI',key)).fetchone()[0]


def test_migration_is_repeatable_and_all_foundation_tables_have_rls(database):
    connect,_,_ = database
    with connect() as connection:
        rows = connection.execute("SELECT relname,relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='platform' AND relname LIKE 'deep_dub_%%' AND relkind='r'").fetchall()
        assert len(rows)==7 and all(enabled for _,enabled in rows)
        assert not connection.execute("SELECT has_function_privilege('anon','platform.deep_dub_create_project(uuid,text,text,text)','EXECUTE')").fetchone()[0]


def test_rpc_idempotence_and_payload_conflict(database):
    connect,users,workspaces = database
    with connect() as connection:
        actor(connection,users[0]);key=str(uuid4())
        first=create(connection,workspaces[0],key)
        assert create(connection,workspaces[0],key)==first
        with pytest.raises(psycopg.errors.UniqueViolation):
            with connection.transaction(): create(connection,workspaces[0],key,'内容改变')


def test_concurrent_requests_only_create_one_project(database):
    from concurrent.futures import ThreadPoolExecutor
    connect,users,workspaces=database
    key=str(uuid4())
    def submit(_):
        with connect() as connection:
            actor(connection,users[0])
            return create(connection,workspaces[0],key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers=list(pool.map(submit,range(12)))
    assert len(set(identifiers))==1


def test_rls_viewer_and_cross_workspace_cannot_create_or_read(database):
    connect,users,workspaces = database
    key=str(uuid4())
    with connect() as connection:
        actor(connection,users[0]);identifier=create(connection,workspaces[0],key)
    with connect() as connection:
        actor(connection,users[1])
        assert connection.execute('SELECT id FROM platform.deep_dub_projects WHERE id=%s',(identifier,)).fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction(): create(connection,workspaces[0],str(uuid4()))
        other=create(connection,workspaces[1],str(uuid4()))
    with connect() as connection:
        actor(connection,users[0])
        assert connection.execute('SELECT id FROM platform.deep_dub_projects WHERE id=%s',(other,)).fetchone() is None
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction(): create(connection,workspaces[1],str(uuid4()))


def test_direct_writes_cannot_bypass_project_rpc(database):
    connect,users,workspaces=database
    with connect() as connection:
        actor(connection,users[0])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                connection.execute('INSERT INTO platform.deep_dub_projects(workspace_id,created_by,name,source_url,request_key) VALUES(%s,%s,%s,%s,%s)',
                    (workspaces[0],users[0],'绕过','source',str(uuid4())))


def test_artifact_project_compound_foreign_key_and_immutable_versions(database):
    connect,users,workspaces=database
    with connect() as connection:
        actor(connection,users[0]);project=create(connection,workspaces[0],str(uuid4()))
        connection.execute('RESET ROLE')
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute('INSERT INTO platform.deep_dub_artifacts(workspace_id,project_id,object_path,sha256,bytes,media_type,created_by) VALUES(%s,%s,%s,%s,1,%s,%s)',
                    (workspaces[1],project,'private/probe','a'*64,'audio/wav',users[0]))
        identifier=connection.execute('INSERT INTO platform.deep_dub_artifacts(workspace_id,project_id,object_path,sha256,bytes,media_type,created_by) VALUES(%s,%s,%s,%s,1,%s,%s) RETURNING id',
            (workspaces[0],project,'private/'+str(uuid4()),'a'*64,'audio/wav',users[0])).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction(): connection.execute('UPDATE platform.deep_dub_artifacts SET sha256=%s WHERE id=%s',('b'*64,identifier))


def test_authenticated_cannot_backfill_production_clock(database):
    connect,users,_=database
    with connect() as connection:
        actor(connection,users[0])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction(): connection.execute('UPDATE platform.deep_dub_runs SET queued_at=now()')


def test_dubbing_run_cannot_reuse_a_demo_task(database):
    connect,users,workspaces=database
    with connect() as connection:
        actor(connection,users[0]);project=create(connection,workspaces[0],str(uuid4()))
        connection.execute('RESET ROLE')
        task=connection.execute("INSERT INTO platform.tasks(workspace_id,created_by,app_id,title,input,request_key) VALUES(%s,%s,'demo','隔离验收','{}',%s) RETURNING id",
            (workspaces[0],users[0],str(uuid4()))).fetchone()[0]
        profile=connection.execute("INSERT INTO platform.deep_dub_quality_profiles(workspace_id,profile_key,revision,rules,rules_hash,provenance,created_by) VALUES(%s,'high',1,'{}',%s,'隔离验收',%s) RETURNING id",
            (workspaces[0],'a'*64,users[0])).fetchone()[0]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with connection.transaction():
                connection.execute("INSERT INTO platform.deep_dub_runs(workspace_id,project_id,task_id,quality_profile_id,flow_revision,input_hash,request_key,mode,created_by) VALUES(%s,%s,%s,%s,'test',%s,%s,'local',%s)",
                    (workspaces[0],project,task,profile,'a'*64,str(uuid4()),users[0]))
