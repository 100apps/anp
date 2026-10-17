"""仅清理一次性 GitHub 托管 Linux 运行器中本构建不用的预装 SDK。"""
import argparse
import os
from pathlib import Path
import shutil
import sys
import subprocess

TARGETS=('/usr/local/lib/android','/usr/share/dotnet','/opt/ghc','/usr/lib/jvm',
    '/opt/hostedtoolcache/CodeQL','/usr/share/swift','/usr/local/.ghcup',
    # 编译发生在 Docker 内；宿主机这些 SDK 不参与 Python 编排和 Actions Node 运行时。
    '/opt/hostedtoolcache/go','/opt/hostedtoolcache/Ruby',
    '/opt/hostedtoolcache/Java_Temurin-Hotspot_jdk','/usr/share/miniconda',
    '/usr/local/share/powershell','/usr/local/share/vcpkg',
    '/opt/az','/usr/local/aws-cli','/usr/local/aws-sam-cli',
    '/opt/microsoft/powershell','/usr/lib/google-cloud-sdk')


def add_swap(gib):
    """只在已验证的临时 CI 主机上新增专用交换文件，不替换现有交换空间。"""
    if not gib:return
    target=Path('/anp-ci-build.swap')
    size=gib*2**30
    active={line.split()[0] for line in Path('/proc/swaps').read_text().splitlines()[1:]}
    if target.is_symlink():raise RuntimeError('CI 交换文件不能是符号链接')
    if target.exists():
        if str(target) in active and target.is_file() and target.stat().st_size==size:
            print('复用本作业已启用的 CI 交换空间',flush=True)
            return
        raise RuntimeError('CI 交换文件已存在但身份或状态不匹配，拒绝覆盖')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:os.posix_fallocate(fd,0,size)
    finally:os.close(fd)
    subprocess.run(['mkswap',str(target)],check=True)
    subprocess.run(['swapon',str(target)],check=True)
    print(f'已新增 {gib} GiB CI 交换空间；不修改宿主机永久配置',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minimum-gb',type=int,default=35)
    parser.add_argument('--extra-swap-gb',type=int,choices=(0,8),default=0)
    args=parser.parse_args()
    if sys.platform!='linux' or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted':
        raise SystemExit('拒绝清理：只允许一次性 GitHub 托管 Linux 运行器')
    before=shutil.disk_usage('/').free/2**30
    print(f'CI 根磁盘可用空间：{before:.2f} GiB',flush=True)
    required=args.minimum_gb+args.extra_swap_gb
    for name in TARGETS:
        if shutil.disk_usage('/').free/2**30>=required:break
        target=Path(name)
        if target.is_symlink():target.unlink()
        elif target.exists():
            if target.resolve()!=target:raise RuntimeError('预装 SDK 路径解析异常，拒绝清理')
            shutil.rmtree(target)
        else:
            print('预装 SDK 不存在，跳过：'+name,flush=True)
            continue
        remaining=shutil.disk_usage('/').free/2**30
        print(f'已处理未使用的预装 SDK：{name}；剩余 {remaining:.2f} GiB',flush=True)
        if remaining>=required:break
    after=shutil.disk_usage('/').free/2**30
    print(f'CI 清理后可用空间：{after:.2f} GiB',flush=True)
    if after<required:raise SystemExit('剩余空间不足以安全组装平台镜像；保留诊断，不启动必然超限的构建')
    add_swap(args.extra_swap_gb)


if __name__=='__main__':main()
