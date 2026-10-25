"""可选 OIDC 登录；Authlib 校验协议，Supabase 签发统一业务会话。"""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from anp_business.server.auth import COOKIE
from anp_business.shared.config import BUSINESS_ROOT, configuration
from anp_business.shared.telemetry import http_client

router = APIRouter(prefix='/api/auth/oauth')
DEFAULT = {'issuer':'', 'client_id':'', 'label':'OAuth 登录', 'redirect_uri':'', 'scopes':'openid profile email',
           'client_secret_file':'', 'service_key_file':'', 'token_endpoint_auth_method':'', 'admin_subjects':[]}


def config_path():
    native = os.getenv('ANP_NATIVE_ROOT')
    return Path(os.getenv('ANP_BUSINESS_OAUTH_FILE', str(Path(native) / 'config/oauth.json' if native else BUSINESS_ROOT / 'var/oauth.json')))


def settings():
    path = config_path()
    values = json.loads(path.read_text()) if path.exists() else {}
    return validate_settings(values)


def validate_settings(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULT):
        raise ValueError('OAuth 配置包含未知字段')
    result = {**DEFAULT, **values}
    if any(not isinstance(result[key], str) for key in DEFAULT if key != 'admin_subjects') or not isinstance(result['admin_subjects'],list) or any(not isinstance(v,str) or not v or v == '*' for v in result['admin_subjects']):
        raise ValueError('OAuth 配置类型无效；管理员须逐个填写 OIDC sub，不能使用通配符')
    if result['token_endpoint_auth_method'] not in ('', 'none', 'client_secret_basic', 'client_secret_post'):
        raise ValueError('客户端认证方式须为 none、client_secret_basic 或 client_secret_post')
    if result['issuer']:
        issuer = urlsplit(result['issuer'])
        callback = urlsplit(result['redirect_uri'])
        if issuer.scheme != 'https' or not issuer.hostname or issuer.username or issuer.password or issuer.query or issuer.fragment:
            raise ValueError('OIDC issuer 必须为无凭据的 HTTPS 地址')
        if callback.scheme != 'https' and not (callback.scheme == 'http' and callback.hostname in ('127.0.0.1','localhost')):
            raise ValueError('OAuth 回调必须使用 HTTPS，本机回环地址可用 HTTP')
        if callback.path != '/api/auth/oauth/callback' or callback.username or callback.password or callback.query or callback.fragment or not result['client_id'] or 'openid' not in result['scopes'].split():
            raise ValueError('请配置 client_id、openid scope 和完整的 /api/auth/oauth/callback 回调地址')
    return result


class OAuthSession:
    """签名 cookie 只保存短期授权 state/nonce/PKCE；不保存 OAuth token。"""
    def __init__(self, app):
        self.app = app
        self.secret = None

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope.get('path','').startswith('/api/auth/oauth/'):
            return await self.app(scope,receive,send)
        if self.secret is None:
            self.secret = await asyncio.to_thread(self.initialize)
        session = SessionMiddleware(self.app, secret_key=self.secret, session_cookie='anp_oauth_flow', max_age=600,
            path='/api/auth/oauth', same_site='lax', https_only=scope.get('scheme') == 'https')
        return await session(scope,receive,send)

    def initialize(self):
        directory = Path(os.getenv('ANP_BUSINESS_DATA_ROOT', str(BUSINESS_ROOT / 'var'))) / 'secrets'
        directory.mkdir(parents=True,exist_ok=True)
        key = directory / 'oauth-cookie-key'
        try:
            fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd,'w') as stream:stream.write(secrets.token_urlsafe(48))
        return key.read_text()


def client(config):
    secret = Path(config['client_secret_file']).read_text().strip() if config['client_secret_file'] else None
    oauth = OAuth()
    return oauth.register('identity', client_id=config['client_id'], client_secret=secret,
        server_metadata_url=config['issuer'].rstrip('/') + '/.well-known/openid-configuration',
        client_kwargs={'scope':config['scopes'], 'code_challenge_method':'S256',
                       'token_endpoint_auth_method':config['token_endpoint_auth_method'] or ('client_secret_basic' if secret else 'none')},
        timeout=15)


async def business_session(claims, config):
    # 以受验证的 issuer+sub 隔离身份，不凭未验证邮箱自动关联或接管已有管理员。
    subject = claims.get('sub')
    if not isinstance(subject,str) or not subject:
        raise ValueError('身份提供方未返回稳定 subject')
    digest = hashlib.sha256((config['issuer'].rstrip('/') + '\n' + subject).encode()).hexdigest()
    email = 'oidc-' + digest + '@oauth.anp.local'
    def load_key():
        if config['service_key_file']:
            service_key = Path(config['service_key_file']).read_text().strip()
        elif os.getenv('ANP_NATIVE_ROOT'):
            service_key = json.loads((Path(os.environ['ANP_NATIVE_ROOT']) / 'var/secrets/runtime.json').read_text())['service-key']
        else:
            raise ValueError('独立 Business 需要配置 service_key_file 才能建立 Supabase 会话')
        return service_key
    service_key = await asyncio.to_thread(load_key)
    base = configuration()['supabase']['url'].rstrip('/') + '/auth/v1/'
    headers = {'apikey':service_key, 'authorization':'Bearer ' + service_key}
    async with http_client(timeout=15, trust_env=False) as http:
        response = await http.post(base+'admin/generate_link', headers=headers, json={'type':'magiclink', 'email':email,
            'data':{'oidc_issuer':config['issuer'], 'oidc_subject':subject, 'display_name':claims.get('name',''), 'display_email':claims.get('email','')}})
        if response.status_code not in (200,201):
            raise ValueError('Supabase 无法建立 OAuth 身份')
        link = response.json()
        user = link.get('user', link)
        identifier = user.get('id')
        if not identifier or not link.get('hashed_token'):
            raise ValueError('Supabase OAuth 会话响应无效')
        # 每次登录重新按显式 subject 清单计算角色；取消清单后不保留旧管理员权限。
        response = await http.put(base+'admin/users/'+identifier, headers=headers,
            json={'app_metadata':{'anp_role':'admin' if subject in config['admin_subjects'] else 'user'}})
        if response.status_code != 200:
            raise ValueError('Supabase 无法设置 OAuth 用户权限')
        response = await http.post(base+'verify', headers=headers, json={'type':link.get('verification_type', 'magiclink'),'token_hash':link['hashed_token']})
        if response.status_code != 200 or not response.json().get('access_token'):
            raise ValueError('Supabase 无法签发 OAuth 会话')
        return response.json()


@router.get('/providers')
async def providers():
    try:
        config = await asyncio.to_thread(settings)
        values = [{'label':config['label'], 'path':'/api/auth/oauth/start'}] if config['issuer'] else []
        return JSONResponse(values,headers={'Cache-Control':'no-store'})
    except (ValueError,OSError):
        return JSONResponse([],headers={'Cache-Control':'no-store'})


@router.get('/start')
async def start(request: Request):
    try:
        config = await asyncio.to_thread(settings)
        if not config['issuer']:
            raise HTTPException(404,'尚未配置 OAuth')
        # 固定回调，不从可伪造 Host/Forwarded 头生成授权目的地址。
        if str(request.base_url).rstrip('/') != config['redirect_uri'].split('/api/auth/oauth/callback')[0]:
            raise HTTPException(400,'请从 OAuth 配置的回调同源地址发起登录')
        request.session.clear()
        provider = await asyncio.to_thread(client, config)
        return await provider.authorize_redirect(request, config['redirect_uri'])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503,'OAuth 配置或身份提供方暂不可用') from None


@router.get('/callback')
async def callback(request: Request):
    stage = 'configuration'
    try:
        config = await asyncio.to_thread(settings)
        provider = await asyncio.to_thread(client, config)
        stage = 'token_validation'
        token = await provider.authorize_access_token(request)
        claims = token.get('userinfo')
        if not claims or claims.get('iss','').rstrip('/') != config['issuer'].rstrip('/'):
            raise ValueError('OIDC issuer 无效')
        stage = 'supabase_session'
        session = await business_session(claims, config)
        response = RedirectResponse('/workbench/', status_code=303, headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        response.set_cookie(COOKIE,session['access_token'],max_age=int(session.get('expires_in',3600)),httponly=True,
            samesite='lax',secure=request.url.scheme=='https',path='/')
        request.session.clear()
        return response
    except Exception as error:
        logging.getLogger(__name__).warning('OAuth callback failed stage=%s error_type=%s', stage, type(error).__name__)
        request.session.clear()
        return JSONResponse({'detail':'OAuth 登录未完成，请重新发起；请管理员检查提供方配置和 Supabase 会话设置。'},status_code=400,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
