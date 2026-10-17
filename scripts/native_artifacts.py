"""在已归属的原生运行目录内干净更新制品，保留 Agent 状态与 DBX 备份。"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

LAYOUT=2
MANAGED=('.venv','src','anp_platform','www','config','migrations','components.json',
    'components.native.json','dependencies.json','pyproject.toml','uv.lock','roots','python','bin','etc','extensions')
ANCHORS=('components.json','anp_platform/supervisor.py','python/bin/python3.12',
    '.venv/lib/python3.12/site-packages','roots/dbx')


def write_json(path,data):
    temporary=path.with_name(path.name+'.tmp-'+uuid4().hex)
    temporary.write_text(json.dumps(data),encoding='utf8')
    os.replace(temporary,path)


def owned_root(root,expected):
    if expected.get('layout')!=LAYOUT or not expected.get('workspace') or not re.fullmatch(r'sha256:[0-9a-f]{64}',expected.get('image','')):
        raise RuntimeError('原生制品身份无效')
    root=Path(root).absolute()
    if root.is_symlink() or root.resolve()!=root:raise RuntimeError('原生运行目录不能是符号链接或重定向路径')
    marker=root/'.anp-native-base.json'
    if root.exists() and not marker.is_file():raise RuntimeError('拒绝覆盖未归属的原生运行目录')
    for name in ('.anp-native-base.json','.anp-native-pending.json','state'):
        if (root/name).is_symlink():raise RuntimeError('原生运行目录的控制路径不能是符号链接')
    if marker.exists() and json.loads(marker.read_text())['workspace']!=expected['workspace']:
        raise RuntimeError('原生运行目录属于其他工作区')
    return root


def active_native():
    marker=Path('/run/anp/native.pid.json')
    if marker.exists():
        pid=json.loads(marker.read_text())['pid']
        command=Path('/proc')/str(pid)/'cmdline'
        if command.exists() and b'anp_platform.native' in command.read_bytes():return True
    # 控制器异常退出后，仍持有运行目录的组件也应阻止替换。
    for process in Path('/proc').glob('[0-9]*'):
        if process.name==str(os.getpid()):continue
        for name in ('exe','cwd','root'):
            try:path=(process/name).readlink()
            except OSError:continue
            if path.is_relative_to('/opt/anp'):return True
    return False


def remove_artifact(root,name):
    if name not in MANAGED:raise RuntimeError('不是允许更新的原生制品路径')
    path=root/name
    # 所有递归清理都只针对已核对的直属制品目录；不沿符号链接删除目标。
    if path.is_symlink():path.unlink();return
    if path.resolve().parent!=root:raise RuntimeError('原生制品路径越界')
    if path.is_dir():shutil.rmtree(path)
    elif path.exists():path.unlink()


def copy_state(source,target):
    shutil.copytree(source,target,symlinks=True)
    if hasattr(os,'chown'):
        for original in [source,*source.rglob('*')]:
            destination=target/original.relative_to(source)
            stat=original.lstat()
            os.chown(destination,stat.st_uid,stat.st_gid,follow_symlinks=False)


def validate(root,expected):
    root=owned_root(root,expected)
    marker=root/'.anp-native-base.json'
    if not marker.exists() or json.loads(marker.read_text())!=expected:
        raise RuntimeError('原生制品尚未完成或镜像身份不符，请重新准备原生模式')
    if any(not (root/path).exists() for path in ANCHORS):
        raise RuntimeError('原生制品不完整，请重新准备原生模式')
    return root


def begin(root,expected):
    root=owned_root(root,expected)
    pending=root/'.anp-native-pending.json'
    try:validate(root,expected)
    except RuntimeError:pass
    else:
        # 成功标记写入后若进程恰好退出，旧日志不应覆盖后来产生的新 DBX 数据。
        if pending.exists():
            if json.loads(pending.read_text())['expected']['workspace']!=expected['workspace']:
                raise RuntimeError('更新日志属于其他工作区')
            pending.unlink()
        return {'cached':True}
    if active_native():raise RuntimeError('请先停止原生平台，再更新制品')
    root.mkdir(parents=True,exist_ok=True)
    write_json(root/'.anp-native-base.json',{**expected,'image':'preparing'})
    if pending.exists():
        journal=json.loads(pending.read_text())
        if journal['expected']['workspace']!=expected['workspace']:raise RuntimeError('更新日志属于其他工作区')
        if journal.get('dbx_backup'):
            source=root/journal['dbx_backup']
            if not source.is_dir() or source.is_symlink() or source.resolve().parent!=root/'state/dbx-backups':
                raise RuntimeError('DBX 更新备份无效，保留现有文件')
        journal['expected']=expected
    else:
        journal={'expected':expected,'dbx_backup':None}
        source=root/'roots/dbx/state'
        if source.exists():
            if source.is_symlink() or not source.resolve().is_relative_to(root):
                raise RuntimeError('DBX 状态路径越界')
            backups=root/'state/dbx-backups'
            if backups.is_symlink():raise RuntimeError('DBX 备份目录不能是符号链接')
            backups.mkdir(parents=True,exist_ok=True)
            target=backups/uuid4().hex
            copy_state(source,target)
            journal['dbx_backup']=str(target.relative_to(root))
    write_json(pending,journal)
    for name in MANAGED:remove_artifact(root,name)
    return {'cached':False,'dbx_backup':journal['dbx_backup']}


def finish(root,expected):
    root=owned_root(root,expected)
    if active_native():raise RuntimeError('更新期间原生平台被启动，拒绝继续')
    pending=root/'.anp-native-pending.json'
    journal=json.loads(pending.read_text())
    if journal['expected']!=expected:raise RuntimeError('更新日志与目标镜像不符')
    if any(not (root/path).exists() for path in ANCHORS):raise RuntimeError('提取的原生制品不完整')
    if journal['dbx_backup']:
        source=root/journal['dbx_backup']
        if not source.is_dir() or source.is_symlink() or source.resolve().parent!=root/'state/dbx-backups':
            raise RuntimeError('DBX 备份路径越界')
        target=root/'roots/dbx/state'
        if not target.resolve().is_relative_to(root) or target.is_symlink():raise RuntimeError('DBX 恢复路径越界')
        if target.exists():shutil.rmtree(target)
        copy_state(source,target)
    write_json(root/'.anp-native-base.json',expected)
    pending.unlink()
    return {'cached':False,'dbx_backup':journal['dbx_backup']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['begin','finish','check'])
    parser.add_argument('--expected',required=True)
    args=parser.parse_args()
    expected=json.loads(args.expected)
    if expected.get('layout')!=LAYOUT:raise SystemExit('原生制品布局版本不符')
    root=Path('/opt/anp')
    if args.action=='check':validate(root,expected);print('ANP_NATIVE_ARTIFACTS_READY')
    else:print(json.dumps((begin if args.action=='begin' else finish)(root,expected)))
