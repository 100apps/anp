"""新项目真实验收：单镜像、选服、四存储恢复、本地业务和同源平台。只操作 anp3。"""
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
from manage import ROOT, compose


def inspect():
    return json.loads(subprocess.check_output(['docker','inspect','anp3-toolbox-1'],text=True))[0]


def status():
    with urllib.request.urlopen('http://127.0.0.1:'+check.ENV['HEALTH_PORT']+'/health',timeout=5) as response:
        value=json.load(response)
    assert value['status']=='ready'
    assert all(item['state']=='ready' for item in value['services'].values())
    return value


def up(services,data_root=None):
    env=os.environ.copy(); env['ANP3_SERVICES']=services
    if data_root: env['ANP_DATA_ROOT']=data_root
    compose('up','-d','--no-build','--wait','--wait-timeout','900','toolbox',env=env)
    return status()


def benchmark():
    cache,s3=check.resources()
    payload=b'a'*1048576
    with check.postgres() as conn:
        actions={
            'postgres_select1':lambda:conn.execute('SELECT 1').fetchone(),
            'redis_set_get':lambda:(cache.set('anp3:benchmark','value'),cache.get('anp3:benchmark')),
            'clickhouse_sum_100000':lambda:check.ch('SELECT sum(number) FROM numbers(100000)'),
            's3_1mib_put_get':lambda:(s3.put_object(Bucket='anp3-smoke',Key='benchmark.bin',Body=payload),
                                    s3.get_object(Bucket='anp3-smoke',Key='benchmark.bin')['Body'].read()),
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
    output=ROOT/'docs/evidence/anp3-acceptance.json'
    result={'project':'anp3','time':datetime.now(timezone.utc).isoformat(),'passed':False}
    try:
        up('all')
        initial=inspect(); assert initial['Config']['Labels']['com.docker.compose.project']=='anp3'
        assert len(initial['Mounts'])==1 and initial['Mounts'][0]['Destination']=='/data'
        result['image_id']=initial['Image']; result['data_root']=initial['Mounts'][0]['Source']
        result['initial_services']=status()
        assert len(result['initial_services']['selected'])==14
        result['storage_versions']=check.write_storage(); check.read_storage()
        result['storage_read_write']=True
        if args.fresh:
            root=initial['Mounts'][0]['Source'].rstrip('/\\')+'/acceptance-fresh-'+uuid.uuid4().hex[:10]
            try:
                fresh=up('all',data_root=root)
                assert len(fresh['selected'])==14
                assert inspect()['Mounts'][0]['Source']!=initial['Mounts'][0]['Source']
                with check.postgres() as connection:
                    assert connection.execute("SELECT count(*) FROM pg_namespace WHERE nspname='anp3_smoke'").fetchone()[0]==0
                result['fresh_initialization']={'passed':True,'isolated_data_root':root,'services':14}
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
        result['benchmark']=benchmark()
        result['docker_stats']=json.loads(subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}','anp3-toolbox-1'],text=True))
        result['tuning']=json.loads(subprocess.check_output(['docker','exec','anp3-toolbox-1','cat','/data/config/effective/tuning.json'],text=True))
        result['image_size_bytes']=json.loads(subprocess.check_output(['docker','image','inspect',result['image_id']],text=True))[0]['Size']
        result['protected']=check.protected(); result['passed']=True
    finally:
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'evidence':str(output),'services':14},ensure_ascii=False))


if __name__=='__main__': main()
