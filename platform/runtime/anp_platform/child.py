"""保留上游运行环境的进程启动器，不创建嵌套容器或 Docker daemon。"""
import json
import os
from pathlib import Path
import shutil
import sys


def identity(root,spec):
    name,_,group=spec.partition(':')
    if not name:return 0,0
    entries=[line.split(':') for line in (root/'etc/passwd').read_text().splitlines() if ':' in line]
    entry=next((p for p in entries if p[0]==name or p[2]==name),None)
    uid=int(name) if name.isdigit() else int(entry[2])
    gid=int(entry[3]) if entry else uid
    if group:
        groups=[line.split(':') for line in (root/'etc/group').read_text().splitlines() if ':' in line]
        gid=int(group) if group.isdigit() else int(next(g for g in groups if g[0]==group)[2])
    return uid,gid


def main():
    name=sys.argv[1]
    metadata=json.loads(Path(os.environ.get('ANP_COMPONENTS','/opt/anp/components.json')).read_text())
    settings=json.loads(Path(os.environ.get('ANP_PLATFORM_CONFIG','/run/anp/config.json')).read_text())
    image=metadata['components'][name]
    component=settings['services'][name]
    initializing=len(sys.argv)>2 and sys.argv[2]=='initialize'
    root=Path('/opt/anp/roots')/name
    uid,gid=identity(root,image.get('user',''))
    environment=dict(item.split('=',1) for item in image['environment'] if '=' in item)
    environment.update({k:str(v) for k,v in component.get('environment',{}).items()})
    command=component['initialization_command'] if initializing else component.get('command') or image['command']
    if component.get('execution')=='native' and not initializing:
        # Node 等可重定位运行时直接读取当前容器的 /proc；不添加 SYS_ADMIN。
        home=root/'tmp/anp-home'
        home.mkdir(parents=True,exist_ok=True)
        os.chown(home,uid,gid)
        environment['HOME']=str(home)
        environment['PATH']=':'.join(str(root/item.lstrip('/')) for item in environment.get('PATH','').split(':') if item.startswith('/'))+':/usr/bin:/bin'
        executable=shutil.which(command[0],path=environment['PATH'])
        if not executable:raise RuntimeError('组件原生运行时不存在：'+name)
        command=[executable,*command[1:]]
        loader=component.get('native_loader')
        if loader:
            command=[str(root/loader.lstrip('/')),'--library-path',str(root/'usr/lib64'),*command]
        engine=component.get('native_prisma_engine')
        if engine:
            modules=root/component.get('native_prisma_directory','app/node_modules/.pnpm')
            matches=list(modules.glob('@prisma+client*/node_modules/.prisma/client/libquery_engine-'+engine+'.so.node'))
            if not matches:raise RuntimeError('组件源码制品缺少 Prisma 引擎：'+name)
            environment['PRISMA_QUERY_ENGINE_LIBRARY']=str(matches[0])
        os.chdir(root/(image.get('working_dir') or '/').lstrip('/'))
    else:
        os.chroot(root)
        os.chdir(image.get('working_dir') or '/')
    if uid:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    os.execvpe(command[0],command,environment)


if __name__=='__main__':main()
