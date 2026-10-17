"""为 WSL 原生平台准备已编译运行时；临时容器只用于读取镜像文件。"""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4
from native_artifacts import LAYOUT

ROOT=Path(__file__).resolve().parents[1]


def image_identity(image):
    command=['docker','image','inspect',image]
    result=subprocess.run(command,capture_output=True,text=True)
    if result.returncode:
        subprocess.run(['docker','pull',image],check=True)
        result=subprocess.run(command,capture_output=True,text=True,check=True)
    return json.loads(result.stdout)[0]['Id']


def prepare(image='ghcr.io/100apps/anp-platform:latest',distro='Ubuntu'):
    wsl=['wsl','-d',distro,'-u','root','--exec']
    identity=image_identity(image)
    marker={'workspace':str(ROOT),'image':identity,'layout':LAYOUT}
    helper=subprocess.check_output([*wsl,'wslpath','-u',str(ROOT/'scripts/native_artifacts.py')],text=True).strip()
    result=json.loads(subprocess.check_output([*wsl,'python3',helper,'begin','--expected',json.dumps(marker)],text=True))
    if result['cached']:return {'native_runtime':'/opt/anp','image':identity,'cached':True}
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
        subprocess.run([*wsl,'python3',helper,'finish','--expected',json.dumps(marker)],check=True)
    finally:subprocess.run(['docker','rm',container],check=True,stdout=subprocess.DEVNULL)
    return {'native_runtime':'/opt/anp','image':identity,'cached':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='ghcr.io/100apps/anp-platform:latest')
    parser.add_argument('--distro',default='Ubuntu')
    arguments=parser.parse_args();print(json.dumps(prepare(arguments.image,arguments.distro),ensure_ascii=False))
