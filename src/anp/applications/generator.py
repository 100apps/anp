import json
import re
import shutil
from anp import ROOT
from anp.applications.registry import applications,application


def sync():
    applications.cache_clear()
    lines=['// 此文件由 anp app sync 生成。','import type { ComponentType } from "react";']
    entries=[]
    for app_id,manifest in applications().items():
        lines.append(f'import App_{app_id} from "../../../apps/{app_id}/ui/index";')
        entries.append(f'{{id:{json.dumps(app_id)},name:{json.dumps(manifest["name"],ensure_ascii=False)},component:App_{app_id}}}')
    lines.append('export const applications: {id:string;name:string;component:ComponentType}[] = ['+','.join(entries)+'];')
    file=ROOT/'console/src/generated/applications.ts'
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    from anp.management.platform import generate
    generate()
    return {'applications':list(applications()),'generated':['console/src/generated/applications.ts','platform/compose.generated.yaml']}


def create(name,source='demo'):
    if not re.fullmatch(r'[a-z][a-z0-9_]*',name): raise ValueError('应用 ID 必须为小写字母、数字或下划线')
    application(source)
    dest=ROOT/'apps'/name
    if dest.exists(): raise ValueError('目标应用已存在')
    shutil.copytree(ROOT/'apps'/source,dest,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    for file in dest.rglob('*'):
        if file.is_file() and file.suffix in ('.py','.toml','.sql','.tsx','.ts','.md'):
            file.write_text(file.read_text(encoding='utf-8').replace(source,name),encoding='utf-8')
    return sync()
