"""在两种开发模式间交接本地 Agent 状态，保留旧快照且拒绝越界归档。"""
import hashlib
import os
from pathlib import Path,PurePosixPath
import tarfile
from uuid import uuid4


def restore(directory,archive):
    root,source=Path(directory),Path(archive)
    if not source.is_file():return False
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    marker=root/'.snapshot-last'
    if marker.exists() and marker.read_text()==digest:return False
    root.mkdir(parents=True,exist_ok=True)
    candidate=root/('.incoming-'+uuid4().hex)
    candidate.mkdir()
    with tarfile.open(source,'r:*') as bundle:
        members=bundle.getmembers()
        for member in members:
            path=PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='.langgraph_api':
                raise ValueError('Agent 状态归档路径越界')
            if not (member.isfile() or member.isdir()):raise ValueError('Agent 状态归档包含不允许的链接或设备')
        bundle.extractall(candidate,members=members,filter='data')
    incoming=candidate/'.langgraph_api'
    if not incoming.is_dir():raise ValueError('Agent 状态归档缺少运行目录')
    current=root/'.langgraph_api'
    if current.exists():
        backups=root/'.backups';backups.mkdir(exist_ok=True)
        os.replace(current,backups/uuid4().hex)
    os.replace(incoming,current)
    candidate.rmdir()
    marker.write_text(digest)
    return True


def snapshot(directory,archive):
    root,target=Path(directory),Path(archive)
    current=root/'.langgraph_api'
    if not current.is_dir():return False
    if any(item.is_symlink() for item in current.rglob('*')):raise ValueError('Agent 状态目录不应包含符号链接')
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.tmp-'+uuid4().hex)
    with tarfile.open(temporary,'w:gz') as bundle:bundle.add(current,arcname='.langgraph_api')
    os.chmod(temporary,0o600)
    digest=hashlib.sha256(temporary.read_bytes()).hexdigest()
    os.replace(temporary,target)
    (root/'.snapshot-last').write_text(digest)
    return True
