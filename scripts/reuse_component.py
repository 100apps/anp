"""只在源码 SHA 与构建配方一致时，复用本仓库前次 CI 的组件制品。"""
import argparse
import json
import os
import re
import subprocess
from build_components import RECIPES,fingerprint
from sync_upstreams import ROOT,git


def reuse(component,run):
    if not run:return False
    if not re.fullmatch('[0-9]+',run):raise ValueError('构建编号必须为数字')
    info=json.loads(subprocess.check_output(['gh','api','repos/100apps/anp/actions/runs/'+run],text=True))
    revision=info['head_sha']
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('无效构建提交')
    if info.get('path')!='.github/workflows/platform.yml':raise ValueError('只复用本平台流水线的制品')
    git('fetch','--depth','1','origin',revision)
    image=f'ghcr.io/100apps/anp-build-{component}:run-{run}'
    pulled=subprocess.run(['docker','pull',image])
    if pulled.returncode:
        print('前次制品不可用，重新构建：',component);return False
    actual=json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]
    source=ROOT/'third_party'/RECIPES[component][0]
    expected=git('-C',str(source),'rev-parse','HEAD')
    if actual['Config'].get('Labels',{}).get('org.opencontainers.image.revision')!=expected:
        print('上游已变化，重新构建：',component);return False
    recorded=actual['Config'].get('Labels',{}).get('dev.anp.build.recipe')
    if recorded:
        if recorded!=fingerprint(component):
            print('组件配方或资源配置已变化，重新构建：',component);return False
    else:
        # 兼容早期未写配方摘要的制品，宁可重建也不猜测其构建参数。
        files=['scripts/build_components.py']
        if component=='postgrest':files.append('deploy/build/postgrest.Dockerfile')
        for file in files:
            previous=subprocess.check_output(['git','show',revision+':'+file],cwd=ROOT)
            if previous!=(ROOT/file).read_bytes():
                print('旧制品缺少配方摘要且配方文件已变化，重新构建：',component);return False
    subprocess.run(['docker','tag',image,f'anp-build/{component}:source'],check=True)
    print('已复用同一源码与配方的组件：',component,expected)
    return True


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',choices=sorted(RECIPES),required=True)
    parser.add_argument('--run',default='')
    args=parser.parse_args();hit=reuse(args.component,args.run)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf8') as output:output.write('hit='+str(hit).lower()+'\n')
