"""记录已核验的官方稳定版本、源码身份和实际安装输入镜像。"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SOURCES={'dbx':('dbx','0.6.49','v0.6.49'),'langfuse-web':('langfuse','4.64.0','v4.64.0'),
         'langfuse-worker':('langfuse','4.64.0','v4.64.0'),'litellm':('litellm','1.106.0','v1.106.0'),
         'supabase-studio':('supabase','1.26.08','v1.26.08'),
         'supabase-auth':('components/auth','2.197.0','v2.197.0'),
         'supabase-rest':('components/postgrest','14.18','v14.18'),
         'supabase-meta':('components/postgres-meta','0.100.0','v0.100.0'),
         'supabase-storage':('components/storage','1.81.0','v1.81.0')}
OFFICIAL={'postgres':('supabase/postgres:17.6.1.136','17.6.1.136'),
          'redis':('redis:8.10.2-trixie','8.10.2'),
          'clickhouse':('clickhouse/clickhouse-server:26.9.22.3','26.9.22.3-stable'),
          'rustfs':('rustfs/rustfs:1.0.3','1.0.3'),
          'node':('node:24-bookworm-slim','24 LTS'),
          'supabase-gateway':('envoyproxy/envoy:v1.39.1','1.39.1')}


def image_id(image,required):
    value=subprocess.run(['docker','image','inspect','--format','{{.Id}}',image],capture_output=True,text=True)
    if value.returncode and required: raise SystemExit('尚未构建或安装：'+image)
    return value.stdout.strip() or None


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--require-images',action='store_true'); args=parser.parse_args()
    data={'verified_at':datetime.now(timezone.utc).isoformat(),'architecture':'linux/amd64','components':{},
          'compatibility_notes':['PostgreSQL使用Supabase当前支持的17.6扩展组合，未跳到PG18',
                                 'ClickHouse26.9稳定分支满足Langfuse4大于等于25.12的约束',
                                 'LiteLLM采用官方正式release1.106.0，不采用main的1.107开发版本',
                                 'Studio从官方最新公开release v1.26.08生产重建路径前缀；不冒充2026.10.12预构建镜像']}
    for name,(folder,version,tag) in SOURCES.items():
        image='anp3/'+name+':source'
        sha=subprocess.check_output(['git','-C',str(ROOT/'platform/sources'/folder),'rev-parse','HEAD'],text=True).strip()
        data['components'][name]={'version':version,'upstream_ref':tag,'source_sha':sha,'image':image,'image_id':image_id(image,args.require_images)}
    for name,(image,version) in OFFICIAL.items():
        data['components'][name]={'version':version,'image':image,'image_id':image_id(image,args.require_images)}
    (ROOT/'docker/toolbox/versions.lock.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('已锁定官方版本、实际源码SHA及安装镜像身份')


if __name__=='__main__': main()
