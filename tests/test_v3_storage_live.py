"""仅在显式开启的 V3 隔离端口环境执行，不连接旧版数据库。"""
import json
import os
import uuid
import subprocess
import time
import pytest
import httpx
import psycopg
import redis
import boto3
from botocore.config import Config
from anp.config import secret

pytestmark=pytest.mark.skipif(os.environ.get('ANP_V3_STORAGE_SMOKE')!='1',reason='需要显式开启 V3 存储隔离验收')


def test_four_stores_real_roundtrip_and_embedded_keeper():
    marker=str(uuid.uuid4())
    with psycopg.connect(host='127.0.0.1',port=54323,dbname='postgres',user='supabase_admin',password=secret('POSTGRES_PASSWORD')) as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE IF NOT EXISTS public.anp_v3_storage_probe (id text PRIMARY KEY, payload jsonb NOT NULL)')
            cursor.execute('INSERT INTO public.anp_v3_storage_probe VALUES (%s,%s)',(marker,json.dumps({'marker':marker})))
            cursor.execute('SELECT payload FROM public.anp_v3_storage_probe WHERE id=%s',(marker,))
            assert cursor.fetchone()[0]['marker']==marker
    cache=redis.Redis(host='127.0.0.1',port=6381,password=secret('REDIS_PASSWORD'),decode_responses=True)
    cache.set('anp:v3:smoke:'+marker,marker,ex=300)
    assert cache.get('anp:v3:smoke:'+marker)==marker
    with httpx.Client(base_url='http://127.0.0.1:8125',auth=('anp',secret('CH_PASSWORD')),trust_env=False,timeout=30) as client:
        for sql in ['CREATE DATABASE IF NOT EXISTS anp_probe',
            "CREATE TABLE IF NOT EXISTS anp_probe.keeper_probe (key String, value String) ENGINE=KeeperMap('/anp/probe/keeper') PRIMARY KEY key",
            f"INSERT INTO anp_probe.keeper_probe VALUES ('{marker}','{marker}')"]:
            response=client.post('/',content=sql);response.raise_for_status()
        response=client.post('/',content=f"SELECT value FROM anp_probe.keeper_probe WHERE key='{marker}' FORMAT JSONEachRow")
        response.raise_for_status()
        assert json.loads(response.text)['value']==marker
    storage=boto3.client('s3',endpoint_url='http://127.0.0.1:9002',region_name='us-east-1',
        aws_access_key_id=secret('S3_ACCESS_KEY'),aws_secret_access_key=secret('S3_SECRET_KEY'),
        config=Config(s3={'addressing_style':'path'}))
    if not any(b['Name']=='anp-v3-probe' for b in storage.list_buckets()['Buckets']):
        storage.create_bucket(Bucket='anp-v3-probe')
    storage.put_object(Bucket='anp-v3-probe',Key=marker,Body=marker.encode())
    assert storage.get_object(Bucket='anp-v3-probe',Key=marker)['Body'].read().decode()==marker
    if os.environ.get('ANP_V3_RESTART_STORES')=='1':
        names=['anp-storage-'+name+'-1' for name in ('postgres','redis','clickhouse','rustfs')]
        containers=json.loads(subprocess.check_output(['docker','inspect',*names],text=True,encoding='utf8'))
        # 只能重启本轮显式创建的隔离烟测卷，不能误操作未来正式 V3 或旧 V2。
        for container in containers:
            volumes=[m['Name'] for m in container['Mounts'] if m['Type']=='volume']
            assert volumes and all(v.startswith('anp_v3_smoke_') for v in volumes)
        subprocess.run(['docker','restart',*names],check=True,capture_output=True)
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            state=subprocess.check_output(['docker','inspect','--format','{{.State.Health.Status}}',*names],text=True)
            if state.split()==['healthy']*4:break
            time.sleep(1)
        else:pytest.fail('隔离存储重启后未恢复健康')
        with psycopg.connect(host='127.0.0.1',port=54323,dbname='postgres',user='supabase_admin',password=secret('POSTGRES_PASSWORD')) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT payload FROM public.anp_v3_storage_probe WHERE id=%s',(marker,))
                assert cursor.fetchone()[0]['marker']==marker
        assert cache.get('anp:v3:smoke:'+marker)==marker
        with httpx.Client(base_url='http://127.0.0.1:8125',auth=('anp',secret('CH_PASSWORD')),trust_env=False) as client:
            response=client.post('/',content=f"SELECT value FROM anp_probe.keeper_probe WHERE key='{marker}' FORMAT JSONEachRow")
            response.raise_for_status()
            assert json.loads(response.text)['value']==marker
        assert storage.get_object(Bucket='anp-v3-probe',Key=marker)['Body'].read().decode()==marker
