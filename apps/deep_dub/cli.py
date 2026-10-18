"""业务 CLI：默认本地服务调用；--transport http 对接相同 HTTP 契约。"""
import asyncio
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError
import typer

from anp.auth.session import current_actor
from anp.config import value
from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.observations import deliver_one
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.processing import decode_artifact
from apps.deep_dub.services.source_acquisition import acquire as acquire_source,read as read_source_acquisition
from apps.deep_dub.services.source_orchestration import launch as launch_source_preparation,read as read_source_preparation,prepare_local
from apps.deep_dub.services.workbench import RecordKind,service as workbench
from apps.deep_dub.services.media import service as media
from apps.deep_dub.services.editor import service as editor
from apps.deep_dub.services import waveforms
from apps.deep_dub.services.translation import generate as generate_translation
from apps.deep_dub.services.references import prepare as prepare_reference
from apps.deep_dub.services.speech import generate as generate_speech
from apps.deep_dub.services.audio_review import review as review_audio
from apps.deep_dub.services.audio_timing import analyze as analyze_timing
from apps.deep_dub.services.source_recognition import recognize as recognize_source,read_latest as read_source_recognition
from apps.deep_dub.services.source_reference import compare as compare_source_reference,read as read_source_reference
from apps.deep_dub.services.source_alignment import align as align_source,read_latest as read_source_alignment
from apps.deep_dub.services.source_review import read as read_source_review,ReviewKind
from apps.deep_dub.services.source_recheck import generate as recheck_source,read_latest as read_source_recheck
from apps.deep_dub.services.source_independent_asr import generate as recognize_independent,read_latest as read_independent

app=typer.Typer(help='deep_dub：默认本地执行，复用 anp auth login 会话。',no_args_is_help=True)
project_commands=typer.Typer(help='项目')
run_commands=typer.Typer(help='运行；排队起点不可重置')
media_commands=typer.Typer(help='私有音视频信息；同一用户和工作空间权限')
app.add_typer(project_commands,name='projects')
app.add_typer(run_commands,name='runs')
app.add_typer(media_commands,name='media')


@run_commands.command('recognize-source-independent')
def source_independent_recognition(ctx:typer.Context,run_id:str,source_id:str,timeout_seconds:int=3600,request_key:str=''):
    body={'source_id':source_id,'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/recognize-source-independent',lambda actor:recognize_independent(actor,run_id,body,request_key or None),body,request_key,timeout_seconds=timeout_seconds+300))


@project_commands.command('source-independent-recognition')
def source_independent_read(ctx:typer.Context,project_id:str,source_id:str):
    perform(invoke(ctx,'GET',f'/projects/{project_id}/sources/{source_id}/independent-recognition',lambda actor:read_independent(actor,project_id,source_id)))


@run_commands.command('recheck-source-boundaries')
def source_recheck(ctx:typer.Context,run_id:str,source_id:str,boundary:list[int]=typer.Option(None),timeout_seconds:int=1800,request_key:str=''):
    body={'source_id':source_id,'boundaries':boundary or None,'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/recheck-source-boundaries',lambda actor:recheck_source(actor,run_id,body,request_key or None),body,request_key,timeout_seconds=timeout_seconds+300))


@project_commands.command('source-boundary-recheck')
def source_recheck_read(ctx:typer.Context,project_id:str,source_id:str):
    perform(invoke(ctx,'GET',f'/projects/{project_id}/sources/{source_id}/boundary-recheck',lambda actor:read_source_recheck(actor,project_id,source_id)))


@project_commands.command('source-review-queue')
def source_review_queue(ctx:typer.Context,project_id:str,source_id:str,offset:int=0,limit:int=30,kind:ReviewKind='all',expected_hash:str|None=None):
    params={'offset':offset,'limit':limit,'kind':kind}
    if expected_hash is not None:params['expected_hash']=expected_hash
    perform(invoke(ctx,'GET',f'/projects/{project_id}/sources/{source_id}/review-queue?'+urlencode(params),
        lambda actor:read_source_review(actor,project_id,source_id,**params)))


@run_commands.command('prepare-reference')
def reference(ctx:typer.Context,run_id:str,segment_id:str,kind:Literal['voice','emotion'],
        voice_start_ms:int|None=None,voice_end_ms:int|None=None,filter_mode:Literal['raw','speech-band']='raw',request_key:str=''):
    body={'segment_id':segment_id,'kind':kind,'voice_start_ms':voice_start_ms,'voice_end_ms':voice_end_ms,'filter_mode':filter_mode}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/prepare-reference',
        lambda actor:prepare_reference(actor,run_id,body,request_key or None),body,request_key))


@run_commands.command('speech')
def speech(ctx:typer.Context,run_id:str,segment_id:str,translation_id:str,voice_reference_id:str,emotion_reference_id:str,
        generation_factor:float=1.,timeout_seconds:int=300,seed:int=42,request_key:str=''):
    body={'segment_id':segment_id,'translation_id':translation_id,'voice_reference_id':voice_reference_id,
        'emotion_reference_id':emotion_reference_id,'generation_factor':generation_factor,'timeout_seconds':timeout_seconds,'seed':seed}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/speech',lambda actor:generate_speech(actor,run_id,body,request_key or None),body,request_key))


@run_commands.command('review-audio')
def audio_review(ctx:typer.Context,run_id:str,audio_attempt_id:str,timeout_seconds:int=300,request_key:str=''):
    body={'audio_attempt_id':audio_attempt_id,'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/review-audio',lambda actor:review_audio(actor,run_id,body,request_key or None),body,request_key))


@run_commands.command('recognize-source')
def source_recognition(ctx:typer.Context,run_id:str,source_id:str,language:str='English',chunk_seconds:int=60,timeout_seconds:int=3600,request_key:str=''):
    body={'source_id':source_id,'language':language,'chunk_seconds':chunk_seconds,'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/recognize-source',lambda actor:recognize_source(actor,run_id,body,request_key or None),body,request_key))


@run_commands.command('acquire-source')
def source_acquisition(ctx:typer.Context,run_id:str,timeout_seconds:int=3600,request_key:str=''):
    body={'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/acquire-source',lambda actor:acquire_source(actor,run_id,body,request_key or None),body,request_key,timeout_seconds=timeout_seconds+300))


@run_commands.command('source-acquisition')
def source_acquisition_result(ctx:typer.Context,run_id:str):
    perform(invoke(ctx,'GET',f'/runs/{run_id}/source-acquisition',lambda actor:read_source_acquisition(actor,run_id)))


@run_commands.command('prepare-source')
def source_preparation(ctx:typer.Context,run_id:str,reference_url:str|None=None,ttl_seconds:int=600,request_key:str=''):
    body={'reference_url':reference_url,'ttl_seconds':ttl_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/prepare-source',lambda actor:prepare_local(actor,run_id,body,request_key or None),body,request_key,timeout_seconds=ttl_seconds+300))


@run_commands.command('launch-source-preparation')
def source_preparation_agent(ctx:typer.Context,run_id:str,reference_url:str|None=None,ttl_seconds:int=600,request_key:str=''):
    body={'reference_url':reference_url,'ttl_seconds':ttl_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/source-preparation',lambda actor:launch_source_preparation(actor,run_id,body,request_key or None),body,request_key))


@run_commands.command('source-preparation')
def source_preparation_result(ctx:typer.Context,run_id:str,procedure_id:str):
    perform(invoke(ctx,'GET',f'/runs/{run_id}/source-preparation/{procedure_id}',lambda actor:read_source_preparation(actor,run_id,procedure_id)))


@run_commands.command('align-source')
def source_alignment(ctx:typer.Context,run_id:str,source_id:str,timeout_seconds:int=3600,request_key:str=''):
    body={'source_id':source_id,'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/align-source',lambda actor:align_source(actor,run_id,body,request_key or None),body,request_key))


@project_commands.command('source-word-timing')
def source_alignment_result(ctx:typer.Context,project_id:str,source_id:str):
    perform(invoke(ctx,'GET',f'/projects/{project_id}/sources/{source_id}/word-timing',lambda actor:read_source_alignment(actor,project_id,source_id)))


@run_commands.command('compare-source-reference')
def source_reference(ctx:typer.Context,run_id:str,source_id:str,reference_url:str,request_key:str=''):
    body={'source_id':source_id,'reference_url':reference_url}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/compare-source-reference',lambda actor:compare_source_reference(actor,run_id,body,request_key or None),body,request_key))


@app.command('source-reference')
def source_reference_result(ctx:typer.Context,attempt_id:str):
    perform(invoke(ctx,'GET',f'/attempts/{attempt_id}/source-reference',lambda actor:read_source_reference(actor,attempt_id)))


@project_commands.command('source-recognition')
def source_recognition_result(ctx:typer.Context,project_id:str,source_id:str):
    perform(invoke(ctx,'GET',f'/projects/{project_id}/sources/{source_id}/recognition',
        lambda actor:read_source_recognition(actor,project_id,source_id)))


@run_commands.command('align-audio')
def audio_timing(ctx:typer.Context,run_id:str,audio_attempt_id:str,playback_start_ms:int|None=None,timeout_seconds:int=300,request_key:str=''):
    body={'audio_attempt_id':audio_attempt_id,'playback_start_ms':playback_start_ms,'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/align-audio',lambda actor:analyze_timing(actor,run_id,body,request_key or None),body,request_key))


@run_commands.command('translate')
def translate_segment(ctx:typer.Context,run_id:str,segment_id:str,timeout_seconds:int=120,request_key:str=''):
    body={'segment_id':segment_id,'timeout_seconds':timeout_seconds}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/translate',
        lambda actor:generate_translation(actor,run_id,body,request_key or None),body,request_key))


@app.command('edit-translation')
def edit_translation(ctx:typer.Context,project_id:str,segment_id:str,
        input:Path=typer.Option(...,exists=True,dir_okay=False),request_key:str=''):
    body=input_json(input)
    perform(invoke(ctx,'POST',f'/projects/{project_id}/segments/{segment_id}/translations',
        lambda actor:editor.edit_translation(actor,project_id,segment_id,body,request_key or None),body,request_key))


@media_commands.command('info')
def media_info(ctx:typer.Context,artifact_id:str):
    perform(invoke(ctx,'GET','/media/artifacts/'+artifact_id+'/info',lambda actor:media.info(actor,artifact_id)))


@media_commands.command('waveform')
def read_waveform(ctx:typer.Context,artifact_id:str,start:float=0,end:float|None=None,points:int=640):
    from urllib.parse import urlencode
    query={'start':start,'points':points}
    if end is not None:query['end']=end
    perform(invoke(ctx,'GET','/media/artifacts/'+artifact_id+'/waveform?'+urlencode(query),
        lambda actor:waveforms.read(actor,artifact_id,start=start,end=end,points=points)))


@run_commands.command('waveform')
def prepare_waveform(ctx:typer.Context,run_id:str,artifact_id:str,device_id:str='',timeout_seconds:int=600,request_key:str=''):
    body={'artifact_id':artifact_id,'timeout_seconds':timeout_seconds}
    if device_id:body['device_id']=device_id
    perform(invoke(ctx,'POST',f'/runs/{run_id}/waveform',
        lambda actor:waveforms.prepare(actor,run_id,body,request_key or None),body,request_key))


@app.callback()
def root(ctx:typer.Context, transport:Literal['local','http']|None=None, endpoint:str|None=None):
    parent=ctx.parent.obj if ctx.parent and isinstance(ctx.parent.obj,dict) else {}
    ctx.obj={'transport':transport or parent.get('transport','local'),
             'endpoint':(endpoint or parent.get('endpoint') or value('workbench','api_url')).rstrip('/')}


def perform(operation):
    try:
        result=asyncio.run(operation)
        typer.echo(json.dumps(result,ensure_ascii=False,default=str))
    except BusinessError as error:
        typer.echo(json.dumps({'error':{'code':error.code,'message':error.message}},ensure_ascii=False))
        raise typer.Exit(1) from None
    except ValidationError as error:
        typer.echo(json.dumps({'error':{'code':'VALIDATION_ERROR',
            'details':error.errors(include_input=False,include_context=False)}},ensure_ascii=False,default=str))
        raise typer.Exit(2) from None
    except httpx.HTTPError:
        typer.echo('{"error":{"code":"UPSTREAM_UNAVAILABLE","message":"平台或业务接口连接失败"}}')
        raise typer.Exit(1) from None


def input_json(file:Path):
    try:
        return json.loads(file.read_text(encoding='utf8'))
    except (OSError,ValueError):
        raise typer.BadParameter('无法读取 JSON 输入文件') from None


async def invoke(ctx, method, path, local, payload=None, request_key=None, *, timeout_seconds=300):
    actor=await current_actor()
    if ctx.obj['transport']=='local':
        return await local(actor)
    request_headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
    if request_key:request_headers['Idempotency-Key']=request_key
    async with async_client(timeout=timeout_seconds,trust_env=False) as client:
        response=await client.request(method,ctx.obj['endpoint']+'/api/apps/deep_dub'+path,
                                      headers=request_headers,json=payload)
    if not response.is_success:
        try:error=response.json()['error']
        except (ValueError,KeyError,TypeError):error={}
        raise BusinessError(error.get('message','业务接口请求失败'),response.status_code,error.get('code','HTTP_ERROR'))
    return response.json()


@project_commands.command('list')
def list_projects(ctx:typer.Context):
    perform(invoke(ctx,'GET','/projects',projects.list))


@project_commands.command('records')
def project_records(ctx:typer.Context,project_id:str,kind:RecordKind,offset:int=0,limit:int=100):
    perform(invoke(ctx,'GET',f'/projects/{project_id}/records/{kind}?offset={offset}&limit={limit}',
        lambda actor:workbench.records(actor,project_id,kind,offset=offset,limit=limit)))


@project_commands.command('studio')
def studio_snapshot(ctx:typer.Context,project_id:str,source_id:str='',segment_set_id:str='',profile_id:str='',timeline_id:str=''):
    from urllib.parse import urlencode
    options={key:val for key,val in dict(source_id=source_id,segment_set_id=segment_set_id,profile_id=profile_id,timeline_id=timeline_id).items() if val}
    perform(invoke(ctx,'GET',f'/projects/{project_id}/studio?'+urlencode(options),lambda actor:workbench.studio(actor,project_id,**options)))


@run_commands.command('get')
def run_detail(ctx:typer.Context,run_id:str):
    perform(invoke(ctx,'GET','/runs/'+run_id,lambda actor:workbench.run(actor,run_id)))


@project_commands.command('create')
def create_project(ctx:typer.Context,name:str,source_url:str,request_key:str=''):
    payload={'name':name,'source_url':source_url}
    perform(invoke(ctx,'POST','/projects',lambda actor:projects.create(actor,payload,request_key or None),payload,request_key))


@project_commands.command('quality-profile')
def create_profile(ctx:typer.Context,input:Path=typer.Option(...,exists=True,dir_okay=False)):
    body=input_json(input)
    perform(invoke(ctx,'POST','/quality-profiles',lambda actor:versions.create_quality_profile(actor,body),body))


@run_commands.command('list')
def list_runs(ctx:typer.Context,project_id:str):
    perform(invoke(ctx,'GET',f'/projects/{project_id}/runs',lambda actor:execution.list(actor,project_id)))


@run_commands.command('queue')
def queue(ctx:typer.Context,project_id:str,input:Path=typer.Option(...,exists=True,dir_okay=False),request_key:str=''):
    body=input_json(input)
    perform(invoke(ctx,'POST',f'/projects/{project_id}/runs',lambda actor:execution.queue(actor,project_id,body,request_key or None),body,request_key))


@run_commands.command('start')
def start(ctx:typer.Context,run_id:str):
    perform(invoke(ctx,'POST',f'/runs/{run_id}/start',lambda actor:execution.start(actor,run_id)))


@run_commands.command('cancel')
def cancel(ctx:typer.Context,run_id:str):
    perform(invoke(ctx,'POST',f'/runs/{run_id}/cancel',lambda actor:execution.cancel(actor,run_id)))


@app.command('flush-observation')
def flush(ctx:typer.Context):
    perform(invoke(ctx,'POST','/observations/flush',deliver_one))


@run_commands.command('decode')
def decode(ctx:typer.Context,run_id:str,artifact_id:str,device_id:str='',request_key:str=''):
    body={'artifact_id':artifact_id,'device_id':device_id or None}
    perform(invoke(ctx,'POST',f'/runs/{run_id}/decode',
        lambda actor:decode_artifact(actor,run_id,body,request_key or None),body,request_key))


@app.command('candidate')
def candidate(ctx:typer.Context,kind:Literal['source','transcript','translation','reference','audio','background','render'],attempt_id:str,
        input:Path=typer.Option(...,exists=True,dir_okay=False),request_key:str=''):
    body=input_json(input)
    operation=getattr(candidates,kind)
    perform(invoke(ctx,'POST',f'/attempts/{attempt_id}/{kind}',
        lambda actor:operation(actor,attempt_id,body,request_key or None),body,request_key))


@app.command('upload-artifact')
def upload_artifact(ctx:typer.Context,attempt_id:str,file:Path=typer.Argument(...,exists=True,dir_okay=False),
        media_type:str='application/octet-stream',duration_ms:int|None=typer.Option(None,min=0),
        sample_rate:int|None=typer.Option(None,min=1)):
    async def run():
        actor=await current_actor()
        if ctx.obj['transport']=='local':
            return await artifacts.upload(actor,attempt_id,file,media_type,duration_ms,sample_rate)
        async def blocks():
            with file.open('rb') as source:
                while block:=source.read(1024*1024):yield block
        params={k:v for k,v in {'duration_ms':duration_ms,'sample_rate':sample_rate}.items() if v is not None}
        async with async_client(timeout=300,trust_env=False) as client:
            response=await client.post(ctx.obj['endpoint']+f'/api/apps/deep_dub/attempts/{attempt_id}/artifacts',
                headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id,
                    'Content-Type':media_type,'Content-Length':str(file.stat().st_size)},
                params=params,content=blocks())
        if not response.is_success:
            raise BusinessError('产物提交失败',response.status_code,'ARTIFACT_UPLOAD_FAILED')
        return response.json()
    perform(run())


if __name__=='__main__':
    app()
