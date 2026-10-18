"""在临时 Linux CI 实测后台 uv 持锁及虚拟环境直接启动的缓存收尾。"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def verify():
    if sys.platform!='linux' or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
        raise RuntimeError('只允许在一次性 GitHub 托管 Linux 运行器验证缓存清理')
    temporary=Path(os.environ['RUNNER_TEMP']).resolve()
    cache=Path(os.environ['UV_CACHE_DIR']).resolve()
    if cache==temporary or not cache.is_relative_to(temporary):
        raise RuntimeError('uv 缓存不在本作业临时目录，拒绝清理')
    if not Path(sys.executable).parent.samefile(ROOT/'.venv/bin'):
        raise RuntimeError('请直接使用已锁定安装的 .venv/bin/python，不能外套 uv run')
    directory=temporary/('anp-uv-cache-probe-'+uuid4().hex)
    directory.mkdir()
    result={'scope':'真实子进程与 uv cache prune 对照；不是完整平台或模型调用验收',
        'uv_version':subprocess.check_output(['uv','--version'],text=True).strip(),
        'background_uv_holds_cache_lock':False,'direct_python_allows_prune':False,
        'owned_processes_stopped':False}
    for wrapped in (True,False):
        name='uv-run' if wrapped else 'direct-python'
        ready=directory/(name+'.ready')
        code='from pathlib import Path; import time; Path('+repr(str(ready))+').write_text("ready"); time.sleep(120)'
        arguments=(['uv','run','--frozen'] if wrapped else [])+[sys.executable,'-c',code]
        with (directory/(name+'.log')).open('wb') as output:
            process=subprocess.Popen(arguments,cwd=ROOT,stdin=subprocess.DEVNULL,
                stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+60
            while not ready.exists():
                if process.poll() is not None:raise RuntimeError(name+' 探针提前退出')
                if time.monotonic()>deadline:raise RuntimeError(name+' 探针未就绪，保留日志')
                time.sleep(.05)
            started=time.monotonic()
            prune=subprocess.run(['uv','cache','prune','--ci'],cwd=ROOT,
                env={**os.environ,'UV_LOCK_TIMEOUT':'1'},capture_output=True,text=True,timeout=15)
            seconds=round(time.monotonic()-started,3)
            (directory/(name+'-prune.log')).write_text(prune.stdout+prune.stderr,encoding='utf8')
            if process.poll() is not None:raise RuntimeError('缓存清理期间后台探针退出，对照无效')
            if wrapped:
                if prune.returncode!=2 or 'Timeout' not in prune.stderr or 'lock' not in prune.stderr:
                    raise RuntimeError('未复现后台 uv 缓存锁超时，不能宣称验证原故障')
                result.update(background_uv_holds_cache_lock=True,wrapped_prune_seconds=seconds)
            else:
                if prune.returncode:raise RuntimeError('后台 Python 运行时缓存清理仍失败')
                result.update(direct_python_allows_prune=True,direct_prune_seconds=seconds)
        finally:
            if process.poll() is None:
                if os.getpgid(process.pid)!=process.pid:raise RuntimeError('探针进程组身份不匹配，拒绝停止')
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            else:process.wait()
    result['owned_processes_stopped']=True
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=verify()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(result,ensure_ascii=False))
