"""业务 CLI：默认本地服务调用；--transport http 对接相同 HTTP 契约。"""
import asyncio
import json
from pathlib import Path
from typing import Literal

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

app=typer.Typer(help='deep_dub：默认本地执行，复用 anp auth login 会话。',no_args_is_help=True)
project_commands=typer.Typer(help='项目')
run_commands=typer.Typer(help='运行；排队起点不可重置')
app.add_typer(project_commands,name='projects')
app.add_typer(run_commands,name='runs')


@app.callback()
def root(ctx:typer.Context, transport:Literal['local','http']='local', endpoint:str=''):
    ctx.obj={'transport':transport,'endpoint':(endpoint or value('workbench','api_url')).rstrip('/')}


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


async def invoke(ctx, method, path, local, payload=None, request_key=None):
    actor=await current_actor()
    if ctx.obj['transport']=='local':
        return await local(actor)
    request_headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
    if request_key:request_headers['Idempotency-Key']=request_key
    async with async_client(timeout=300,trust_env=False) as client:
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
