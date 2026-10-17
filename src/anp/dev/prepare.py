"""F5 只准备平台与业务注册，不占用 API/Worker 的调试进程。"""
import asyncio
import time
from anp import ROOT
from anp.applications.generator import sync
from anp.management.platform import command,migrate
from anp.management.bootstrap import seed,wait_url
from anp.config import value,secret


def prepare():
    sync()
    base=ROOT/'platform/compose.generated.yaml'
    debug=ROOT/'platform/compose.debug.yaml'
    command(['docker','compose','-f',str(base),'-f',str(debug),'up','-d','--build'],capture=False,cwd=ROOT/'platform')
    async def ready():
        await wait_url(value('supabase','url')+'/auth/v1/health',headers={'apikey':secret('ANON_KEY')})
        await wait_url(value('supabase','url')+'/storage/v1/status')
        migrate()
        await seed()
    asyncio.run(ready())
    # 不等待引擎图的 HTTP 健康检查，避免等待调试器时相互阻塞。
    deadline=time.monotonic()+90
    probe="import pathlib,sys; rows=pathlib.Path('/proc/net/tcp').read_text().splitlines()[1:]; sys.exit(0 if any(r.split()[1].endswith(':162E') and r.split()[3]=='0A' for r in rows) else 1)"
    while time.monotonic()<deadline:
        try:
            command(['docker','compose','-f',str(base),'-f',str(debug),'exec','-T','agent-demo','python','-c',probe])
            break
        except RuntimeError: time.sleep(.5)
    else: raise RuntimeError('Agent 调试端口没有就绪')
    print('ANP_PREPARE_READY',flush=True)


if __name__=='__main__': prepare()
