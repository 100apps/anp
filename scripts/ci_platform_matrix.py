"""将显式组件选择映射到新版生产配方；不追踪上游最新分支。"""
import json
import os

RECIPES = {
    'dbx': ('dbx', 'dbx/Dockerfile', ''),
    'langfuse-web': ('langfuse', 'langfuse/web.Dockerfile', ''),
    'langfuse-worker': ('langfuse', 'langfuse/worker.Dockerfile', ''),
    'litellm': ('litellm', 'litellm/Dockerfile', ''),
    'supabase-studio': ('supabase', 'supabase/studio.Dockerfile', 'production'),
    'supabase-auth': ('components/auth', 'supabase/auth.Dockerfile', ''),
    'supabase-rest': ('components/postgrest', 'supabase/rest.Dockerfile', ''),
    'supabase-meta': ('components/postgres-meta', 'supabase/meta.Dockerfile', ''),
    'supabase-storage': ('components/storage', 'supabase/storage.Dockerfile', ''),
}
selected = os.environ['ANP_CI_COMPONENT']
if selected != 'all' and selected not in RECIPES:
    raise SystemExit('不支持的组件')
matrix = [{'name': name, 'context': 'platform/sources/' + source, 'file': 'docker/platform/' + recipe,
           'target': target, 'build_args': ('NEXT_PUBLIC_BASE_PATH=/langfuse' if name == 'langfuse-web'
                                          else 'NEXT_PUBLIC_BASE_PATH=/supabase' if name == 'supabase-studio'
                                          else '')} for name, (source, recipe, target) in RECIPES.items()
          if selected in ('all', name)]
print('matrix=' + json.dumps({'include': matrix}, separators=(',', ':')))
