"""构建前更新 Git submodule，输出真实提交清单；失败立即终止，不回退旧镜像。"""
import argparse
import json
from pathlib import Path
import subprocess
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True,encoding='utf-8').strip()


def sync(update=True):
    git('submodule','sync','--recursive')
    git('submodule','update','--init','--recursive','--jobs','4')
    names=git('config','-f','.gitmodules','--get-regexp',r'^submodule\..*\.path$')
    result=[]
    for line in names.splitlines():
        key,path=line.split(maxsplit=1)
        name=key.removeprefix('submodule.').removesuffix('.path')
        if update:
            if git('-C',path,'status','--porcelain'):
                raise RuntimeError('依赖源码有未提交改动，拒绝覆盖：'+path)
            git('-C',path,'fetch','--depth','1','origin','HEAD')
            git('-C',path,'checkout','--detach','FETCH_HEAD')
        result.append({'name':name,'path':path,'url':git('-C',path,'remote','get-url','origin'),
            'commit':git('-C',path,'rev-parse','HEAD')})
    output=ROOT/'.local/build/dependencies.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({'recorded_at':datetime.now(timezone.utc).isoformat(),
        'updated_to_remote_head':update,'dependencies':result},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'dependencies':len(result),'manifest':str(output),'updated':update},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--locked',action='store_true',help='开发复现已锁定版本；发布构建不能使用该选项')
    sync(not parser.parse_args().locked)
