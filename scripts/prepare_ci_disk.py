"""仅清理一次性 GitHub 托管 Linux 运行器中本构建不用的预装 SDK。"""
import argparse
import os
from pathlib import Path
import shutil
import sys

TARGETS=('/usr/local/lib/android','/usr/share/dotnet','/opt/ghc','/usr/lib/jvm','/opt/hostedtoolcache/CodeQL')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minimum-gb',type=int,default=35)
    args=parser.parse_args()
    if sys.platform!='linux' or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
        raise SystemExit('拒绝清理：只允许一次性 GitHub 托管 Linux 运行器')
    before=shutil.disk_usage('/').free/2**30
    print(f'CI 根磁盘可用空间：{before:.2f} GiB',flush=True)
    if before>=args.minimum_gb:return
    for name in TARGETS:
        target=Path(name)
        if target.is_symlink():target.unlink()
        elif target.exists():
            if target.resolve()!=target:raise RuntimeError('预装 SDK 路径解析异常，拒绝清理')
            shutil.rmtree(target)
        print('已处理未使用的预装 SDK：'+name,flush=True)
    after=shutil.disk_usage('/').free/2**30
    print(f'CI 清理后可用空间：{after:.2f} GiB',flush=True)
    if after<args.minimum_gb:raise SystemExit('剩余空间不足以安全组装平台镜像；保留诊断，不启动必然超限的构建')


if __name__=='__main__':main()
