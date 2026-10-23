"""只在组装阶段精简不可变安装文件；不访问/data，不删除组件或PostgreSQL扩展。"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

ROOTS=[Path(p) for p in ('/opt/anp/services','/app/.venv','/opt/prisma','/opt/langfuse-tools')]


def files(root):
    for parent,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if not Path(parent,name).is_symlink()]
        for name in names:
            path=Path(parent,name)
            if not path.is_symlink() and path.is_file(): yield path


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--apply',action='store_true'); args=parser.parse_args()
    report={'apply':args.apply,'removed_bytes':0,'removed_files':0,'deduplicated_bytes':0,'deduplicated_files':0,
            'preserved':'所有15个服务、PostgreSQL完整扩展、Java、Node22/24及原生运行库；保留LICENSE/NOTICE'}
    for root in ROOTS:
        for path in files(root):
            # sourcemap与Python字节码均可由原始执行文件替代，不删除业务源文件。
            if path.suffix in ('.map','.pyc','.pyo'):
                report['removed_bytes']+=path.stat().st_size; report['removed_files']+=1
                if args.apply: path.unlink()
    runtime=Path('/opt/runtime/storage')
    for root in (runtime/'usr/lib/python3.12',runtime/'usr/lib/gcc',runtime/'usr/lib/pkgconfig',runtime/'usr/local/lib/node_modules'):
        if root.exists():
            report['removed_bytes']+=sum(p.stat().st_size for p in files(root))
            if args.apply: shutil.rmtree(root)
    for path in files(runtime):
        if path.suffix in ('.a','.la','.pyc'):
            report['removed_bytes']+=path.stat().st_size; report['removed_files']+=1
            if args.apply: path.unlink()
    # Redis的四个入口使用相同官方二进制，由argv[0]选择模式；保留全部命令。
    redis=Path('/opt/anp/bin/redis-server')
    digest=hashlib.sha256(redis.read_bytes()).digest()
    for name in ('redis-sentinel','redis-check-rdb','redis-check-aof'):
        path=redis.with_name(name)
        if path.is_file() and not path.is_symlink():
            if hashlib.sha256(path.read_bytes()).digest()!=digest: continue
            report['removed_bytes']+=path.stat().st_size
            if args.apply: path.unlink(); path.symlink_to('redis-server')
    node=Path('/usr/bin/node')
    if node.is_file() and not node.is_symlink():
        report['removed_bytes']+=node.stat().st_size
        if args.apply: node.unlink(); node.symlink_to('/opt/anp/runtimes/node24/bin/node')
    # 硬链接保持原始路径与Node相对解析；仅对node_modules代码进行内容去重。
    groups=defaultdict(list)
    for path in files(Path('/opt/anp/services')):
        if 'node_modules' not in path.parts or any(p in ('.cache','cache','tmp') for p in path.parts): continue
        info=path.stat()
        if info.st_size>=4096: groups[(info.st_size,bool(info.st_mode&0o111))].append(path)
    for (size,executable),paths in groups.items():
        if len(paths)<2: continue
        hashes={}
        for path in paths:
            digest=hashlib.sha256(path.read_bytes()).digest()
            if digest not in hashes: hashes[digest]=path; continue
            first=hashes[digest]
            if path.stat().st_ino==first.stat().st_ino: continue
            report['deduplicated_bytes']+=size; report['deduplicated_files']+=1
            if args.apply:
                # 跨服务共享的依赖只读，运行缓存仍写各自可写目录。
                os.chown(first,0,0); first.chmod(0o755 if executable else 0o644)
                path.unlink(); os.link(first,path)
    if args.apply:
        Path('/opt/anp/image-optimization.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
