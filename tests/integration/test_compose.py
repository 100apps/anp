"""只渲染新项目配置；不触碰参考项目的容器与数据。"""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def render(file='compose.yaml'):
    return json.loads(subprocess.check_output(['docker','compose','-p','anp-next','-f',file,'config','--format','json'],
                                            cwd=ROOT,text=True,encoding='utf-8'))


def test_runtime_has_one_toolbox_and_one_persistent_root():
    data=render()
    assert data['name']=='anp-next'
    assert set(data['services'])=={'toolbox'}
    assert not data['services']['toolbox'].get('build')
    mounts=data['services']['toolbox']['volumes']
    assert len(mounts)==1 and mounts[0]['type']=='bind' and mounts[0]['target']=='/data'
    source=mounts[0]['source'].replace('\\','/').lower()
    assert '/anp2/' not in source and source!='/var/lib/anp/data'


def test_runtime_does_not_require_docker_socket_or_privileged_access():
    service=render()['services']['toolbox']
    assert not service.get('privileged',False)
    assert not service.get('cap_add')
    assert all('docker.sock' not in str(m) for m in service['volumes'])
    assert all(p['host_ip']=='127.0.0.1' for p in service['ports'])
    assert service['init'] and int(service['mem_limit'])>0


def test_build_is_single_file_and_has_no_local_component_image_inputs():
    text=(ROOT/'docker/toolbox/Dockerfile').read_text(encoding='utf-8')
    assert 'FROM debian:trixie-slim AS assembled' in text
    assert ':source' not in text and 'anp3/' not in text
    assert 'AS postgres-build' in text and 'AS dbx-backend' in text
    assert '"$TARGETARCH"' in text


def test_locked_sources_match_real_submodule_commits():
    lock=json.loads((ROOT/'docker/toolbox/versions.lock.json').read_text(encoding='utf-8'))
    assert lock['architectures']==['linux/amd64','linux/arm64']
    assert lock['components']['litellm']['upstream_ref']=='v1.106.0'
    assert lock['components']['supabase-studio']['upstream_ref']=='v1.26.08'
    folders={'dbx':'dbx','langfuse-web':'langfuse','langfuse-worker':'langfuse','litellm':'litellm',
             'supabase-studio':'supabase','supabase-auth':'components/auth','supabase-rest':'components/postgrest',
             'supabase-meta':'components/postgres-meta','supabase-storage':'components/storage','postgres':'components/postgres'}
    for name,folder in folders.items():
        sha=subprocess.check_output(['git','ls-files','--stage','platform/sources/'+folder],cwd=ROOT,text=True).split()[1]
        assert sha==lock['components'][name]['source_sha'], name
