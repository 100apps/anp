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
    assert any(value.startswith('/data/run:') for value in data['services']['toolbox']['tmpfs'])
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
    bases=json.loads((ROOT/'docker/build/base-images.lock.json').read_text())['images']
    assert 'FROM debian:trixie-slim@'+bases['debian:trixie-slim']['digest']+' AS assembled' in text
    assert ':source' not in text and 'anp3/' not in text
    assert 'AS postgres-build' in text and 'fetch.py dbx' in text
    assert '"$TARGETARCH"' in text
    assert 'AS realtime-build' in text and 'bin/pgbouncer' not in text
    assert '--from=ghcr.io/' not in text
    assert 'docker/packages/meta/package-lock.json' in text
    assert 'uv sync --frozen' in text and '--no-build-package litellm' in text


def test_locked_sources_match_real_submodule_commits():
    lock=json.loads((ROOT/'docker/toolbox/versions.lock.json').read_text(encoding='utf-8'))
    assert lock['architectures']==['linux/amd64','linux/arm64']
    assert lock['components']['litellm']['upstream_ref']=='v1.106.0'
    assert lock['components']['supabase-studio']['upstream_ref']=='v1.26.08'
    folders={'langfuse-web':'langfuse','langfuse-worker':'langfuse',
             'supabase-studio':'supabase','supabase-auth':'components/auth',
             'supabase-storage':'components/storage','postgres':'components/postgres','supabase-realtime':'components/realtime'}
    for name,folder in folders.items():
        sha=subprocess.check_output(['git','ls-files','--stage','platform/sources/'+folder],cwd=ROOT,text=True).split()[1]
        assert sha==lock['components'][name]['source_sha'], name
