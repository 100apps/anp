"""验证实际submodule提交与版本锁；使用已发布镜像时无需检出平台源码。"""
import json
import hashlib
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FOLDERS={'dbx':'dbx','langfuse-web':'langfuse','langfuse-worker':'langfuse','litellm':'litellm',
         'supabase-studio':'supabase','supabase-auth':'components/auth','supabase-rest':'components/postgrest',
         'supabase-meta':'components/postgres-meta','supabase-storage':'components/storage','postgres':'components/postgres'}


def main():
    lock=json.loads((ROOT/'docker/toolbox/versions.lock.json').read_text(encoding='utf-8'))
    for name,folder in FOLDERS.items():
        path=ROOT/'platform/sources'/folder
        if not (path/'.git').exists():
            raise SystemExit('请先运行 make sources：缺少 '+folder)
        actual=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
        assert actual==lock['components'][name]['source_sha'], name+' 源码与版本锁不一致'
    for name,architectures in lock['artifacts'].items():
        for arch,artifact in architectures.items():
            assert artifact['url'].startswith('https://github.com/') and len(artifact['sha256'])==64,(name,arch)
    recipes=json.loads((ROOT/'docker/build/recipes.lock.json').read_text(encoding='utf-8'))
    for name,recipe in recipes.items():
        assert hashlib.sha256((ROOT/'docker/build/recipes'/name).read_bytes()).hexdigest()==recipe['template_sha256'],name+' 配方快照校验失败'
    print('源码提交、双架构产物与校验值检查通过')


if __name__=='__main__':main()
