"""单一配置入口：公开地址、容器地址、基础设施和密钥分别管理。"""
from functools import lru_cache
import os
import tomllib
from dotenv import load_dotenv
from anp import ROOT


@lru_cache
def settings():
    load_dotenv(ROOT / 'config/secrets.local.env', override=False)
    if os.environ.get('ANP_STACK')!='legacy':
        load_dotenv(ROOT/'.local/generated/platform-v3/business.env',override=False)
    file = ROOT / 'config/settings.local.toml'
    if not file.exists():
        file = ROOT / 'config/settings.example.toml'
    return tomllib.loads(file.read_text(encoding='utf-8'))


def value(section, key, default=None):
    result = settings().get(section, {}).get(key, default)
    return os.environ.get('ANP_' + section.upper() + '_' + key.upper(), result)


def secret(name):
    settings()
    result = os.environ.get(name)
    if not result:
        raise RuntimeError(f'缺少密钥配置：{name}；请运行 uv run anp init')
    return result


def postgres_dsn(database='agent_local', account='agent'):
    connection=value('postgres',account+'_dsn_env')
    if connection: return secret(connection)
    host = value('postgres', 'host')
    port = value('postgres', 'port')
    user = value('postgres', account + '_user', account)
    password = secret('AGENT_DB_PASSWORD' if account == 'agent' else 'POSTGRES_PASSWORD')
    sslmode = value('postgres', 'sslmode', 'disable')
    return f'postgresql://{user}:{password}@{host}:{port}/{database}?sslmode={sslmode}'
