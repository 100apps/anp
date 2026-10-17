"""直接启动已安装的 Vite，避免 IDE 的 shell/profile 改写包管理命令。"""
import shutil
import subprocess
from anp import ROOT

def main():
    node=shutil.which('node')
    if not node: raise SystemExit('未找到 Node.js，请安装 Node.js 22 或更新版本后重开 IDE')
    vite=ROOT/'console/node_modules/vite/bin/vite.js'
    if not vite.exists(): raise SystemExit('缺少前端依赖，请先运行 pnpm install')
    raise SystemExit(subprocess.call([node,str(vite)],cwd=ROOT/'console'))

if __name__=='__main__': main()
