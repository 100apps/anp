"""经单平台入口测量 DBX 的只读查询；不修改业务表或保存连接配置。"""
import argparse
import asyncio
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

from anp.config import secret,value
from anp.sdk.http import async_client


QUERIES={
    'constant':('SELECT 1 AS value',1,1),
    'aggregate':('SELECT sum(i)::bigint AS value FROM generate_series(1,10000) AS t(i)',1,50005000),
    'rows':('SELECT i::integer AS value FROM generate_series(1,1000) AS t(i)',1000,1000),
}


def metadata():
    script="import json;p=json.load(open('/opt/anp/components.json'))['components']['dbx'];print(json.dumps({k:p.get(k) for k in ['revision','image']}))"
    result=subprocess.run(['docker','exec','anp-platform-platform-1','python','-c',script],capture_output=True,text=True,timeout=15)
    if result.returncode:return {'available':False}
    return json.loads(result.stdout)


async def measure(count,concurrencies):
    async with async_client(base_url=value('dbx','url'),trust_env=False,timeout=30) as client:
        response=await client.post('/api/auth/login',json={'password':secret('DBX_PASSWORD')})
        response.raise_for_status()
        response=await client.get('/api/connection/list');response.raise_for_status()
        config=next(item for item in response.json() if item['id']=='anp-postgres')
        config['driver_profile']=config.get('driver_profile') or config['db_type']
        response=await client.post('/api/connection/connect',json={'config':config});response.raise_for_status()
        results=[]
        for name,(sql,rows,last) in QUERIES.items():
            async def query():
                started=time.perf_counter()
                response=await client.post('/api/query/execute',json={
                    'connectionId':'anp-postgres','database':'postgres','sql':sql,
                    'maxRows':1000,'timeoutSecs':10})
                response.raise_for_status();data=response.json()
                if len(data['rows'])!=rows or int(data['rows'][-1][0])!=last or data.get('truncated'):
                    raise RuntimeError('只读查询结果校验失败：'+name)
                return (time.perf_counter()-started)*1000
            for _ in range(3):await query()
            for concurrency in concurrencies:
                semaphore=asyncio.Semaphore(concurrency)
                async def bounded():
                    async with semaphore:return await query()
                started=time.perf_counter()
                samples=await asyncio.gather(*(bounded() for _ in range(count)))
                wall=time.perf_counter()-started
                results.append({'query':name,'concurrency':concurrency,'count':count,
                    'median_ms':round(statistics.median(samples),3),
                    'p95_ms':round(sorted(samples)[math.ceil(len(samples)*.95)-1],3),
                    'wall_seconds':round(wall,4),'requests_per_second':round(count/wall,2),
                    'samples_ms':[round(item,4) for item in samples]})
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count',type=int,default=30)
    parser.add_argument('--concurrency',type=int,nargs='+',default=[1,4])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.count<=100 or any(not 1<=c<=8 for c in args.concurrency):
        parser.error('每组请求数为 1–100，并发为 1–8')
    document={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'dbx':metadata(),
        'base_url':value('dbx','url'),'scope':'鉴权后，经单平台入口执行只读 PostgreSQL 查询；结果解码和校验包含在延迟中',
        'warmup_requests_per_query':3,'results':asyncio.run(measure(args.count,args.concurrency)),
        'limitations':['共享开发机背景负载未隔离','不同源码版本的比较不能单独归因于 LTO 或代码生成参数','不是业务流程吞吐或 deep_dub 整片实时率']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes((json.dumps(document,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({**document,'results':[{k:v for k,v in row.items() if k!='samples_ms'} for row in document['results']]},ensure_ascii=False))


if __name__=='__main__':main()
