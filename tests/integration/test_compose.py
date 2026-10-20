"""只渲染新项目配置；不触碰参考项目的容器与数据。"""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]


def render(file='compose.yaml'):
    return json.loads(subprocess.check_output(['docker','compose','-p','anp3','-f',file,'config','--format','json'],
                                            cwd=ROOT,text=True,encoding='utf-8'))


def test_runtime_has_one_toolbox_and_one_persistent_root():
    data=render()
    assert data['name']=='anp3'
    assert set(data['services'])=={'toolbox'}
    mounts=data['services']['toolbox']['volumes']
    assert len(mounts)==1 and mounts[0]['type']=='bind' and mounts[0]['target']=='/data'
    source=mounts[0]['source'].replace('\\','/').lower()
    assert '/anp3/' in source and '/anp2/' not in source and '/anp/' not in source


def test_runtime_does_not_require_docker_socket_or_privileged_access():
    service=render()['services']['toolbox']
    assert not service.get('privileged',False)
    assert not service.get('cap_add')
    assert all('docker.sock' not in str(m) for m in service['volumes'])
    assert all(p['host_ip']=='127.0.0.1' for p in service['ports'])
    assert service['init'] and int(service['mem_limit'])>0


def test_production_component_builds_are_separate_from_runtime():
    data=render('compose.build.yaml')
    assert len(data['services'])==9
    for name,service in data['services'].items():
        assert not service.get('ports') and not service.get('volumes')
        context=Path(service['build']['context'])
        assert (context/'.git').is_file(), name
        assert (context/service['build']['dockerfile']).is_file(), name
        assert service['image']=='anp3/'+name+':source'
    assert data['services']['langfuse-web']['build']['args']['NEXT_PUBLIC_BASE_PATH']=='/langfuse'
    assert data['services']['supabase-studio']['build']['args']['NEXT_PUBLIC_BASE_PATH']=='/supabase'


def test_locked_sources_match_real_submodule_commits():
    lock=json.loads((ROOT/'docker/toolbox/versions.lock.json').read_text(encoding='utf-8'))
    for component in lock['components'].values():
        assert ':latest' not in component['image']
    assert lock['components']['litellm']['upstream_ref']=='v1.106.0'
    assert lock['components']['supabase-studio']['upstream_ref']=='v1.26.08'
    folders={'dbx':'dbx','langfuse-web':'langfuse','langfuse-worker':'langfuse','litellm':'litellm',
             'supabase-studio':'supabase','supabase-auth':'components/auth','supabase-rest':'components/postgrest',
             'supabase-meta':'components/postgres-meta','supabase-storage':'components/storage'}
    for name,folder in folders.items():
        sha=subprocess.check_output(['git','-C',str(ROOT/'platform/sources'/folder),'rev-parse','HEAD'],text=True).strip()
        assert sha==lock['components'][name]['source_sha'], name
