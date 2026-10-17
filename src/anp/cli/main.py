"""AI 一等入口：默认在本机调用业务代码，HTTP 模式调用同一服务契约。"""
import asyncio
import json
from pathlib import Path
import sys
import time
import typer
import httpx
from anp.sdk.http import async_client
from pydantic import ValidationError
from anp import ROOT
from anp.config import value,secret
from anp.errors import BusinessError
from anp.applications.registry import applications,application,entrypoint
from anp.auth.session import login,current_actor,session_path

app=typer.Typer(help='ANP AI 原生工作台：默认本地执行，不需要启动业务 Web 服务。',no_args_is_help=True)
auth_app=typer.Typer(help='Supabase 登录与会话管理')
platform_app=typer.Typer(help='平台服务与资源管理')
apps_app=typer.Typer(help='业务发现、复制与注册')
users_app=typer.Typer(help='管理员账号与权限维护')
app.add_typer(auth_app,name='auth')
app.add_typer(platform_app,name='platform')
app.add_typer(apps_app,name='app')
app.add_typer(users_app,name='users')


@app.callback()
def root(ctx:typer.Context,transport:str=typer.Option('local',help='local 或 http'),
         endpoint:str=typer.Option('',help='HTTP 模式业务 API 地址'),
         json_output:bool=typer.Option(False,'--json',help='结构化输出，非交互环境也默认输出 JSON')):
    if transport not in ('local','http'): raise typer.BadParameter('transport 只能为 local 或 http')
    ctx.obj={'transport':transport,'endpoint':endpoint or value('workbench','api_url')}


def output(data):
    typer.echo(json.dumps(data,ensure_ascii=False,default=str))


def perform(coro):
    async def operation():
        try: return await coro
        finally:
            # 查询、登录等命令不创建追踪，避免为 flush 引入整个观测 SDK。
            tracing=sys.modules.get('anp.sdk.tracing')
            if tracing and tracing.get_client.cache_info().currsize:
                await asyncio.to_thread(tracing.get_client().flush)
    try:
        data=asyncio.run(operation())
        output(data)
        if isinstance(data,dict) and data.get('status')=='failed': raise typer.Exit(1)
        return data
    except BusinessError as error:
        output({'error':{'code':error.code,'message':error.message}})
        raise typer.Exit(1)
    except ValidationError as error:
        output({'error':{'code':'VALIDATION_ERROR','message':str(error)}})
        raise typer.Exit(2)
    except httpx.HTTPError:
        output({'error':{'code':'UPSTREAM_UNAVAILABLE','message':'连接失败，请检查平台或目标 API 地址'}})
        raise typer.Exit(1)


@auth_app.command('login')
def auth_login(email:str=typer.Option(...,prompt='邮箱'),
               password:str=typer.Option(...,prompt='密码',hide_input=True,envvar='ANP_PASSWORD')):
    perform(login(email,password))


@auth_app.command('whoami')
def whoami():
    async def run():
        actor=await current_actor()
        return {'id':actor.user['id'],'email':actor.user['email'],'workspace_id':actor.workspace_id}
    perform(run())


@auth_app.command('logout')
def logout():
    path=session_path()
    if path.exists(): path.unlink()
    output({'logged_out':True})


@app.command('init')
def init():
    from anp.dev.v3 import initialize
    config=initialize()
    output({'initialized':True,'platform_url':config['platform']['url'],'configuration':'config/deployment.local.toml'})


@platform_app.command('up')
def platform_up(admin:bool=typer.Option(False,'--admin',help='兼容旧命令；V3 总是包含管理后台和 DBX'),
                llm:bool=typer.Option(False,'--llm',help='兼容旧命令；V3 总是包含模型网关')):
    from contextlib import redirect_stdout
    from anp.dev.v3 import prepare,environment
    from anp.management.applications import migrate_applications
    with redirect_stdout(sys.stderr):prepare('docker')
    environment();migrate_applications()
    output({'started':True,'topology':'四存储 + 单平台','platform_url':value('workbench','api_url')})


@platform_app.command('down')
def platform_down():
    from anp.dev.v3 import compose,stop_native,document
    stop_native();compose('platform','stop')
    services=document()['storage'].get('local',[])
    if services:compose('storage','--profile','local','stop',*services)
    output({'stopped':True,'volumes_preserved':True})


@platform_app.command('check')
def platform_check():
    response=httpx.get(value('workbench','api_url')+'/_platform/health',trust_env=False,timeout=10)
    output(response.json())
    if response.status_code!=200:raise typer.Exit(1)


@app.command('migrate')
def migrate():
    from anp.management.applications import migrate_applications as run
    output(run())


@users_app.command('create')
def user_create(email:str=typer.Option(...),password:str=typer.Option(...,hide_input=True,prompt=True),
                role:str=typer.Option('developer')):
    from anp.management.bootstrap import create_user
    async def run():
        actor=await current_actor()
        if not await actor.objects.rpc('is_admin'): raise BusinessError('仅平台管理员可创建账号',403,'FORBIDDEN')
        if role not in ('admin','developer','viewer'): raise BusinessError('无效角色')
        return await create_user(email,password,role,actor.workspace_id)
    perform(run())


@apps_app.command('list')
def list_apps():
    output([{'id':a,'name':m['name'],'version':m['version']} for a,m in applications().items()])


@apps_app.command('sync')
def sync_apps():
    from anp.applications.generator import sync
    output(sync())


@apps_app.command('new')
def new_app(name:str,source:str=typer.Option('demo','--from')):
    from anp.applications.generator import create
    output(create(name,source))


def input_json(text,title,input_file):
    if input_file:
        raw=sys.stdin.read() if input_file=='-' else Path(input_file.removeprefix('@')).read_text(encoding='utf-8')
        try: return json.loads(raw)
        except ValueError: raise typer.BadParameter('输入必须为 JSON')
    if not text: raise typer.BadParameter('请提供 --text 或 --input JSON文件（- 表示标准输入）')
    return {'title':title,'text':text}


async def invoke(ctx,app_id,verb,task_id=None,payload=None,request_key=None,wait=True):
    actor=await current_actor()
    if ctx.obj['transport']=='local':
        service=entrypoint(application(app_id)['services']['entry'])
        if verb=='create': return await service.create(actor,payload,request_key)
        if verb=='list': return await service.list(actor)
        if verb=='run': return await service.run(actor,task_id,'local')
        return await getattr(service,verb)(actor,task_id)
    base=ctx.obj['endpoint']+f'/api/apps/{app_id}/tasks'
    headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
    if request_key: headers['Idempotency-Key']=request_key
    method='GET'; url=base
    if verb=='create': method='POST'
    elif verb in ('run','cancel'): method='POST'; url+=f'/{task_id}/{verb}'
    elif verb=='result': url+=f'/{task_id}/result'
    elif verb=='get': url+='/'+task_id
    async with async_client(timeout=30,trust_env=False) as http:
        response=await http.request(method,url,headers=headers,json=payload)
        if not response.is_success:
            error=response.json().get('error',{})
            raise BusinessError(error.get('message','接口调用失败'),response.status_code,error.get('code','HTTP_ERROR'))
        data=response.json()
        deadline=time.monotonic()+180
        while verb=='run' and wait and data['status']=='running':
            if time.monotonic()>deadline: raise BusinessError('等待超时，任务仍可通过 get 查询',408,'WAIT_TIMEOUT')
            await asyncio.sleep(0.5)
            response=await http.get(base+'/'+task_id,headers=headers)
            response.raise_for_status(); data=response.json()
        return data


def register_commands(app_id):
    business=typer.Typer(help=application(app_id)['name'])
    tasks=typer.Typer(help='任务管理，本地与 HTTP 共用业务服务契约')
    business.add_typer(tasks,name='tasks')
    app.add_typer(business,name=app_id)

    @tasks.command('create')
    def create_task(ctx:typer.Context,text:str=typer.Option(''),title:str=typer.Option('文本分析任务'),
                    input_file:str=typer.Option('','--input'),request_key:str=typer.Option('')):
        perform(invoke(ctx,app_id,'create',payload=input_json(text,title,input_file),request_key=request_key or None))

    @tasks.command('list')
    def list_tasks(ctx:typer.Context): perform(invoke(ctx,app_id,'list'))

    @tasks.command('get')
    def get_task(ctx:typer.Context,task_id:str): perform(invoke(ctx,app_id,'get',task_id))

    @tasks.command('run')
    def run_task(ctx:typer.Context,task_id:str,wait:bool=typer.Option(True,'--wait/--no-wait')):
        perform(invoke(ctx,app_id,'run',task_id,wait=wait))

    @tasks.command('cancel')
    def cancel_task(ctx:typer.Context,task_id:str): perform(invoke(ctx,app_id,'cancel',task_id))

    @tasks.command('result')
    def result(ctx:typer.Context,task_id:str): perform(invoke(ctx,app_id,'result',task_id))


for app_id in applications(): register_commands(app_id)
