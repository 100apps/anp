"""验证实际submodule提交与版本锁；使用已发布镜像时无需检出平台源码。"""
import json
import hashlib
from pathlib import Path
import subprocess
import tomllib

ROOT=Path(__file__).resolve().parents[1]
FOLDERS={'langfuse-web':'langfuse','langfuse-worker':'langfuse',
         'supabase-studio':'supabase','supabase-auth':'components/auth',
         'supabase-storage':'components/storage','postgres':'components/postgres','supabase-realtime':'components/realtime'}


def main():
    lock=json.loads((ROOT/'docker/toolbox/versions.lock.json').read_text(encoding='utf-8'))
    for name,folder in FOLDERS.items():
        path=ROOT/'platform/sources'/folder
        if not (path/'.git').exists():
            raise SystemExit('请先运行 make sources：缺少 '+folder)
        actual=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
        assert actual==lock['components'][name]['source_sha'], name+' 源码与版本锁不一致'
    for name,architectures in lock['artifacts'].items():
        assert set(architectures)=={'amd64','arm64'}, name+' 缺少目标架构'
        for arch,artifact in architectures.items():
            assert artifact['url'].startswith(('https://github.com/','https://nodejs.org/dist/','https://packages.redis.io/deb/')) and len(artifact['sha256'])==64,(name,arch)
    packages=ROOT/'docker/packages'
    meta=json.loads((packages/'meta/package-lock.json').read_text())['packages']['node_modules/@supabase/postgres-meta']
    assert meta['version']==lock['components']['supabase-meta']['version']
    assert meta['resolved'].startswith('https://registry.npmjs.org/') and meta['integrity'].startswith('sha512-')
    python_lock=tomllib.loads((packages/'litellm/uv.lock').read_text())
    lite=next(package for package in python_lock['package'] if package['name']=='litellm')
    assert lite['version']==lock['components']['litellm']['version']
    for architecture in ('manylinux_2_28_aarch64','manylinux_2_28_x86_64'):
        assert any(architecture in wheel['url'] and wheel['hash'].startswith('sha256:') for wheel in lite['wheels'])
    tools=json.loads((ROOT/'docker/build/tools.lock.json').read_text())
    assert tools['architectures']==lock['architectures']
    for name,artifacts in tools['artifacts'].items():
        assert set(artifacts)=={'amd64','arm64'}, name+' 缺少目标架构'
        for artifact in artifacts.values():
            assert artifact['url'].startswith('https://github.com/') and len(artifact['sha256'])==64
    bases=json.loads((ROOT/'docker/build/base-images.lock.json').read_text())['images']
    generated=(ROOT/'docker/toolbox/Dockerfile').read_text()
    for name,metadata in bases.items():
        assert metadata['digest'].startswith('sha256:') and len(metadata['digest'])==71,name+' 基础镜像摘要错误'
        assert set(metadata['platform_digests'])=={'amd64','arm64'},name+' 基础镜像缺少架构'
        assert all(value.startswith('sha256:') and len(value)==71 for value in metadata['platform_digests'].values())
        assert name+'@'+metadata['digest'] in generated,name+' 未使用锁定基础镜像'
    for line in generated.splitlines():
        if not line.startswith('FROM '):continue
        parts=line.split(); image=parts[2] if parts[1].startswith('--') else parts[1]
        if ':' in image or '/' in image:assert '@sha256:' in image,'存在浮动基础镜像：'+image
    recipes=json.loads((ROOT/'docker/build/recipes.lock.json').read_text(encoding='utf-8'))
    for name,recipe in recipes.items():
        assert hashlib.sha256((ROOT/'docker/build/recipes'/name).read_bytes()).hexdigest()==recipe['template_sha256'],name+' 配方快照校验失败'
    print('源码提交、双架构产物与校验值检查通过')


if __name__=='__main__':main()
