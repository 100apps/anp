"""为开发者及agent提供不含凭据的工具检查和项目上下文。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
context={'project':'anp','root':str(root),'runtime':'Debian slim单镜像，宿主LangGraph Agent Server扩展',
         'commands':{'start':'make init && make up && make dev','test':'make test','acceptance':'make acceptance',
                     'sources':'make sources','build':'make build','debug':'VSCode F5：ANP: 一键前后端热重载'},
         'entrypoints':['business/langgraph.json','business/src/anp_business/server/http.py','docker/toolbox/Dockerfile'],
         'protected':'不得操作相邻项目；仅管理当前Compose实例和显式独立数据根',
         'test_boundary':'默认离线workflow和Deep Agents；不调用付费供应商'}
if '--context' in sys.argv:
    print(json.dumps(context,ensure_ascii=False,indent=2));raise SystemExit(0)
result={'tools':{name:bool(shutil.which(name)) for name in ('docker','uv','node','pnpm','git')},'docker':False}
if result['tools']['docker']:
    probe=subprocess.run(['docker','info','--format','{{.OSType}}'],capture_output=True,text=True,timeout=15)
    result['docker']=probe.returncode==0 and probe.stdout.strip()=='linux'
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if all(result['tools'].values()) and result['docker'] else 1)
