"""为 WSL 原生平台准备已编译运行时；临时容器只用于读取镜像文件。"""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def prepare(image='anp/platform:agent-integration',distro='Ubuntu'):
    wsl=['wsl','-d',distro,'-u','root','--exec']
    identity=json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]['Id']
    marker={'workspace':str(ROOT),'image':identity}
    check='''import json,sys
from pathlib import Path
root=Path('/opt/anp');marker=root/'.anp-native-base.json'
expected=json.loads(sys.argv[1])
if root.exists() and not marker.exists():raise SystemExit('拒绝覆盖未受本项目管理的 /opt/anp')
if marker.exists():
 previous=json.loads(marker.read_text())
 if previous['workspace']!=expected['workspace']:raise SystemExit('原生运行目录属于其他工作区')
 if previous==expected:print('cached');raise SystemExit(0)
pidfile=Path('/run/anp/native.pid.json')
if pidfile.exists():
 pid=json.loads(pidfile.read_text())['pid'];command=Path(f'/proc/{pid}/cmdline')
 if command.exists() and b'anp_platform.native' in command.read_bytes():raise SystemExit('请先停止原生平台，再更新运行制品')
root.mkdir(parents=True,exist_ok=True)
marker.write_text(json.dumps({'workspace':expected['workspace'],'image':'preparing'}))
print('prepare')
'''
    result=subprocess.check_output([*wsl,'python3','-c',check,json.dumps(marker)],text=True).strip()
    if result=='cached':return {'native_runtime':'/opt/anp','image':identity,'cached':True}
    container=subprocess.check_output(['docker','create','--name','anp-native-export-'+uuid4().hex[:10],image],text=True).strip()
    def copy(source,destination):
        subprocess.run([*wsl,'mkdir','-p',destination],check=True)
        sender=subprocess.Popen(['docker','cp',container+':'+source,'-'],stdout=subprocess.PIPE)
        try:
            receiver=subprocess.Popen([*wsl,'tar','-xf','-','-C',destination],stdin=sender.stdout)
            sender.stdout.close()
            code=receiver.wait();sent=sender.wait()
            if code or sent:raise RuntimeError('提取原生平台制品失败')
        finally:
            if sender.poll() is None:sender.terminate();sender.wait()
    try:
        print('提取平台运行目录……',flush=True)
        copy('/opt/anp/.','/opt/anp')
        copy('/usr/local/.','/opt/anp/python')
        copy('/usr/sbin/nginx','/opt/anp/bin')
        copy('/etc/nginx/mime.types','/opt/anp/etc')
        subprocess.run([*wsl,'python3','-c',
            'from pathlib import Path;import sys;Path("/opt/anp/.anp-native-base.json").write_text(sys.argv[1])',json.dumps(marker)],check=True)
    finally:subprocess.run(['docker','rm',container],check=True,stdout=subprocess.DEVNULL)
    return {'native_runtime':'/opt/anp','image':identity,'cached':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='anp/platform:agent-integration')
    parser.add_argument('--distro',default='Ubuntu')
    arguments=parser.parse_args();print(json.dumps(prepare(arguments.image,arguments.distro),ensure_ascii=False))
