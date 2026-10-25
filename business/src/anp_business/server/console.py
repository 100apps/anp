"""管理员控制台：限定到当前原生包；复制 Business 后仍可查看代理配置。"""
from contextlib import contextmanager
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from anp_business.shared.config import configuration, BUSINESS_ROOT

router = APIRouter(prefix='/api/console')
JOBS = {}
TASKS = set()
POLICIES = [
    {"id":"rustfs", "name":"RustFS", "mode":"手动登录", "note":"对象存储后台经 Agent Server 管理员鉴权代理；使用 Access Key 和 Secret Key 登录，S3 签名认证保留。"},
    {'id': 'dbx', 'name': 'DBX', 'mode': '统一登录', 'note': '原生包关闭 DBX 独立密码，仅监听回环地址；代理由 Agent Server 校验管理员身份。'},
    {'id': 'grafana', 'name': 'Grafana', 'mode': '代理认证', 'note': '原生 Grafana 信任 Agent Server 注入的身份头；客户端同名头会被移除。外部 Grafana 需自行配置代理认证。'},
    {'id': 'supabase', 'name': 'Supabase Studio', 'mode': '统一登录', 'note': 'Studio 无独立登录；Data API、Auth 和 Storage 的 JWT/RLS 权限仍保留。原生单入口使用同源 /project/default，无需额外登录；独立 Business 未启用 ingress 时跳转到配置地址。'},
    {'id': 'langfuse', 'name': 'Langfuse', 'mode': '手动登录', 'note': '管理界面需要自身用户会话；关闭用户名密码会禁用这种登录方式，不代表免登录。'},
    {'id': 'litellm', 'name': 'LiteLLM', 'mode': '手动登录', 'note': '管理界面保留官方管理员登录；模型 API 的 master key 继续保留。原生单入口使用 /litellm/；独立 Business 未启用 ingress 时跳转到配置地址。'},
]


async def body(request):
    try:
        value = await request.json()
    except ValueError:
        raise HTTPException(400, '请输入有效 JSON') from None
    if not isinstance(value, dict):
        raise HTTPException(400, '配置请求必须为 JSON 对象')
    return value


def root():
    value = os.getenv('ANP_NATIVE_ROOT')
    if not value:
        return None
    path = Path(value).resolve()
    if not (path / 'runtime/console_control.py').is_file() or not (path / 'anp').is_file():
        raise HTTPException(503, '当前原生包未安装控制台管理桥')
    return path


def override_path():
    return Path(os.getenv('ANP_BUSINESS_OVERRIDES', str(BUSINESS_ROOT / 'var/console-platforms.json')))


def revision(path):
    return hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest()


@contextmanager
def configuration_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.lock')
    with lock_path.open('a+b') as lock:
        os.chmod(lock_path, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if lock.tell() == 0:
                    lock.write(b'0'); lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise HTTPException(409, '配置正在修改，请稍后重试') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def mapping_snapshot():
    path = override_path()
    with configuration_lock(path):
        config = configuration()
        return {'platforms': config['platforms'], 'ingress': config['server'], 'unified': config['ingress']['enabled'], 'mapping_revision': revision(path)}


def private_write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.console-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def native(action, data=None):
    directory = await asyncio.to_thread(root)
    if directory is None:
        raise HTTPException(409, '独立 Business 未连接原生包，不能启停宿主服务')
    process = await asyncio.create_subprocess_exec(str(directory / 'tools/python/bin/python3'), '-B', str(directory / 'runtime/console_control.py'), action,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=directory, env={k:v for k,v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'TMPDIR')})
    try:
        output, _ = await asyncio.wait_for(process.communicate(json.dumps(data or {}).encode()), timeout=1800)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise HTTPException(504, '管理命令超时，请检查本目录服务日志') from None
    try:
        value = json.loads(output)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(503, '管理桥未返回有效结果，请查看本目录日志') from None
    if process.returncode:
        raise HTTPException(409, value.get('detail', '操作失败'))
    return value


def no_store(value):
    return JSONResponse(value, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


@router.get('/state')
async def state():
    directory = await asyncio.to_thread(root)
    managed = await native('status') if directory else {'services': [], 'configuration': None, 'revision': None}
    mappings = await asyncio.to_thread(mapping_snapshot)
    return no_store({**managed, **mappings, 'managed': bool(directory), 'authentication': POLICIES,
                     'note': '底层服务仅监听 127.0.0.1。端口映射仅代理 HTTP/WebSocket；数据库 TCP 不对外映射。'})


@router.post('/credentials/{platform}')
def credentials(platform: str):
    fields = {'langfuse': ('admin@anp.local', 'langfuse-user-password'), 'litellm': ('admin', 'litellm'), 'rustfs': ('', 's3')}
    if platform not in fields:
        raise HTTPException(404, '此平台无需额外登录，或凭据由外部平台维护')
    directory = root()
    if not directory:
        raise HTTPException(409, '外部平台的凭据由平台管理员维护')
    # 改到外部上游后不能把本包凭据错误地当作该平台的密码。
    target = urlsplit(configuration()['platforms'][platform])
    config_path = directory / 'config/anp.json'
    if not config_path.exists():
        config_path = directory / 'defaults/anp.json'
    ports = {'rustfs-console': 59003, **json.loads(config_path.read_text())['ports']}
    name = 'langfuse-web' if platform == 'langfuse' else 'rustfs-console' if platform == 'rustfs' else platform
    if target.hostname != '127.0.0.1' or target.port != ports[name]:
        raise HTTPException(409, '已配置外部平台，请使用外部平台账号')
    values = json.loads((directory / 'var/secrets/runtime.json').read_text())
    username, key = fields[platform]
    if platform == "rustfs":
        username = values["s3-access"]
    if not values.get(key):
        raise HTTPException(409, '尚未生成此平台的凭据')
    return no_store({'username': username, 'password': values[key], 'note': '本包初始化凭据；平台内修改密码后请使用新密码。请勿分享或录屏。'})


@router.put('/mappings')
async def mappings(request: Request):
    return await asyncio.to_thread(save_mappings, await body(request))

def save_mappings(data):
    with configuration_lock(override_path()):
        return save_mappings_locked(data)

def save_mappings_locked(data):
    path = override_path()
    if data.get('revision') != revision(path):
        raise HTTPException(409, '配置已变化，请刷新后重试')
    values = data.get('platforms')
    defaults = configuration()['platforms']
    if not isinstance(values, dict) or set(values) != set(defaults):
        raise HTTPException(400, '平台代理配置字段不完整')
    for value in values.values():
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c in value for c in '\r\n\\'):
                raise ValueError()
            parsed.port
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, '上游必须为无凭据、无查询参数的 HTTP(S) URL') from None
    host = data.get('host', '127.0.0.1')
    if host not in ('127.0.0.1', '0.0.0.0'):
        raise HTTPException(400, '入口监听地址无效')
    private_write(path, {'platforms': values, 'server': {'host': host}})
    return no_store({'saved': True, 'detail': '代理地址即时生效；入口监听修改需 ./anp stop 后 ./anp start。仅 Agent Server 可对局域网开放，始终需要登录。'})


@router.put('/configuration')
async def configure(request: Request):
    return no_store(await native('configure', await body(request)))


@router.post('/services/{service}/{action}')
async def operate(service: str, action: str):
    if action not in ('start', 'stop'):
        raise HTTPException(400, '无效操作')
    inventory = await native('status')
    value = next((item for item in inventory['services'] if item['id'] == service), None)
    if not value or not value['can_' + action]:
        raise HTTPException(409, '当前服务不能执行此操作，请查看状态和维护说明')
    if len([value for value in JOBS.values() if value['state'] == 'running']):
        raise HTTPException(409, '已有管理任务在执行')
    identifier = uuid.uuid4().hex
    JOBS[identifier] = {'id': identifier, 'service': service, 'action': action, 'state': 'running'}
    async def work():
        try:
            await native(action, {'service': service})
            JOBS[identifier]['state'] = 'succeeded'
        except HTTPException as error:
            JOBS[identifier].update(state='failed', detail=error.detail)
    task = asyncio.create_task(work())
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    while len(JOBS) > 32:
        del JOBS[next(iter(JOBS))]
    return no_store(JOBS[identifier])


@router.get('/jobs/{identifier}')
async def job(identifier: str):
    if identifier not in JOBS:
        raise HTTPException(404, '任务不存在或服务已重启，请刷新服务状态')
    return no_store(JOBS[identifier])


@router.get('/oauth')
def oauth_configuration():
    from anp_business.server.oauth import settings, config_path
    with configuration_lock(config_path()):
        return no_store({'configuration': settings(), 'revision': revision(config_path())})


@router.put('/oauth')
async def oauth_save(request: Request):
    return await asyncio.to_thread(save_oauth, await body(request))

def save_oauth(data):
    from anp_business.server.oauth import config_path
    with configuration_lock(config_path()):
        return save_oauth_locked(data)

def save_oauth_locked(data):
    from anp_business.server.oauth import config_path, DEFAULT
    path = config_path()
    if data.get('revision') != revision(path):
        raise HTTPException(409, 'OAuth 配置已变化，请刷新后重试')
    values = data.get('configuration')
    if not isinstance(values, dict) or set(values) != set(DEFAULT):
        raise HTTPException(400, 'OAuth 配置字段不完整')
    # 使用同一个校验器，临时文件不落地、不覆盖已配置值。
    from anp_business.server.oauth import validate_settings
    try:
        validate_settings(values)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    private_write(path, values)
    return no_store({'saved': True, 'detail': 'OAuth 配置已保存；issuer 留空即关闭。客户端密钥只从指定私有文件读取。'})
