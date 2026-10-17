"""对真实 demo 图执行有界并发基准，包含任务创建、排队、三个节点与对象读取。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

import httpx
from anp.config import secret


def snapshot():
    names=['anp-platform-platform-1',*[f'anp-storage-{name}-1' for name in ('postgres','redis','clickhouse','rustfs')]]
    result=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',*names],
        capture_output=True,text=True,timeout=30)
    if result.returncode:return {'available':False}
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


async def measure(base,concurrencies,count):
    async with httpx.AsyncClient(base_url=base,trust_env=False,timeout=30) as client:
        response=await client.post('/supabase/auth/v1/token?grant_type=password',
            headers={'apikey':secret('ANON_KEY')},
            json={'email':secret('ADMIN_EMAIL'),'password':secret('ADMIN_PASSWORD')})
        if response.status_code!=200:raise RuntimeError('基准账号登录失败，HTTP '+str(response.status_code))
        client.headers['Authorization']='Bearer '+response.json()['access_token']
        result=[]
        for concurrency in concurrencies:
            semaphore=asyncio.Semaphore(concurrency)
            async def task(index):
                async with semaphore:
                    started=time.perf_counter()
                    response=await client.post('/api/apps/demo/tasks',json={
                        'title':f'平台并发基准 / {concurrency} / {index}',
                        'text':'真实平台基准。包含图调度、业务节点、数据库与对象保存。'})
                    if response.status_code!=201:raise RuntimeError('基准任务创建失败，HTTP '+str(response.status_code))
                    task_id=response.json()['id']
                    response=await client.post(f'/api/apps/demo/tasks/{task_id}/run')
                    if response.status_code!=200:raise RuntimeError('基准任务启动失败，HTTP '+str(response.status_code))
                    while True:
                        response=await client.get(f'/api/apps/demo/tasks/{task_id}')
                        response.raise_for_status();detail=response.json()
                        if detail['status'] in ('succeeded','failed','cancelled'):break
                        if time.perf_counter()-started>120:raise RuntimeError('基准任务等待超过 120 秒：'+task_id)
                        await asyncio.sleep(.1)
                    if detail['status']!='succeeded':raise RuntimeError('基准任务未成功：'+task_id)
                    if [node['node'] for node in detail['nodes']]!=['normalize','analyze','save']:
                        raise RuntimeError('任务节点不完整：'+task_id)
                    response=await client.get(f'/api/apps/demo/tasks/{task_id}/result')
                    response.raise_for_status();report=response.json()
                    response=await client.get('/supabase/storage/v1/object/authenticated/'+report['object_path'])
                    response.raise_for_status()
                    if response.json()!=report['report']:raise RuntimeError('保存对象与报告不一致：'+task_id)
                    return {'task_id':task_id,'seconds':round(time.perf_counter()-started,4)}
            started=time.perf_counter()
            tasks=await asyncio.gather(*(task(index) for index in range(count)))
            elapsed=time.perf_counter()-started
            times=sorted(item['seconds'] for item in tasks)
            row={'concurrency':concurrency,'count':count,'wall_seconds':round(elapsed,4),
                'tasks_per_second':round(count/elapsed,4),'latency_median_seconds':round(statistics.median(times),4),
                'latency_max_seconds':max(times),'tasks':tasks}
            result.append(row)
            print(json.dumps({key:value for key,value in row.items() if key!='tasks'},ensure_ascii=False),flush=True)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8089')
    parser.add_argument('--concurrency',type=int,nargs='+',default=[1,2,4])
    parser.add_argument('--count',type=int,default=8)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.count<=32 or any(not 1<=n<=8 for n in args.concurrency):
        parser.error('每组任务数须为 1–32，并发须为 1–8')
    record={'scope':'真实 demo HTTP 执行链路；不是 deep_dub 整片 RTF，也不是模型吞吐',
        'url':args.url,'host_logical_cpus':os.cpu_count(),'resources_before':snapshot()}
    record['measurements']=asyncio.run(measure(args.url,args.concurrency,args.count))
    record['resources_after']=snapshot()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


if __name__=='__main__':main()
