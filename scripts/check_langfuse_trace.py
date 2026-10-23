"""仅对本机独立验收项目写入合成 trace，并验证异步处理、鉴权和恢复后读回。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import subprocess
from manage import ROOT, PROJECT, ensure_owned_project, read_env
import time
import uuid

import httpx

root=ROOT
ensure_owned_project(read_env(ROOT/'.env')['ANP_WORKSPACE_ID'])
raw=subprocess.run(['docker','exec',PROJECT+'-toolbox-1','cat','/data/config/services.json'],capture_output=True)
if raw.returncode:
    raise SystemExit('未配置 Langfuse 验收项目，Trace 验收未执行')
private=json.loads(raw.stdout).get('langfuse-web',{})
if not all(private.get(key) for key in ('LANGFUSE_INIT_PROJECT_PUBLIC_KEY','LANGFUSE_INIT_PROJECT_SECRET_KEY')):
    raise SystemExit('未配置 Langfuse 验收项目密钥，Trace 验收未执行')
auth=(private['LANGFUSE_INIT_PROJECT_PUBLIC_KEY'],private['LANGFUSE_INIT_PROJECT_SECRET_KEY'])
receipt=root/'var/evidence/current-langfuse-trace.json'
read_only='--read' in sys.argv
trace=json.loads(receipt.read_text())['trace_id'] if read_only else uuid.uuid4().hex
name='anp-local-acceptance-'+trace
with httpx.Client(base_url='http://127.0.0.1:51760',timeout=30,trust_env=False) as client:
    if not read_only:
        timestamp=time.time_ns()
        values={"langfuse.trace.name":name,"langfuse.trace.input":json.dumps({"scope":"synthetic local fixture"}),
                "langfuse.trace.output":json.dumps({"verified":True}),"langfuse.observation.type":"span",
                "langfuse.observation.input":json.dumps({"scope":"synthetic local fixture"}),
                "langfuse.observation.output":json.dumps({"verified":True})}
        payload={"resourceSpans":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"anp-local-acceptance"}}]},
            "scopeSpans":[{"scope":{"name":"anp-local-acceptance","version":"1.0"},"spans":[{
                "traceId":trace,"spanId":uuid.uuid4().hex[:16],"name":name,"kind":1,
                "startTimeUnixNano":str(timestamp),"endTimeUnixNano":str(timestamp+1000000),
                "attributes":[{"key":key,"value":{"stringValue":value}} for key,value in values.items()]}]}]}]}
        response=client.post("/langfuse/api/public/otel/v1/traces",auth=auth,json=payload)
        assert response.status_code==200,f"OTLP ingestion status {response.status_code}"
        body=response.json()
        assert not body.get("partialSuccess",{}).get("rejectedSpans"),"OTLP rejected span"
    deadline=time.monotonic()+120
    while True:
        response=client.get('/langfuse/api/public/v2/observations',auth=auth,params={'traceId':trace,'fields':'basic,io'})
        if response.status_code==200 and response.json().get('data'):
            observed=response.json()['data'][0]
            assert observed['traceId']==trace and observed['name']==name
            for key in ('input','output'):
                if isinstance(observed[key],str): observed[key]=json.loads(observed[key])
            assert observed['input']=={'scope':'synthetic local fixture'}
            assert observed['output']=={'verified':True}
            break
        assert response.status_code in (200,404,503),f'trace read status {response.status_code}'
        if time.monotonic()>=deadline:
            raise TimeoutError('trace was not indexed within 120 seconds')
        time.sleep(2)
    denied=client.get('/langfuse/api/public/v2/observations',params={'traceId':trace})
    assert denied.status_code==401,'unauthenticated trace read must be rejected'
result={'passed':True,'trace_id':trace,'synthetic':True,'remote_model_calls':0,
        'protocol':'OTLP JSON, Langfuse v4','authenticated_ingestion':True,'async_trace_readback':True,'unauthenticated_read_denied':True}
if read_only:
    result['read_after_runtime_restarts']=True
receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
