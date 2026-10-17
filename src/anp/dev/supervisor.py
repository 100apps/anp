"""Uvicorn 热重载父进程；debugpy 的 subProcess 机制跟踪每次重启的子进程。"""
import sys
import uvicorn
from anp import ROOT
from anp.config import value


def main():
    role=sys.argv[1] if len(sys.argv)>1 else 'api'
    if role not in ('api','worker'): raise SystemExit('服务类型必须为 api 或 worker')
    port=int(value('development',role+'_port'))
    host='127.0.0.1' if role=='api' else '0.0.0.0'
    uvicorn.run('anp.server.'+role+':app',host=host,port=port,reload=True,
        reload_dirs=[str(ROOT/'src/anp'),str(ROOT/'apps')],
        reload_excludes=['*.pyc','__pycache__','*.sql','*.tsx','*.ts','*.json','.local/*'])


if __name__=='__main__': main()
