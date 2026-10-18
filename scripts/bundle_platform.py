"""把已验明 SHA 的源码组件组装成一个平台镜像；部分构建不能标记为发布。"""
import argparse
import json
import os
import threading
import time
from uuid import uuid4
from pathlib import Path
import subprocess
from build_components import RECIPES,monitor
from sync_upstreams import ROOT


def validate_release(metadata,partial):
    missing=set(metadata['required_components'])-set(metadata['components'])
    if missing and not partial:
        raise RuntimeError('正式平台缺少必须制品：'+','.join(sorted(missing)))


def prepare(components,partial=False):
    metadata={'required_components':list(RECIPES)+['agent'],'partial':partial,'components':{}}
    stages=[]
    copies=[]
    for index,name in enumerate(components):
        tag=f'anp-build/{name}:source'
        image=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
        config=image['Config']
        revision=subprocess.check_output(['git','-C',str(ROOT/'third_party'/RECIPES[name][0]),'rev-parse','HEAD'],text=True).strip()
        if config.get('Labels',{}).get('org.opencontainers.image.revision')!=revision:
            raise RuntimeError(f'{name} 的镜像与 submodule SHA 不一致，必须先重建')
        metadata['components'][name]={'image':image['Id'],'revision':revision,
            'build_recipe':config.get('Labels',{}).get('dev.anp.build.recipe'),
            'build_profile':config.get('Labels',{}).get('dev.anp.build.profile','legacy'),
            'environment':config.get('Env') or [],'user':config.get('User') or '',
            'command':(config.get('Entrypoint') or [])+(config.get('Cmd') or []),
            'working_dir':config.get('WorkingDir') or '/'}
        stages.append(f'FROM {tag} AS component_{index}')
        parent='platform_base' if index==0 else f'platform_root_{index-1}'
        copies.append(f'FROM {parent} AS platform_root_{index}\nCOPY --from=component_{index} / /opt/anp/roots/{name}/')
    graph_revision=subprocess.check_output(['git','-C',str(ROOT/'third_party/langgraph'),'rev-parse','HEAD'],text=True).strip()
    metadata['components']['agent']={'image':'platform-python-runtime','revision':graph_revision,'rootfs':False,
        'environment':['PATH=/opt/anp/.venv/bin:/usr/local/bin:/usr/bin:/bin','ANP_ROOT=/opt/anp',
            'PYTHONPATH=/opt/anp/extensions:/opt/anp/src:/opt/anp'],
        'user':'','working_dir':'/state/agent','command':['/opt/anp/.venv/bin/python','-m','anp_platform.agent']}
    validate_release(metadata,partial)
    destination=ROOT/'.local/build/platform'
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'components.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf8')
    manifest=ROOT/'.local/build/dependencies.json'
    if manifest.exists():
        dependencies=json.loads(manifest.read_text(encoding='utf8'))
        for entry in dependencies['dependencies']:
            actual=subprocess.check_output(['git','-C',str(ROOT/entry['path']),'rev-parse','HEAD'],text=True).strip()
            if actual!=entry['commit']:raise RuntimeError('依赖清单与源码不一致：'+entry['path'])
    else:
        dependencies={'updated_to_remote_head':False,'dependencies':[
            {'name':name,'commit':item['revision']} for name,item in metadata['components'].items()]}
    (destination/'dependencies.json').write_text(json.dumps(dependencies,ensure_ascii=False,indent=2),encoding='utf8')
    dockerfile='\n'.join(stages)+'''
FROM python:3.12-slim AS platform_base
RUN apt-get update && apt-get install -y --no-install-recommends nginx tini ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /usr/local/bin/uv
WORKDIR /opt/anp
'''+ '\n'.join(copies)+f'\nFROM platform_root_{len(components)-1} AS platform_runtime\n'+'''
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY --from=graph_source / /tmp/anp-langgraph/
RUN uv pip install --python /opt/anp/.venv/bin/python --no-deps /tmp/anp-langgraph && uv pip check --python /opt/anp/.venv/bin/python && rm -rf /tmp/anp-langgraph && mkdir -p /state/agent
ENV PATH="/opt/anp/.venv/bin:$PATH" PYTHONPATH=/opt/anp:/opt/anp/src ANP_ROOT=/opt/anp
COPY platform/runtime/anp_platform ./anp_platform
COPY platform/runtime/www ./www
COPY config/settings.example.toml ./config/settings.example.toml
COPY migrations ./migrations
COPY --from=platform_manifest components.json ./components.json
COPY --from=platform_manifest dependencies.json ./dependencies.json
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=90s --retries=12 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/_platform/health',timeout=2)"
ENTRYPOINT ["/usr/bin/tini","-g","--","python","-m","anp_platform.supervisor"]
'''
    path=destination/'Dockerfile'
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
    dockerfile+=f'\nLABEL org.opencontainers.image.source="https://github.com/100apps/anp" org.opencontainers.image.revision="{revision}" dev.anp.source_dirty="{str(dirty).lower()}"\n'
    path.write_text(dockerfile,encoding='utf8')
    # 平台上下文白名单：业务源码、私有配置和整个 third_party 不进入最终组装上下文。
    (destination/'Dockerfile.dockerignore').write_text('**\n!pyproject.toml\n!uv.lock\n!src/\n!src/**\n!platform/\n!platform/runtime/\n!platform/runtime/**\n!config/\n!config/settings.example.toml\n!migrations/\n!migrations/**\n**/__pycache__/\n',encoding='utf8')
    return path


def build_image(dockerfile,tag,checkpoints=(),*,context=ROOT,build_contexts=None):
    """逐个物化大层，再导出最终镜像；任何检查点失败都不更新最终标签。"""
    if any(not name.startswith('platform_root_') or not name.removeprefix('platform_root_').isdigit() for name in checkpoints):
        raise ValueError('检查点只能是生成器的组件根目录阶段')
    build_contexts=build_contexts or {}
    command=['docker','build','--progress','plain']
    for name,path in build_contexts.items():command+=['--build-context',name+'='+str(path)]
    command+=['-f',str(dockerfile)]
    owned=[];durations=[];identity=uuid4().hex
    try:
        for index,target in enumerate(checkpoints):
            temporary=f'anp-build/platform-checkpoint:{identity}-{index}'
            started=time.monotonic()
            subprocess.run(command+['--target',target,'-t',temporary,str(context)],check=True)
            owned.append(temporary)
            durations.append({'target':target,'seconds':round(time.monotonic()-started,3)})
        started=time.monotonic()
        subprocess.run(command+['-t',tag,str(context)],check=True)
        durations.append({'target':'final','seconds':round(time.monotonic()-started,3)})
        return durations
    finally:
        # 仅移除本次 UUID 下已成功建立的临时标签；不清理其他镜像、缓存或卷。
        for temporary in owned:
            subprocess.run(['docker','image','rm',temporary],check=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--components',nargs='+',choices=sorted(RECIPES))
    parser.add_argument('--tag',default='anp/platform:v3')
    parser.add_argument('--checkpoint-exports',action='store_true',help='依次导出组件根目录层，避免最终集中导出的峰值压力')
    parser.add_argument('--partial',action='store_true',help='仅做整合测试；组件缺失时健康接口仍返回 503')
    args=parser.parse_args()
    selected=args.components or list(RECIPES)
    if set(selected)!=set(RECIPES) and not args.partial:parser.error('正式构建必须包含全部平台组件')
    if args.partial and (args.tag.endswith(':latest') or args.tag.endswith(':v3')):parser.error('部分构建不能使用正式标签')
    file=prepare(selected,args.partial)
    stopping=threading.Event();thread=None
    if os.environ.get('GITHUB_ACTIONS')=='true':
        thread=threading.Thread(target=monitor,args=(stopping,),daemon=True);thread.start()
    try:
        checkpoints=tuple(f'platform_root_{i}' for i in range(len(selected))) if args.checkpoint_exports else ()
        durations=build_image(file,args.tag,checkpoints,build_contexts={
            'platform_manifest':file.parent,'graph_source':ROOT/'third_party/langgraph/libs/langgraph'})
        print(json.dumps({'checkpoint_exports':args.checkpoint_exports,'build_stages':durations},ensure_ascii=False))
    finally:
        stopping.set()
        if thread:thread.join(timeout=1)
