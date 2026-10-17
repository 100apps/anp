"""验证云 URL、服务账号与 TLS 参数的传播，不连接或打印真实云凭据。"""
import copy
from pathlib import Path
import sys
from urllib.parse import urlsplit
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from platform_bindings import bind,expand,relocate
from bundle_platform import validate_release


def configuration():
    names=['auth','storage','postgrest','meta','studio','langfuse-web','langfuse-worker','litellm','lens']
    result={'services':{name:{'environment':{}} for name in names},
        'storage':{'postgres_url':'postgresql://admin:old@postgres/postgres','s3_url':'http://rustfs:9000',
            's3_access_key':'local-access','s3_secret_key':'local-secret'},'dbx_connections':[]}
    for name,key in [('auth','GOTRUE_DB_DATABASE_URL'),('storage','DATABASE_URL'),('postgrest','PGRST_DB_URI'),
        ('langfuse-web','DATABASE_URL'),('langfuse-worker','DATABASE_URL'),('litellm','DATABASE_URL')]:
        result['services'][name]['environment'][key]='postgresql://service:p%40ss@postgres:5432/app'
    for driver,db in [('postgres','langfuse'),('redis','0'),('clickhouse','default')]:
        result['dbx_connections'].append({'db_type':driver,'database':db,'username':'service','password':'p@ss'})
    return result


def test_cloud_addresses_reach_all_consumers_and_dbx_with_tls():
    config=configuration()
    document={'storage':{
        'postgres_container_url':'${CLOUD_PG_URL}',
        'redis_container_url':'rediss://default:r%40p@redis.example:6380/4',
        'clickhouse_container_url':'https://reader:c%40p@ch.example',
        'clickhouse_migration_url':'clickhouses://ch.example:9440',
        's3_container_url':'https://s3.example','s3_access_key':'cloud-access','s3_secret_key':'cloud-secret',
        'initialize_databases':False,'initialize_buckets':False},
        'postgres':{'urls':{'langfuse':'postgresql://lf:private@pool.example:6543/lf?sslmode=verify-full'}}}
    bind(config,document,{'CLOUD_PG_URL':'postgresql://owner:admin@pg.example:5433/postgres?sslmode=verify-full','CH_PASSWORD':'dummy'})
    env=lambda name:config['services'][name]['environment']
    assert urlsplit(env('auth')['GOTRUE_DB_DATABASE_URL']).hostname=='pg.example'
    assert env('langfuse-web')['DATABASE_URL']==env('langfuse-worker')['DATABASE_URL']
    assert urlsplit(env('langfuse-web')['DATABASE_URL']).hostname=='pool.example'
    assert env('meta')['PG_META_DB_SSL_MODE']=='verify-full'
    assert env('studio')['POSTGRES_HOST']=='pg.example'
    assert env('langfuse-worker')['REDIS_TLS_ENABLED']=='true'
    assert env('langfuse-web')['CLICKHOUSE_MIGRATION_URL']=='clickhouses://ch.example:9440'
    assert env('lens')['CLICKHOUSE_URL']=='https://reader:c%40p@ch.example'
    assert env('storage')['GLOBAL_S3_ENDPOINT']=='https://s3.example'
    assert env('langfuse-worker')['LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY']=='cloud-secret'
    pg,redis,ch=config['dbx_connections']
    assert (pg['host'],pg['username'],pg['ssl'])==('pool.example','lf',True)
    assert (redis['host'],redis['password'],redis['ssl'])==('redis.example','r@p',True)
    assert (ch['host'],ch['port'],ch['ssl'])==('ch.example',443,True)
    assert not config['initialize_databases'] and not config['initialize_buckets']


def test_explicit_binding_never_leaves_unresolved_credentials_or_guesses_native_ch_port():
    with pytest.raises(ValueError,match='缺少配置变量：CLOUD_PG_URL'):
        expand('${CLOUD_PG_URL}',{})
    with pytest.raises(ValueError,match='clickhouse_migration_url'):
        bind(configuration(),{'storage':{'clickhouse_container_url':'https://ch.example'}},{'CH_PASSWORD':'dummy'})
    original='postgresql://service:p%40ss@local:5432/db'
    relocated=relocate(original,'postgresql://owner:admin@[::1]:5433/postgres?sslmode=verify-full')
    assert relocated=='postgresql://service:p%40ss@[::1]:5433/db?sslmode=verify-full'


def test_no_binding_preserves_default_endpoints():
    config=configuration();old=copy.deepcopy(config)
    bind(config,{}, {})
    assert config['services']==old['services']
    assert config['storage']==old['storage']


def test_formal_image_cannot_omit_agent_engine_even_when_other_components_exist():
    metadata={'required_components':['auth','dbx','agent'],'components':{'auth':{},'dbx':{}}}
    with pytest.raises(RuntimeError,match='agent'):validate_release(metadata,False)
    validate_release(metadata,True)
