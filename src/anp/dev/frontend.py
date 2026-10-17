"""直接启动已安装的 Vite，避免 IDE 的 shell/profile 改写包管理命令。"""
import shutil
import subprocess
import base64
import json
import os
import re
import time
from pathlib import Path

import httpx
from anp import ROOT


def node_command():
    node=shutil.which('node')
    if not node: raise SystemExit('未找到 Node.js，请安装 Node.js 22 或更新版本后重开 IDE')
    vite=ROOT/'console/node_modules/vite/bin/vite.js'
    if not vite.exists(): raise SystemExit('缺少前端依赖，请先运行 pnpm install')
    return [node,str(vite)]


def ready():
    """确认端口上的 Vite 确实提供本工作区源码，避免复用其他项目。"""
    port=int(os.environ.get('ANP_UI_PORT','5173'))
    if not 1<=port<=65535:raise ValueError('前端端口无效')
    base=os.environ.get('VITE_BASE_PATH','/')
    if not base.startswith('/') or not base.endswith('/') or '..' in base:
        raise ValueError('前端基础路径无效')
    try:
        with httpx.Client(trust_env=False,timeout=4) as client:
            response=client.get(f'http://127.0.0.1:{port}{base}src/main.tsx')
    except httpx.ConnectError:return False
    match=re.search(r'sourceMappingURL=data:application/json;base64,([^\s]+)',response.text)
    if response.status_code!=200 or not match:
        raise RuntimeError('前端端口已被其他服务占用，拒绝复用或覆盖')
    document=json.loads(base64.b64decode(match.group(1)))
    source=Path(document.get('file','')).resolve()
    if source!=(ROOT/'console/src/main.tsx').resolve():
        raise RuntimeError('Vite 属于其他工作区，拒绝复用或覆盖')
    return True


def ensure():
    """F5 前置任务可重复执行，不依赖 IDE 对旧后台任务 ready 事件的复用。"""
    if ready():return
    log=ROOT/'.local/v3-vite-f5.log'
    log.parent.mkdir(parents=True,exist_ok=True)
    options={'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    with log.open('ab') as output:
        process=subprocess.Popen(node_command(),cwd=ROOT/'console',stdin=subprocess.DEVNULL,
            stdout=output,stderr=subprocess.STDOUT,**options)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('Vite 启动失败，请检查 .local/v3-vite-f5.log')
        if ready():return
        time.sleep(.25)
    raise RuntimeError('Vite 就绪超时；保留进程和日志供诊断，不重复启动')


def main():
    raise SystemExit(subprocess.call(node_command(),cwd=ROOT/'console'))

if __name__=='__main__': main()
