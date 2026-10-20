"""镜像内安装检查与独立身份，不产生运行数据或预置密码。"""
import os
from pathlib import Path

NAMES = ['postgres','redis','clickhouse','rustfs','dbx','langfuse-web','langfuse-worker','litellm',
         'supabase-studio','supabase-meta','supabase-auth','supabase-rest','supabase-storage','supabase-gateway']
passwd = Path('/etc/passwd')
group = Path('/etc/group')
with passwd.open('a') as p, group.open('a') as g:
    for number, name in enumerate(NAMES,20001):
        p.write(f'anp3-{name}:x:{number}:{number}:ANP3 {name}:/data/{name}:/bin/sh\n')
        g.write(f'anp3-{name}:x:{number}:\n')
# LiteLLM 以运行配置改写静态 UI；仅将此必要目录交给它的专属用户。
for p in Path('/app/.venv/lib').glob('python*/site-packages/litellm/proxy/_experimental'):
    for base, dirs, files in os.walk(p):
        os.chown(base,20008,20008)
        for name in files:
            os.chown(Path(base)/name,20008,20008,follow_symlinks=False)
print('独立服务身份与应用目录准备完成')
