"""按run已冻结URL获取新原片，登记不可变来源；同身份三入口共用。"""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID
from contextlib import contextmanager
import shutil

from pydantic import Field

from anp import STATE_ROOT
from anp.config import value
from anp.errors import BusinessError
from apps.deep_dub.nodes.source_fetch import DownloadConfig,RULE,fetch,youtube_location
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.runtime import run_cpu_stage,StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class AcquisitionInput(Contract):
    timeout_seconds:int=Field(default=3600,ge=60,le=7200,strict=True)
    device_id:UUID|None=None


@contextmanager
def workspace(attempt_id):
    root=(STATE_ROOT/'deep_dub/source-fetch').resolve();root.mkdir(parents=True,exist_ok=True)
    folder=root/identifier(attempt_id)
    folder.mkdir(exist_ok=False)
    try:yield folder
    finally:
        # 仅本次新建临时目录，绝对路径及父目录已核对；不保留宿主机原片副本。
        if folder.is_symlink() or folder.resolve().parent!=root:
            raise BusinessError('下载临时目录归属变化，拒绝清理',409,'DOWNLOAD_TARGET_CHANGED')
        shutil.rmtree(folder)


def configuration():
    filename=value('deep_dub','source_fetch_config')
    if not filename:raise BusinessError('尚未配置来源下载执行器',503,'DOWNLOAD_UNCONFIGURED')
    return DownloadConfig.model_validate_json(Path(filename).read_bytes())


async def describe(actor,run_id,payload):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    body=AcquisitionInput.model_validate(payload);run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs:raise BusinessError('运行不存在或无权访问',404,'NOT_FOUND')
    run=runs[0]
    tasks=await actor.objects.select('tasks',workspace_id='eq.'+actor.workspace_id,id='eq.'+run['task_id'])
    if not tasks:raise BusinessError('运行缺少冻结任务输入',409,'SOURCE_INPUT_MISSING')
    original=tasks[0]['input'].get('source_url');url,video_id=youtube_location(original)
    config=configuration()
    inputs={'source_url_snapshot':original,'download_url':url,'video_id':video_id,'rule_revision':RULE,
        'downloader_sha256':config.executable_sha256,'download_context_revision':config.context_revision,
        'timeout_seconds':body.timeout_seconds}
    digest=sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if run['source_id'] is not None:
        sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,id='eq.'+run['source_id'])
        stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,run_id='eq.'+run_id,
            node='eq.source_acquisition',input_hash='eq.'+digest)
        if not sources or not stages:
            raise BusinessError('本轮已绑定其他来源，不能换源或复用旧片冒充新下载',409,'SOURCE_ALREADY_BOUND')
    return body,config,inputs,digest


async def acquire(actor,run_id,payload,request_key=None):
    run_id=identifier(run_id);body,config,inputs,digest=await describe(actor,run_id,payload)
    async def handler(current,attempt_id):
        with workspace(attempt_id) as folder:
            proof,file=await fetch(config,inputs['download_url'],folder,body.timeout_seconds)
            video=await artifacts.upload(current,attempt_id,file,{'mkv':'video/x-matroska','mp4':'video/mp4','webm':'video/webm'}[file.suffix[1:]],
                duration_ms=proof['media']['duration_ms'])
            proof={'input':inputs,'download':proof,'video_artifact_id':video['id']}
            note=folder/'proof.json';note.write_bytes(json.dumps(proof,ensure_ascii=False,indent=2).encode())
            evidence=await artifacts.upload(current,attempt_id,note,'application/json')
            await candidates.source(current,attempt_id,{'artifact_id':video['id'],'duration_ms':proof['download']['media']['duration_ms'],
                'identity_evidence':{'rule_revision':RULE,'video_id':inputs['video_id'],'download_evidence_artifact_id':evidence['id'],
                    'new_download':True,'independent_content_review':'not_measured'}},'source-acquisition:'+attempt_id)
            return StageResult(outputs=[video['id'],evidence['id']])
    result=await run_cpu_stage(actor,run_id,'source_acquisition',digest,handler,request_key=operation_key(request_key),
        device_id=str(body.device_id) if body.device_id else None)
    if result['status']=='succeeded':await record_scores(actor,result['attempt'])
    return result


async def checked_evidence(actor,attempt):
    if len(attempt['outputs'])!=2:raise BusinessError('来源获取缺少原片及证据',409,'SOURCE_ACQUISITION_EVIDENCE_INVALID')
    rows=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='in.('+','.join(attempt['outputs'])+')')
    videos=[r for r in rows if r['media_type'].startswith('video/') and r['stage_attempt_id']==attempt['id']]
    notes=[r for r in rows if r['media_type']=='application/json' and r['stage_attempt_id']==attempt['id'] and r['bytes']<=1024**2]
    if len(videos)!=1 or len(notes)!=1:raise BusinessError('来源获取产物类型或归属无效',409,'SOURCE_ACQUISITION_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-source-fetch-proof-') as directory:
        file=Path(directory)/'proof.json';await artifacts.download(actor,notes[0]['id'],file)
        proof=json.loads(file.read_bytes())
    try:
        inputs=proof['input'];download=proof['download'];video=videos[0]
        digest=sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
        assert stages and stages[0]['node']=='source_acquisition' and stages[0]['input_hash']==digest
        runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+stages[0]['run_id'])
        assert runs and runs[0]['project_id']==attempt['project_id']
        sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,id='eq.'+runs[0]['source_id'])
        assert sources and sources[0]['stage_attempt_id']==attempt['id'] and sources[0]['artifact_id']==video['id']
        tasks=await actor.objects.select('tasks',workspace_id='eq.'+actor.workspace_id,id='eq.'+runs[0]['task_id'])
        assert tasks and tasks[0]['input']['source_url']==inputs['source_url_snapshot']==sources[0]['source_url']
        url,video_id=youtube_location(inputs['source_url_snapshot'])
        assert download['source_url']==inputs['download_url']==url and download['online']['video_id']==inputs['video_id']==video_id
        assert download['rule_revision']==inputs['rule_revision']==RULE and download['new_download'] is True
        assert download['downloader_sha256']==inputs['downloader_sha256'] and download['download_context_revision']==inputs['download_context_revision']
        assert download['file']=={'sha256':video['sha256'],'bytes':video['bytes']} and proof['video_artifact_id']==video['id']
        assert download['media']['duration_ms']==video['duration_ms']==sources[0]['duration_ms']
        assert all(any(s['codec_type']==kind for s in download['media']['streams']) for kind in ('video','audio'))
        assert download['process']['exit_code']==0 and download['process']['operation_stopped'] is True
        assert download['process']['job']['active_processes']==0 and download['process']['job']['total_processes']>=2
        assert download['content_accuracy_review']==download['subtitle_timing_review']=='not_measured'
        identity=sources[0]['identity_evidence']
        assert identity['download_evidence_artifact_id']==notes[0]['id'] and identity['new_download'] is True
    except (AssertionError,KeyError,ValueError,TypeError):
        raise BusinessError('来源获取证据与运行、输入、原片或进程回执不符',409,'SOURCE_ACQUISITION_EVIDENCE_INVALID') from None
    return {'source_id':sources[0]['id'],'stage_attempt_id':attempt['id'],'evidence_artifact_id':notes[0]['id'],'proof':proof}


async def read(actor,run_id):
    await actor.require('deep_dub.project.read');run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs:raise BusinessError('运行不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,run_id='eq.'+run_id,node='eq.source_acquisition')
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,
        stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc',limit=1)
    return await checked_evidence(actor,attempts[0]) if attempts else None


async def record_scores(actor,attempt):
    record=await checked_evidence(actor,attempt)
    for metric,number in {'source_download_id_match':1,'source_download_av_streams':1,
        'source_download_content_accuracy':None,'source_download_subtitle_timing':None}.items():
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,
            'measurement_status':'measured' if number is not None else 'not_measured','value':number,
            'evidence_artifact_id':record['evidence_artifact_id'] if number is not None else None,
            'details':{'scope':record['proof']['download']['identity_scope']}},metric+':'+attempt['id'])
