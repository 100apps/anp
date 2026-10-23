"""镜像内安装检查与独立身份，不产生运行数据或预置密码。"""
import os
import platform
import struct
import subprocess
from pathlib import Path

from settings import NAMES

passwd = Path('/etc/passwd')
group = Path('/etc/group')
with passwd.open('a') as p, group.open('a') as g:
    for number, name in enumerate(NAMES,20001):
        p.write(f'anp-{name}:x:{number}:{number}:ANP {name}:/data/{name}:/bin/sh\n')
        g.write(f'anp-{name}:x:{number}:\n')
# LiteLLM 以运行配置改写静态 UI；仅将此必要目录交给它的专属用户。
for p in Path('/app/.venv/lib').glob('python*/site-packages/litellm/proxy/_experimental'):
    for base, dirs, files in os.walk(p):
        os.chown(base,20008,20008)
        for name in files:
            os.chown(Path(base)/name,20008,20008,follow_symlinks=False)
print('独立服务身份与应用目录准备完成')

# 安装失败在镜像组装时阻断，避免直到用户启动才发现缺组件。
for executable in ('clickhouse','rustfs','supabase-auth','postgrest','envoy','dbx-web','redis-server','redis-cli','migrate'):
    path=Path('/opt/anp/bin')/executable
    assert path.is_file() and os.access(path,os.X_OK), executable+' 未安装'
for path in ('/opt/anp/services/supabase-realtime/bin/realtime',
             '/opt/anp/services/supabase-meta/node_modules/@supabase/postgres-meta/dist/server/server.js',
             '/opt/anp/runtimes/node22/bin/node','/opt/anp/runtimes/node24/bin/node'):
    assert Path(path).is_file(), path+' 未安装'
assert not Path('/opt/anp/bin/pgbouncer').exists(), '不再打包 PgBouncer'

# 防止下载产物或最终 COPY 将两种架构混装。
machine={'x86_64':62,'aarch64':183}[platform.machine()]
executables=[Path('/opt/anp/bin')/name for name in ('clickhouse','rustfs','supabase-auth','postgrest','envoy','dbx-web','redis-server','redis-cli','migrate')]
executables += [Path('/opt/anp/runtimes')/version/'bin/node' for version in ('node22','node24')]
executables += list(Path('/opt/anp/services/supabase-realtime').glob('erts-*/bin/beam.smp'))
assert len(executables)==12, 'Realtime 未包含 ERTS'
for executable in executables:
    with executable.open('rb') as source: header=source.read(20)
    assert header[:4]==b'\x7fELF' and struct.unpack('<H',header[18:20])[0]==machine, str(executable)+' 架构错误'

# 官方发行包仍需满足最终 Debian 运行库；静态 ELF 的 ldd 返回非零可接受。
for executable in executables:
    dependencies=subprocess.run(['ldd',str(executable)],capture_output=True,text=True)
    assert 'not found' not in dependencies.stdout+dependencies.stderr, str(executable)+' 缺少动态运行库'
print('官方二进制架构与动态运行库检查通过')
