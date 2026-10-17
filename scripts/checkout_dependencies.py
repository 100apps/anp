"""在 CI 各作业复现同一份最新依赖清单，拒绝清单修改 submodule 来源。"""
import argparse
import json
from pathlib import Path
import re
from sync_upstreams import git,ROOT


def checkout(file):
    manifest=json.loads(Path(file).read_text(encoding='utf8'))
    if manifest.get('updated_to_remote_head') is not True:raise ValueError('发布清单必须实际更新上游')
    paths={line.split(maxsplit=1)[1] for line in git('config','-f','.gitmodules','--get-regexp',r'^submodule\..*\.path$').splitlines()}
    entries=manifest['dependencies']
    if len(entries)!=len(paths) or {x['path'] for x in entries}!=paths:raise ValueError('依赖清单与项目不一致')
    git('submodule','update','--init','--recursive','--jobs','4')
    for entry in entries:
        path,commit=entry['path'],entry['commit']
        if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('无效依赖 SHA')
        if git('-C',path,'remote','get-url','origin')!=entry['url']:raise ValueError('依赖来源不一致')
        git('-C',path,'fetch','--depth','1','origin',commit)
        git('-C',path,'checkout','--detach',commit)
    output=ROOT/'.local/build/dependencies.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('已复现依赖清单：',len(entries))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('manifest',type=Path)
    checkout(parser.parse_args().manifest)
