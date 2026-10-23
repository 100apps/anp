"""实际服务图的选服、启动顺序、固定命令及凭据隔离契约。"""
from collections import defaultdict
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'docker/toolbox/runtime'))
import settings
import supervisor


@pytest.fixture
def graph(monkeypatch,tmp_path):
    monkeypatch.setattr(settings,'DATA',tmp_path)
    return settings.definitions(defaultdict(lambda:'a'*64),
                                {'node_web_mb':1024,'node_worker_mb':768,'node_studio_mb':512})


def test_single_redis_does_not_start_other_services(graph):
    assert supervisor.selection('redis',graph)==['redis']


def test_langfuse_requires_all_four_storage_services(graph):
    order=supervisor.selection('langfuse',graph)
    assert set(('postgres','redis','clickhouse','rustfs')).issubset(order)
    assert order.index('langfuse-web')<order.index('langfuse-worker')


def test_all_services_have_a_topological_order_without_duplicates(graph):
    order=supervisor.selection('all',graph)
    assert len(order)==len(graph)==len(set(order))
    for name in order:
        for dependency in graph[name]['deps']:
            assert order.index(dependency)<order.index(name)


def test_external_dependencies_can_be_configured_without_local_storage(graph):
    assert supervisor.selection('langfuse-web',graph,auto=False)==['langfuse-web']


def test_unknown_or_shell_commands_are_not_service_names(graph):
    with pytest.raises(ValueError,match='未知'):
        supervisor.selection('redis; echo unsafe',graph)


def test_cycle_is_rejected_instead_of_deadlocking_startup(graph):
    graph['langfuse-web']['deps'].append('langfuse-worker')
    with pytest.raises(ValueError,match='循环'):
        supervisor.selection('langfuse',graph)


def test_each_service_uses_a_unique_non_root_identity_and_port(graph):
    assert len({s['uid'] for s in graph.values()})==len(graph)
    assert all(s['uid']>0 for s in graph.values())
    assert len({s['port'] for s in graph.values()})==len(graph)


def test_children_do_not_inherit_unrelated_parent_credentials(graph,monkeypatch):
    monkeypatch.setenv('ANOTHER_PLATFORM_ADMIN_PASSWORD','do-not-forward')
    env=supervisor.environment(graph['dbx'])
    assert 'ANOTHER_PLATFORM_ADMIN_PASSWORD' not in env
    assert 'POSTGRES_PASSWORD' not in env and 'DBX_PASSWORD' in env


def test_platform_paths_and_private_data_paths_remain_separate(graph):
    assert graph['dbx']['env']['DBX_PUBLIC_BASE_PATH']=='/dbx'
    assert graph['litellm']['env']['SERVER_ROOT_PATH']=='/litellm'
    assert graph['langfuse-web']['env']['NEXT_PUBLIC_BASE_PATH']=='/langfuse'
    assert graph['supabase-studio']['env']['NEXT_PUBLIC_BASE_PATH']=='/supabase'
    assert graph['postgres']['command'][0].startswith('/nix/')


def test_realtime_group_includes_postgres_auth_and_gateway(graph):
    order=supervisor.selection('supabase',graph)
    assert 'supabase-realtime' in order
    assert order.index('postgres') < order.index('supabase-realtime') < order.index('supabase-gateway')
    assert graph['supabase-realtime']['env']['SELF_HOST_TENANT_NAME']=='realtime-dev'
    assert not {'pgbouncer','supavisor','vector','logflare','imgproxy','edge-runtime'} & set(graph)
