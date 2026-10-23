"""新项目真实验收：单镜像、选服、四存储恢复、本地业务和同源平台。只操作 anp。"""
import asyncio
import argparse
from datetime import datetime, timezone
import json
import os
import statistics
import subprocess
import time
import urllib.request
import uuid

import check
import check_platforms
import check_platform_operations
import check_realtime
from manage import ROOT, PROJECT, compose


def inspect():
    return json.loads(subprocess.check_output(['docker','inspect',PROJECT+'-toolbox-1'],text=True))[0]


def data_mount(container):
    mounts=[mount for mount in container['Mounts'] if mount['Type']=='bind']
    assert len(mounts)==1 and mounts[0]['Destination']=='/data'
    assert '/data/run' in container['HostConfig'].get('Tmpfs',{})
    return mounts[0]


def status():
    with urllib.request.urlopen('http://127.0.0.1:'+check.ENV['HEALTH_PORT']+'/health',timeout=5) as response:
        value=json.load(response)
    assert value['status']=='ready'
    assert all(item['state']=='ready' for item in value['services'].values())
    return value


def up(services,data_root=None):
    env=os.environ.copy(); env['ANP_SERVICES']=services
    if data_root: env['ANP_DATA_ROOT']=data_root
    compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox',env=env)
    return status()


def benchmark():
    cache,s3=check.resources()
    payload=b'a'*1048576
    with check.postgres() as conn:
        actions={
            'postgres_select1':lambda:conn.execute('SELECT 1').fetchone(),
            'redis_set_get':lambda:(cache.set('anp:benchmark','value'),cache.get('anp:benchmark')),
            'clickhouse_sum_100000':lambda:check.ch('SELECT sum(number) FROM numbers(100000)'),
            's3_1mib_put_get':lambda:(s3.put_object(Bucket='anp-smoke',Key='benchmark.bin',Body=payload),
                                    s3.get_object(Bucket='anp-smoke',Key='benchmark.bin')['Body'].read()),
        }
        values={}
        for name,action in actions.items():
            samples=[]
            for _ in range(20):
                started=time.perf_counter(); observed=action(); samples.append((time.perf_counter()-started)*1000)
                if name=='postgres_select1': assert observed==(1,)
                elif name=='redis_set_get': assert observed==(True,'value')
                elif name=='clickhouse_sum_100000': assert observed=='4999950000'
                else: assert observed[1]==payload
            values[name]={'samples':20,'p50_ms':round(statistics.median(samples),2),
                          'p95_ms':round(sorted(samples)[18],2)}
    return {'scope':'单客户端本机烟测，不能代表生产容量或线上最优参数','results':values}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fresh',action='store_true',help='额外使用数据根内的全新子目录验证首次初始化')
    args=parser.parse_args()
    output=ROOT/'var/evidence/anp-acceptance.json'
    result={'project':'anp','compose_project':PROJECT,'time':datetime.now(timezone.utc).isoformat(),'passed':False}
    try:
        up('all')
        initial=inspect(); assert initial['Config']['Labels']['com.docker.compose.project']==PROJECT
        mount=data_mount(initial)
        result['image_id']=initial['Image']; result['data_root']=mount['Source']
        result['runtime_tmpfs']=True
        result['initial_services']=status()
        assert len(result['initial_services']['selected'])==15
        result['storage_versions']=check.write_storage(); check.read_storage()
        result['storage_read_write']=True
        expected=json.loads((ROOT/'tests/baselines/postgres-extensions-baseline.json').read_text(encoding='utf-8'))
        with check.postgres() as connection:
            actual=dict(connection.execute('SELECT name,default_version FROM pg_available_extensions').fetchall())
        missing=sorted(set(dict(expected))-set(actual))
        assert not missing,'缺少原镜像PostgreSQL扩展：'+str(missing)
        changed={name:{'expected':version,'actual':actual[name]} for name,version in expected if name in actual and actual[name]!=version}
        result['postgres_extensions']={'baseline_count':len(expected),'available_count':len(actual),'version_changes':changed,'all_preserved':not changed}
        assert not changed,'PostgreSQL扩展版本发生变化：'+str(changed)
        modules=check.resources()[0].execute_command('MODULE','LIST')
        names=[(m if isinstance(m,dict) else dict(zip(m[::2],m[1::2])))['name'] for m in modules]
        assert 'vectorset' in names,'Redis缺少内置vectorset'
        result['redis_builtin_modules']=names
        if args.fresh:
            root=mount['Source'].rstrip('/\\')+'/acceptance-fresh-'+uuid.uuid4().hex[:10]
            try:
                fresh=up('all',data_root=root)
                assert len(fresh['selected'])==15
                assert data_mount(inspect())['Source']!=mount['Source']
                with check.postgres() as connection:
                    assert connection.execute("SELECT count(*) FROM pg_namespace WHERE nspname='anp_smoke'").fetchone()[0]==0
                result['fresh_initialization']={'passed':True,'isolated_data_root':root,'services':15}
            finally:
                up('all')
            check.read_storage(); result['original_data_after_fresh_test']=True
        compose('restart','toolbox'); up('all'); check.read_storage()
        result['storage_after_restart']=True
        try:
            selected=up('redis'); assert selected['selected']==['redis']
            assert check.resources()[0].ping()
            try:
                with check.postgres() as conn: conn.execute('SELECT 1')
            except Exception: pass
            else: raise AssertionError('只选Redis时不应启动PostgreSQL')
            result['redis_only']=True
        finally:
            up('all')
        check.read_storage(); result['storage_after_selection_restore']=True
        current=inspect(); assert current['Image']==initial['Image'] and current['Mounts']==initial['Mounts']
        result['same_image_and_mount']=True
        result['demos']=asyncio.run(check.demos())
        result['demo_mode']='离线确定性模型；执行真实 LangGraph/Deep Agents 图与工具调用，不计作供应商模型验收'
        result['platforms']=asyncio.run(check_platforms.check('http://127.0.0.1:51760',True))
        result['platform_operations']=check_platform_operations.check()
        result['realtime']=asyncio.run(check_realtime.verify())
        result['benchmark']=benchmark()
        result['docker_stats']=json.loads(subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}',PROJECT+'-toolbox-1'],text=True))
        result['tuning']=json.loads(subprocess.check_output(['docker','exec',PROJECT+'-toolbox-1','cat','/data/config/effective/tuning.json'],text=True))
        result['image_size_bytes']=json.loads(subprocess.check_output(['docker','image','inspect',result['image_id']],text=True))[0]['Size']
        result['protected']=check.protected(); result['passed']=True
    finally:
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'evidence':str(output),'services':15},ensure_ascii=False))


if __name__=='__main__': main()
