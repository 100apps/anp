"""真实 Docker 对比分阶段导出与一次导出的文件、权限和链接语义。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from bundle_platform import ROOT, build_image


def verify():
    identity = uuid4().hex
    directory = ROOT / '.local/build' / ('checkpoint-probe-' + identity)
    directory.mkdir(parents=True)
    fixture = """import os
from pathlib import Path
p=Path('/fixture');p.mkdir()
(p/'中文.bin').write_bytes(bytes(range(256))*8192)
(p/'run.sh').write_text('#!/bin/sh\\necho probe\\n')
os.chmod(p/'run.sh',0o751)
os.chown(p/'中文.bin',1001,1002)
os.link(p/'中文.bin',p/'hard.bin')
os.symlink('中文.bin',p/'relative-link')
os.symlink('/opt/anp/roots/a/中文.bin',p/'absolute-link')
"""
    fixture = '# 本次独立构建 '+identity+'\n'+fixture
    dockerfile = directory / 'Dockerfile'
    dockerfile.write_text('FROM python:3.12-slim AS fixture\nRUN python -c ' +
                          json.dumps('exec('+repr(fixture)+')', ensure_ascii=True) + """
FROM python:3.12-slim AS platform_base
FROM platform_base AS platform_root_0
COPY --from=fixture /fixture/ /opt/anp/roots/a/
FROM platform_root_0 AS platform_root_1
COPY --from=fixture /fixture/ /opt/anp/roots/b/
FROM platform_root_1 AS platform_runtime
ENV ANP_CHECKPOINT_PROBE=enabled
RUN mkdir -p /state/agent
LABEL dev.anp.checkpoint_probe=""" + json.dumps(identity) + '\n', encoding='utf8')
    snapshot = """import hashlib,json,os,stat
from pathlib import Path
root=Path('/opt/anp/roots');rows=[]
for p in sorted(root.rglob('*')):
 s=p.lstat();r={'path':str(p.relative_to(root)),'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid}
 if p.is_symlink():r['link']=os.readlink(p)
 elif p.is_file():r['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
 rows.append(r)
hardlinks={name:os.stat(root/name/'中文.bin').st_ino==os.stat(root/name/'hard.bin').st_ino for name in ['a','b']}
print(json.dumps({'files':rows,'hardlinks':hardlinks,'environment':os.environ['ANP_CHECKPOINT_PROBE']}))
"""
    tags = ['anp-build/checkpoint-probe:' + identity + suffix for suffix in ['-staged', '-direct']]
    results = []
    owned = []
    try:
        for tag, targets in zip(tags, [('platform_root_0', 'platform_root_1'), ()]):
            stages = build_image(dockerfile, tag, targets, context=directory)
            owned.append(tag)
            result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                                     'python', tag, '-c', snapshot], capture_output=True, text=True,
                                    encoding='utf8', check=True, timeout=60)
            results.append({'stages': stages, 'snapshot': json.loads(result.stdout)})
        assert results[0]['snapshot'] == results[1]['snapshot'], '两种导出结果不一致'
        assert all(results[1]['snapshot']['hardlinks'].values()), '硬链接关系未保留'
        files = {row['path']: row for row in results[1]['snapshot']['files']}
        assert files['a/中文.bin']['uid'] == 1001 and files['a/中文.bin']['gid'] == 1002
        assert files['a/run.sh']['mode'] == 0o751
        assert files['a/relative-link']['link'] == '中文.bin'
        assert files['b/absolute-link']['link'] == '/opt/anp/roots/a/中文.bin'
        return {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
                'file_metadata_and_contents_equal': True, 'hardlinks_preserved': True,
                'entries': len(files), 'execution_order': ['staged', 'direct'],
                'fixture_command_unique_per_run': True, 'stages': [r['stages'] for r in results],
                'snapshot_sha256': hashlib.sha256(json.dumps(results[1]['snapshot'], sort_keys=True).encode()).hexdigest(),
                'scope': '真实小型 Docker 镜像的导出等价检查；不等于完整平台低内存导出或运行验收通过'}
    finally:
        for tag in owned:
            subprocess.run(['docker', 'image', 'rm', tag], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = verify()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    print(json.dumps(report, ensure_ascii=False))
