"""从真实 submodule 构建平台组件中间制品，不部署额外平台容器。"""
import argparse
import subprocess
import hashlib
import json
import os
import threading
import shutil
from pathlib import Path
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


def recipe(component):
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
    if component=='lens':
        marker='COPY src/ui/ src/ui/\n'
        if dockerfile.count(marker)!=1:
            raise RuntimeError('Lens UI 构建阶段已变化，需要适配子路径')
        dockerfile=dockerfile.replace(marker,marker+
            'ENV NEXT_PUBLIC_LENS_API_URL=/lens\n'+
            '''RUN sed -i 's|basePath: "/ui"|basePath: "/lens/ui"|' src/ui/app/next.config.mjs\n''')
    profile=os.environ.get('ANP_BUILD_PROFILE','default')
    if profile not in ('default','ci'):raise ValueError('未知构建资源配置')
    if profile=='ci':
        if component=='dbx':
            # 托管运行器仅 2 CPU / 7.75 GiB；实测最终 release 阶段内存及交换空间接近耗尽。
            # ThinLTO/16 单元仍耗尽 7.75 GiB + 3 GiB swap；分拆单元并关闭跨 crate LTO。
            # 保留 release 优化级别、panic/strip；CI 制品的运行性能需要另外实测。
            marker='ENV CARGO_BUILD_JOBS=4 CMAKE_BUILD_PARALLEL_LEVEL=4\n'
            if dockerfile.count(marker)!=1:raise RuntimeError('DBX 构建资源配置发生变化，需要适配')
            dockerfile=dockerfile.replace(marker,
                'ENV CARGO_BUILD_JOBS=1 CMAKE_BUILD_PARALLEL_LEVEL=1 '
                'CARGO_PROFILE_RELEASE_LTO=off CARGO_PROFILE_RELEASE_CODEGEN_UNITS=256\n')
        if component=='studio':
            dockerfile=dockerfile.replace('--max-old-space-size=6144','--max-old-space-size=5120')
            marker='RUN pnpm --filter studio exec next build\n'
            if dockerfile.count(marker)!=1:raise RuntimeError('Studio 构建命令已变化，需要适配')
            # 上游只配置了 Turbopack 的文本加载规则；Webpack 也必须保留原始文本。
            webpack='''  webpack(config) {
    // Next 的 tsconfig 路径插件会跳过 .d.ts；这两份文件是 Monaco 所需的原始文本。
    if (!config.context) throw new Error("Studio Webpack context missing");
    config.resolve.alias = {
      ...config.resolve.alias,
      "@/public/deno/edge-runtime.d.ts$": config.context + "/public/deno/edge-runtime.d.ts",
      "@/public/deno/lib.deno.d.ts$": config.context + "/public/deno/lib.deno.d.ts",
    };
    const rawText = /(?:\\.md|(?:^|[\\\\/])(?:edge-runtime|lib\\.deno)\\.d\\.ts)$/;
    const excludeRaw = (rules: any[]) => {
      for (const rule of rules) {
        if (!rule || typeof rule !== "object") continue;
        if (Array.isArray(rule.oneOf)) excludeRaw(rule.oneOf);
        if (Array.isArray(rule.rules)) excludeRaw(rule.rules);
        if (!rule.oneOf && !rule.rules) rule.exclude = rule.exclude ? [rule.exclude, rawText] : rawText;
      }
    };
    excludeRaw(config.module.rules);
    config.module.rules.push({ test: rawText, type: "asset/source" });
    return config;
  },
'''
            experimental='  experimental: {\n'
            insertion='  onDemandEntries: {\n'
            patch_script=('const fs=require("fs");const p="./apps/studio/next.config.ts";'
                'let s=fs.readFileSync(p,"utf8");'
                'const a='+json.dumps(experimental)+';const b='+json.dumps(insertion)+';'
                'if(s.split(a).length!==2||s.split(b).length!==2||/\\bwebpack\\s*[:(]/.test(s))'
                'throw new Error("Studio Next config changed");'
                's=s.replace(a,a+"    cpus: 1,\\n    webpackBuildWorker: true,\\n    webpackMemoryOptimizations: true,\\n");'
                's=s.replace(b,'+json.dumps(webpack)+'+b);fs.writeFileSync(p,s);')
            dockerfile=dockerfile.replace(marker,"RUN node -e '"+patch_script+"'\n"+
                'RUN ulimit -c 0 && pnpm --filter studio exec next build --webpack\n')
        if component=='langfuse-web':
            marker="NODE_OPTIONS='--max-old-space-size-percentage=75'"
            if dockerfile.count(marker)!=1:raise RuntimeError('Langfuse 构建内存配置发生变化，需要适配')
            dockerfile=dockerfile.replace(marker,"NODE_OPTIONS='--max-old-space-size=5120'")
            # Webpack 在 8 GiB V8 堆仍耗尽；同源码本机的上游默认构建器已成功。
            # CI 增加交换空间后重验默认构建器，保留原 build 脚本、类型检查和 source map。
            marker='RUN NODE_OPTIONS='
            if dockerfile.count(marker)!=1:raise RuntimeError('Langfuse 构建入口发生变化，需要适配')
            patch_script=('const fs=require("fs");const q="./web/next.config.mjs";let t=fs.readFileSync(q,"utf8");'
                'const a="  experimental: {\\n";'
                'if(t.split(a).length!==2||/\\bcpus\\s*:/.test(t))'
                'throw new Error("Langfuse Next memory config changed");'
                't=t.replace(a,a+"    cpus: 1,\\n");'
                'fs.writeFileSync(q,t);')
            # 日志仍保留完整错误堆栈；CI 不生成数 GiB 的未采集 core 文件。
            dockerfile=dockerfile.replace(marker,"RUN node -e '"+patch_script+"'\nRUN ulimit -c 0 && NEXT_DISABLE_BUILD_CACHE=true NODE_OPTIONS=")
            marker='turbo run build --filter=web...\n'
            if dockerfile.count(marker)!=1:raise RuntimeError('Langfuse turbo 构建命令发生变化，需要适配')
            dockerfile=dockerfile.replace(marker,'turbo run build --filter=web... --concurrency=1 --output-logs=full\n')
    return dockerfile


def fingerprint(component):
    flags={'target':RECIPES[component][2],'base_path':'/langfuse' if component=='langfuse-web' else None}
    return hashlib.sha256((recipe(component)+json.dumps(flags,sort_keys=True)).encode()).hexdigest()


def monitor(stopping):
    while not stopping.is_set():
        memory={}
        source=Path('/proc/meminfo')
        if source.exists():
            memory={line.split(':')[0]:line.split(':')[1].strip() for line in source.read_text().splitlines()
                if line.startswith(('MemTotal:','MemAvailable:','SwapFree:'))}
        print('ANP_BUILD_RESOURCES',json.dumps({'memory':memory,'disk_free_gb':round(shutil.disk_usage(ROOT).free/2**30,2)}),flush=True)
        stopping.wait(30)


def build(component):
    repository,filename,target=RECIPES[component]
    source=ROOT/'third_party'/repository
    dockerfile=recipe(component)
    output=ROOT/'.local/build'/f'{component}.Dockerfile'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(dockerfile,encoding='utf8')
    # 不修改 submodule 的工作区；生成文件不是最终平台镜像。
    command=['docker','build','--progress','plain']
    if target:command+=['--target',target]
    if component=='langfuse-web':command+=['--build-arg','NEXT_PUBLIC_BASE_PATH=/langfuse']
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    command+=['--label','org.opencontainers.image.revision='+revision,
        '--label','dev.anp.build.recipe='+fingerprint(component),
        '--label','dev.anp.build.profile='+os.environ.get('ANP_BUILD_PROFILE','default'),
        '-f',str(output),'-t',f'anp-build/{component}:source',str(source)]
    stopping=threading.Event();thread=None
    if os.environ.get('GITHUB_ACTIONS')=='true':
        thread=threading.Thread(target=monitor,args=(stopping,),daemon=True);thread.start()
    try:subprocess.run(command,check=True)
    finally:
        stopping.set()
        if thread:thread.join(timeout=1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',choices=sorted(RECIPES),required=True)
    parser.add_argument('--locked',action='store_true',help='仅用于复现已锁定代码；默认先更新所有依赖')
    args=parser.parse_args()
    # 更新 Git 必须串行；多个组件构建可并行，锁定构建不能再改 Git 索引。
    if not args.locked:
        sync(True)
    build(args.component)
