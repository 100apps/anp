"""从真实 submodule 构建平台组件中间制品，不部署额外平台容器。"""
import argparse
import subprocess
from sync_upstreams import ROOT,sync

RECIPES={
    'studio':('supabase','apps/studio/Dockerfile','production'),
    'dbx':('dbx','deploy/Dockerfile',None),
    'auth':('supabase-auth','Dockerfile',None),
    'storage':('supabase-storage','Dockerfile',None),
    'meta':('postgres-meta','Dockerfile',None),
    'postgrest':('postgrest','',None),
    'langfuse-web':('langfuse','web/Dockerfile',None),
    'langfuse-worker':('langfuse','worker/Dockerfile',None),
    'litellm':('litellm','Dockerfile',None),
    'lens':('lens','deploy/runtime/Dockerfile','runtime'),
}


def build(component):
    repository,filename,target=RECIPES[component]
    source=ROOT/'third_party'/repository
    dockerfile_path=ROOT/'deploy/build/postgrest.Dockerfile' if component=='postgrest' else source/filename
    dockerfile=dockerfile_path.read_text(encoding='utf8')
    if component=='studio':
        marker='FROM dev AS build-next\n'
        if dockerfile.count(marker)!=1:
            raise RuntimeError('Studio 上游 Dockerfile 结构已变化，需要适配后再构建')
        dockerfile=dockerfile.replace(marker,marker+'ENV NEXT_PUBLIC_BASE_PATH=/supabase\nENV NODE_OPTIONS=--max-old-space-size=6144\n')
        dockerfile=dockerfile.replace('http://localhost:3000/api/platform/profile','http://localhost:3000/supabase/api/platform/profile')
    if component=='dbx':
        marker='FROM --platform=$BUILDPLATFORM node:22-slim AS frontend\n'
        if dockerfile.count(marker)!=1:
            raise RuntimeError('DBX 上游 Dockerfile 结构已变化，需要适配后再构建')
        dockerfile=dockerfile.replace(marker,marker+'ENV VITE_DBX_BASE_PATH=/dbx\n')
        marker='FROM --platform=$BUILDPLATFORM rust:1-bookworm AS backend\n'
        if dockerfile.count(marker)!=1:
            raise RuntimeError('DBX Rust 构建阶段已变化，需要适配')
        dockerfile=dockerfile.replace(marker,marker+'ENV CARGO_BUILD_JOBS=4 CMAKE_BUILD_PARALLEL_LEVEL=4\n')
    if component=='litellm':
        # 上游当前 Turbopack 出现 conflicting effects 内部错误；同一源码用 Next 官方 Webpack 构建。
        marker='RUN npm run build\n'
        if dockerfile.count(marker)!=1:
            raise RuntimeError('LiteLLM UI 构建阶段已变化，需要适配')
        dockerfile=dockerfile.replace(marker,'RUN npm run build -- --webpack\n')
    output=ROOT/'.local/build'/f'{component}.Dockerfile'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(dockerfile,encoding='utf8')
    # 不修改 submodule 的工作区；生成文件不是最终平台镜像。
    command=['docker','build','--progress','plain']
    if target:command+=['--target',target]
    if component=='langfuse-web':command+=['--build-arg','NEXT_PUBLIC_BASE_PATH=/langfuse']
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    command+=['--label','org.opencontainers.image.revision='+revision,
        '-f',str(output),'-t',f'anp-build/{component}:source',str(source)]
    subprocess.run(command,check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',choices=sorted(RECIPES),required=True)
    parser.add_argument('--locked',action='store_true',help='仅用于复现已锁定代码；默认先更新所有依赖')
    args=parser.parse_args()
    # 更新 Git 必须串行；多个组件构建可并行，锁定构建不能再改 Git 索引。
    if not args.locked:
        sync(True)
    build(args.component)
