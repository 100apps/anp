"""只渲染配置，不启动或变更任何容器。"""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def render(profiles):
    env = os.environ.copy()
    env['COMPOSE_PROFILES'] = profiles
    result = subprocess.run(['docker', 'compose', '-p', 'anp2-next', 'config', '--format', 'json'],
                            cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


def test_external_pg_does_not_enable_local_postgres():
    config = render('supabase,litellm,langfuse,business')
    assert 'postgres' not in config['services']
    for name, service in config['services'].items():
        assert 'postgres' not in service.get('depends_on', {}), name


def test_local_storage_uses_separate_bind_directories():
    config = render('local-pg,local-redis,local-clickhouse,local-s3')
    assert config['name'] == 'anp2-next'
    for name in ('postgres', 'redis', 'clickhouse', 'rustfs'):
        mounts = config['services'][name]['volumes']
        assert any(m['type'] == 'bind' and m['source'].endswith('/' + name) for m in mounts)
        assert all('e:/anp/' not in m['source'].replace('\\', '/').lower() for m in mounts)
        assert all(p['host_ip'] == '127.0.0.1' for p in config['services'][name]['ports'])


def test_source_build_contexts_have_real_git_submodules_and_dockerfiles():
    config = render('dbx,langfuse,litellm,supabase,business')
    for name, service in config['services'].items():
        if 'build' not in service:
            continue
        context = Path(service['build']['context'])
        recipe = context / service['build']['dockerfile']
        assert recipe.is_file(), name
        if name != 'business':
            assert (context / '.git').is_file(), name


def test_remote_platform_mode_only_runs_business_and_its_init():
    config = render('business')
    assert set(config['services']) == {'business', 'business-permissions'}


def test_platform_prefixes_and_storage_gateway_port_agree():
    services = render('dbx,langfuse,litellm,supabase,business')['services']
    assert services['dbx']['environment']['DBX_PUBLIC_BASE_PATH'] == '/dbx'
    assert services['langfuse-web']['build']['args']['NEXT_PUBLIC_BASE_PATH'] == '/langfuse'
    assert services['supabase-studio']['build']['args']['NEXT_PUBLIC_BASE_PATH'] == '/supabase'
    assert services['supabase-storage']['environment']['SERVER_PORT'] == '5000'
    assert services['litellm']['environment']['SERVER_ROOT_PATH'] == '/litellm'
    for key in ('DBX', 'LANGFUSE', 'LITELLM', 'SUPABASE', 'SUPABASE_API'):
        assert services['business']['environment']['ANP_PLATFORM_' + key + '_URL'].startswith('http://')
