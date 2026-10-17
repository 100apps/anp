"""平台运行控制的失败可见性与路径边界。"""
import asyncio
from pathlib import Path
import sys
import pytest
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'platform/runtime'))
from anp_platform.gateway import render
from anp_platform.supervisor import Runtime
from anp_platform.child import identity


def test_partial_bundle_is_not_ready_and_missing_services_are_visible():
    settings={'services':{'auth':{},'postgrest':{}}}
    runtime=Runtime(settings,{'components':{'auth':{}}})
    runtime.states['auth']['status']='ready'
    asyncio.run(runtime.service('postgrest',{}))
    assert runtime.states['postgrest']['status']=='missing_artifact'
    assert runtime.ready() is False


def test_gateway_preserves_frontend_prefix_and_strips_api_prefix():
    configuration={'routes':[
        {'path':'/supabase/','target':'127.0.0.1:13000','basic_auth':True},
        {'path':'/supabase/rest/v1/','target':'127.0.0.1:13002','strip_prefix':True}]}
    result=render(configuration)
    assert 'proxy_pass http://service_0;' in result
    assert 'proxy_pass http://service_1/;' in result
    assert 'proxy_buffering off' in result and 'proxy_request_buffering off' in result
    assert 'proxy_set_header Upgrade $http_upgrade' in result
    assert result.count('auth_basic "ANP Supabase 管理"')==1
    for invalid in ['http://example/','/supabase/../','/abc;return 200;/']:
        with pytest.raises(ValueError):render({'routes':[{'path':invalid,'target':'127.0.0.1:1'}]})


def test_child_resolves_upstream_uid_and_gid_before_chroot(tmp_path):
    (tmp_path/'etc').mkdir()
    (tmp_path/'etc/passwd').write_text('nextjs:x:1001:1002::/app:/bin/sh\n')
    (tmp_path/'etc/group').write_text('nodejs:x:1002:\n')
    assert identity(tmp_path,'nextjs:nodejs')==(1001,1002)
    assert identity(tmp_path,'1001')==(1001,1002)
    assert identity(tmp_path,'')==(0,0)


def test_schema_failure_is_visible_without_exposing_connection_details(monkeypatch,caplog):
    from anp_platform import bootstrap
    def fail(_):raise RuntimeError('postgresql://private-password@example')
    monkeypatch.setattr(bootstrap,'schema',fail)
    spec={'before_start':'schema'}
    runtime=Runtime({'services':{'postgrest':spec}},{'components':{'postgrest':{}}})
    asyncio.run(runtime.service('postgrest',spec))
    assert runtime.states['postgrest']['status']=='initialization_failed'
    assert runtime.ready() is False
    assert 'private-password' not in caplog.text


def test_platform_compose_has_one_container_one_port_and_readonly_flow_deployment():
    root=Path(__file__).resolve().parents[1]
    document=yaml.safe_load((root/'deploy/platform/compose.yaml').read_text(encoding='utf8'))
    assert set(document['services'])=={'platform'}
    service=document['services']['platform']
    assert len(service['ports'])==1
    assert not service.get('privileged') and not service.get('cap_add')
    assert all('docker.sock' not in volume for volume in service['volumes'])
    deployment=next(volume for volume in service['volumes'] if 'ANP_APPLICATIONS_DIR' in volume)
    assert deployment.endswith(':/opt/anp/extensions/apps:ro')
    assert service['stop_grace_period']=='30s'


def test_stop_interrupts_long_restart_backoff():
    async def scenario():
        runtime=Runtime({'services':{}},{'components':{}})
        waiting=asyncio.create_task(runtime.pause(30))
        await asyncio.sleep(0)
        runtime.stop()
        await asyncio.wait_for(waiting,timeout=.2)
    asyncio.run(scenario())
