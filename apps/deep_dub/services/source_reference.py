"""官方参考比对阶段：只输出差异候选，不封存原文或签发质量审核。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.nodes.source_reference import RULE, parse_yale_reference, compare_reference
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.runtime import StageResult, run_cpu_stage
from apps.deep_dub.services.source_recognition import read_latest, checked_evidence as checked_asr
from apps.deep_dub.services.versions import Contract, identifier, operation_key


class SourceReferenceInput(Contract):
    source_id: UUID
    reference_url: str = Field(min_length=1, max_length=500)


def reference_location(reference_url, source_url):
    official = urlsplit(reference_url)
    if (official.scheme != 'https' or official.netloc != 'oyc.yale.edu' or official.query or official.fragment
        or not re.fullmatch(r'/[a-z-]+/[a-z]+-\d+/lecture-\d+', official.path)):
        raise BusinessError('当前只支持耶鲁官方课程页，禁止自定义主机、端口、跳转或查询参数',422,'REFERENCE_URL_INVALID')
    source = urlsplit(source_url)
    video = parse_qs(source.query).get('v', [])
    if source.scheme != 'https' or source.netloc not in ('www.youtube.com', 'youtube.com') or source.path != '/watch' or len(video) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video[0]):
        raise BusinessError('项目缺少明确的YouTube来源视频ID',422,'REFERENCE_SOURCE_INVALID')
    return video[0]


async def fetch_reference(url):
    # 无调用者Cookie/授权头；限定官方主机，拒绝跳转并按实际解压字节限制大小。
    async with async_client(timeout=60,trust_env=True,follow_redirects=False) as client:
        async with client.stream('GET',url) as response:
            if response.status_code != 200 or 'text/html' not in response.headers.get('content-type',''):
                raise BusinessError('官方参考页不可用、跳转或不是HTML',502,'REFERENCE_FETCH_FAILED')
            raw=bytearray()
            async for block in response.aiter_bytes():
                raw.extend(block)
                if len(raw)>2*1024**2:
                    raise BusinessError('官方参考页超过两MiB',502,'REFERENCE_FETCH_LIMIT')
    return bytes(raw)


async def compare(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run')
    await actor.require('deep_dub.audit')
    body=SourceReferenceInput.model_validate(payload)
    run_id=identifier(run_id)
    async def row(table,key):
        rows=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+str(key))
        if not rows:
            raise BusinessError('参考比对输入不存在或无权访问',404,'NOT_FOUND')
        return rows[0]
    run=await row('deep_dub_runs',run_id)
    source=await row('deep_dub_sources',body.source_id)
    project=await row('deep_dub_projects',run['project_id'])
    diagnostic=(run['purpose']=='acceptance' and run['reuse_policy']=='approved_versions' and run['source_id'] is None)
    if source['project_id']!=project['id'] or (run['source_id']!=source['id'] and not diagnostic):
        raise BusinessError('参考比对来源不属于本轮项目',409,'REFERENCE_INPUT_MISMATCH')
    video_id=reference_location(body.reference_url,source['source_url'])
    asr=await read_latest(actor,project['id'],source['id'])
    if not asr:
        raise BusinessError('参考比对需要同一来源的成功ASR证据',409,'SOURCE_ASR_REQUIRED')
    note=await row('deep_dub_artifacts',asr['evidence_artifact_id'])
    inputs={'source_id':source['id'],'source_artifact_id':source['artifact_id'],
            'asr_evidence_artifact_id':note['id'],'asr_evidence_sha256':note['sha256'],
            'reference_url':body.reference_url,'expected_video_id':video_id,'rule_revision':RULE,
            'binding_scope':'explicit-historical-source-diagnostic' if diagnostic else 'run-bound-source'}
    canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    async def handler(current,attempt_id):
        async with asyncio.timeout(120):
            raw=await fetch_reference(body.reference_url)
            official=await await_blocking(parse_yale_reference,raw,video_id)
            chunks=[{'text':c['recognition']['text']} for c in asr['recognition']['chunks']]
            comparison=await await_blocking(compare_reference,official['text'],chunks)
            with TemporaryDirectory(prefix='anp-source-reference-') as folder:
                html=Path(folder)/'official.html';html.write_bytes(raw)
                reference=await artifacts.upload(current,attempt_id,html,'text/html')
                proof={'input':inputs,'reference_artifact_id':reference['id'],'reference':official,'comparison':comparison,
                       'http_status':200,'official_page_embeds_expected_video':True,
                       'local_media_online_identity_verified':False,'source_or_subtitles_modified':False}
                file=Path(folder)/'comparison.json'
                file.write_bytes((json.dumps(proof,ensure_ascii=False,sort_keys=True)+'\n').encode())
                if file.stat().st_size>8*1024**2:
                    raise BusinessError('参考比对证据超限',502,'REFERENCE_RESPONSE_LIMIT')
                evidence=await artifacts.upload(current,attempt_id,file,'application/json')
            return StageResult([reference['id'],evidence['id']])
    result=await run_cpu_stage(actor,run_id,'source_reference',sha256(canonical.encode()).hexdigest(),handler,
                               partition=source['id'],request_key=operation_key(request_key))
    if result['status']=='succeeded':
        await record_scores(actor,result['attempt'])
    return result


async def read_evidence(actor,attempt):
    await actor.require('deep_dub.project.read')
    rows=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,
                                   id='in.('+','.join(attempt['outputs'])+')')
    notes=[r for r in rows if r['media_type']=='application/json' and r['stage_attempt_id']==attempt['id'] and r['bytes']<=8*1024**2]
    html=[r for r in rows if r['media_type']=='text/html' and r['stage_attempt_id']==attempt['id'] and r['bytes']<=2*1024**2]
    if len(attempt['outputs'])!=2 or len(notes)!=1 or len(html)!=1:
        raise BusinessError('参考比对缺少唯一证据',409,'REFERENCE_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-reference-proof-') as folder:
        file=Path(folder)/'proof.json';await artifacts.download(actor,notes[0]['id'],file)
        proof=json.loads(file.read_bytes())
        raw=Path(folder)/'official.html';await artifacts.download(actor,html[0]['id'],raw)
        try:
            inputs=proof['input']
            stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
            sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,id='eq.'+inputs['source_id'])
            assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+inputs['asr_evidence_artifact_id'])
            assert assets and sources and stages
            asr_attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,id='eq.'+assets[0]['stage_attempt_id'],status='eq.succeeded')
            assert asr_attempts and asr_attempts[0]['project_id']==attempt['project_id']==sources[0]['project_id']
            asr,_=await checked_asr(actor,asr_attempts[0])
            assert asr['input']['source_id']==sources[0]['id']
            canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            assert stages[0]['node']=='source_reference' and stages[0]['partition_key']==sources[0]['id']
            assert inputs['rule_revision']==RULE and sha256(canonical.encode()).hexdigest()==stages[0]['input_hash']
            assert inputs['source_artifact_id']==sources[0]['artifact_id'] and inputs['asr_evidence_sha256']==assets[0]['sha256']
            assert proof['reference_artifact_id']==html[0]['id'] and proof['http_status']==200
            assert proof['official_page_embeds_expected_video'] is True
            assert proof['local_media_online_identity_verified'] is False and proof['source_or_subtitles_modified'] is False
            official=await await_blocking(parse_yale_reference,raw.read_bytes(),inputs['expected_video_id'])
            comparison=await await_blocking(compare_reference,official['text'],[{'text':c['recognition']['text']} for c in asr['recognition']['chunks']])
            assert proof['reference']==official and proof['comparison']==comparison
        except (KeyError,TypeError,AssertionError,ValueError):
            raise BusinessError('参考比对证据与来源、ASR、HTML或输入摘要不匹配',409,'REFERENCE_EVIDENCE_INVALID') from None
    return proof,notes[0]


async def read(actor,attempt_id):
    await actor.require('deep_dub.project.read')
    rows=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,id='eq.'+identifier(attempt_id),status='eq.succeeded')
    if not rows:
        raise BusinessError('参考比对结果不存在或无权访问',404,'NOT_FOUND')
    proof,note=await read_evidence(actor,rows[0])
    return {'stage_attempt_id':rows[0]['id'],'evidence_artifact_id':note['id'],'proof':proof}


async def read_latest_reference(actor,project_id,source_id):
    await actor.require('deep_dub.project.read')
    project_id,source_id=identifier(project_id),identifier(source_id)
    sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+source_id)
    if not sources:
        raise BusinessError('来源不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,node='eq.source_reference',partition_key='eq.'+source_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    return await read(actor,attempts[0]['id'])


async def record_scores(actor,attempt):
    proof,note=await read_evidence(actor,attempt)
    comparison=proof['comparison']
    for metric,value in {'source_reference_differences':comparison['difference_count'],
                         'source_reference_numeric_candidates':sum(d['contains_number'] for d in comparison['differences']),
                         'source_reference_semantics':None,'source_reference_subtitle_timing':None}.items():
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,
            'measurement_status':'not_measured' if value is None else 'measured','value':value,
            'evidence_artifact_id':note['id'] if value is not None else None,
            'details':{'scope':'差异候选，不判定双方文本哪方正确或字幕可用'}},metric+':'+attempt['id'])
