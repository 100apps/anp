"""CLI 会话只存在本机私有运行目录，不写入应用配置。"""
import json
import os
import time
from anp.sdk.http import async_client
from anp import ROOT
from anp.config import value, secret
from anp.errors import BusinessError
from anp.auth.identity import actor_from_token


def session_path():
    profile = os.environ.get('ANP_PROFILE', 'default')
    if not profile.replace('-', '').replace('_', '').isalnum():
        raise BusinessError('会话名称只能包含字母、数字、横线与下划线')
    return ROOT / '.local/auth' / (profile + '.json')


def save_session(data):
    file = session_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data), encoding='utf-8')
    os.chmod(file, 0o600)


async def login(email, password):
    async with async_client(timeout=20, trust_env=False) as client:
        response = await client.post(value('supabase', 'url') + '/auth/v1/token?grant_type=password',
            headers={'apikey': secret('ANON_KEY')}, json={'email': email, 'password': password})
    if not response.is_success:
        raise BusinessError('邮箱或密码不正确', 401, 'LOGIN_FAILED')
    data = response.json()
    data['expires_at'] = time.time() + data.get('expires_in', 3600)
    actor = await actor_from_token(data['access_token'])
    save_session(data)
    return {'user_id': actor.user['id'], 'email': actor.user['email'], 'workspace_id': actor.workspace_id}


async def current_actor():
    path = session_path()
    if not path.exists():
        raise BusinessError('请先执行 anp auth login', 401, 'UNAUTHENTICATED')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['expires_at'] < time.time() + 60:
        async with async_client(timeout=20, trust_env=False) as client:
            response = await client.post(value('supabase', 'url') + '/auth/v1/token?grant_type=refresh_token',
                headers={'apikey': secret('ANON_KEY')}, json={'refresh_token': data['refresh_token']})
        if not response.is_success:
            raise BusinessError('登录已失效，请重新登录', 401, 'UNAUTHENTICATED')
        data = response.json()
        data['expires_at'] = time.time() + data.get('expires_in', 3600)
        save_session(data)
    return await actor_from_token(data['access_token'], os.environ.get('ANP_WORKSPACE'))
