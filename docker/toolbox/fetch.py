"""仅从版本锁指定的官方URL获取产物，完整校验后再解包。"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import uuid

lock = json.loads(Path('/build/versions.lock.json').read_text())
name, arch, destination = sys.argv[1:]
entry = lock['artifacts'][name].get(arch) or lock['artifacts'][name]['all']
with tempfile.TemporaryDirectory() as folder:
    cache = Path('/root/.cache/anp-artifacts') / entry['sha256']
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / entry['url'].rsplit('/', 1)[-1]
    if not archive.exists():
        partial = Path(folder) / archive.name
        subprocess.run(['curl', '--fail', '--silent', '--show-error', '--location', '--retry', '5', '--retry-all-errors', '--retry-delay', '2', '--connect-timeout', '30', '--max-time', '1800', '--output', str(partial), entry['url']], check=True)
        with partial.open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == entry['sha256'], name + ' 校验失败'
        cached_partial = cache / ('download-' + uuid.uuid4().hex)
        shutil.copyfile(partial, cached_partial)
        cached_partial.replace(archive)
    with archive.open('rb') as source:
        assert hashlib.file_digest(source, 'sha256').hexdigest() == entry['sha256'], name + ' 校验失败'
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith('.deb'):
        subprocess.run(['dpkg-deb', '--extract', str(archive), str(target)], check=True)
    elif archive.name.endswith('.zip'):
        with zipfile.ZipFile(archive) as package:
            for member in package.namelist():
                assert (target / member).resolve().is_relative_to(target.resolve())
            package.extractall(target)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as package:
            package.extractall(target, filter='data')
    else:
        shutil.copyfile(archive, target / name)
    for file in target.rglob('*'):
        if file.is_file() and (file.name in ('auth', 'postgrest', 'rustfs', 'envoy', 'clickhouse') or not file.suffix):
            file.chmod(0o755)
print(name, arch, '官方产物校验和安装完成')
