"""仅清理 GitHub 官方临时托管运行器的预装 SDK，绝不在本机运行。"""
import os
import shutil
import subprocess

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
    raise SystemExit('磁盘准备只允许 GitHub 官方临时托管运行器')
paths = ['/usr/local/lib/android', '/usr/share/dotnet', '/opt/ghc', '/opt/hostedtoolcache/CodeQL']
subprocess.run(['sudo', 'rm', '-rf', '--', *paths], check=True)
free = shutil.disk_usage('.').free // (1024 ** 3)
print(f'源码构建可用磁盘：{free} GiB')
if free < 30:
    raise SystemExit('可用空间小于30 GiB，请使用磁盘更大的托管运行器')
