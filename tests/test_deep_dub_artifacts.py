"""独立库验证私有对象策略；storage.objects 行为元数据夹具，不冒充实际 S3 内容。"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from anp import ROOT
from tests.test_deep_dub_foundation import database,actor,pytestmark
from tests.test_deep_dub_versions import versions,seed
from tests.test_deep_dub_execution import executions,work,begin


@pytest.fixture(scope='module')
def artifacts(executions):
    with executions[0]() as c:
        for _ in range(2):
            c.execute((ROOT/'apps/deep_dub/migrations/008_private_artifacts.sql').read_text(encoding='utf8'),prepare=False)
    return executions


@pytest.fixture
def item(artifacts,work):
    s=work
    with s['connect']() as c:
        actor(c,s['created_by']);attempt=begin(c,s,kind=None)
        c.execute("SELECT id FROM platform.deep_dub_mark_stage(%s,%s,'started')",(s['workspace_id'],attempt['id']))
    digest=sha256(b'fixture bytes').hexdigest()
    path=f"{s['workspace_id']}/{s['project']}/{attempt['id']}/{digest}"
    return {**s,'attempt':attempt['id'],'path':path,'digest':digest}


def register(c,s,size=13,media='application/json'):
    return c.execute('SELECT id,object_path,sha256 FROM platform.deep_dub_register_artifact(%s,%s,%s,%s,%s,NULL,NULL)',
        (s['workspace_id'],s['attempt'],s['digest'],size,media)).fetchone()


def metadata(c,s):
    c.execute('INSERT INTO storage.objects(bucket_id,name,metadata) VALUES(%s,%s,%s)',
              ('deep-dub',s['path'],Jsonb({'size':13})))


def test_private_bucket_insert_only_and_workspace_paths(item):
    s=item
    with s['connect']() as c:
        actor(c,s['created_by']);metadata(c,s)
        assert c.execute("SELECT platform.deep_dub_object_access(%s,true)",(s['path'],)).fetchone()[0]
        for path in ['../escape',s['path']+'/child',s['path'].replace(str(s['project']),str(uuid4()))]:
            assert not c.execute('SELECT platform.deep_dub_object_access(%s,true)',(path,)).fetchone()[0]
        assert c.execute("UPDATE storage.objects SET metadata=%s WHERE bucket_id='deep-dub' AND name=%s RETURNING name",(Jsonb({'size':999}),s['path'])).fetchall()==[]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():
                c.execute("DELETE FROM storage.objects WHERE bucket_id='deep-dub' AND name=%s RETURNING name",(s['path'],))


def test_missing_and_mismatched_object_cannot_be_registered(item):
    s=item
    with s['connect']() as c:
        actor(c,s['created_by'])
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():register(c,s)
        metadata(c,s)
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():register(c,s,size=999)
        first=register(c,s);assert first==register(c,s)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with c.transaction():register(c,s,media='text/plain')


def test_concurrent_register_returns_same_immutable_record(item):
    s=item
    with s['connect']() as c:
        actor(c,s['created_by']);metadata(c,s)
    def submit(_):
        with s['connect']() as c:
            actor(c,s['created_by']);return register(c,s)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(submit,range(12)))
    assert all(x==results[0] for x in results)


def test_cancelled_attempt_and_viewer_cannot_upload(item,artifacts):
    s=item
    with s['connect']() as c:
        actor(c,s['created_by']);c.execute('SELECT id FROM platform.deep_dub_cancel_run(%s,%s)',(s['workspace_id'],s['run']))
        assert not c.execute('SELECT platform.deep_dub_object_access(%s,true)',(s['path'],)).fetchone()[0]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():metadata(c,s)
        c.execute('RESET ROLE');actor(c,artifacts[1][1])
        assert not c.execute('SELECT platform.deep_dub_object_access(%s,true)',(s['path'],)).fetchone()[0]
