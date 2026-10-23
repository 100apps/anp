"""以服务身份执行上游迁移和自托管种子，然后替换为 Realtime 前台进程。"""
import os
from pathlib import Path
import subprocess

ROOT = Path('/opt/anp/services/supabase-realtime')
ready = Path(os.environ['RELEASE_TMP']) / 'initialized'
ready.unlink(missing_ok=True)
command = str(ROOT / 'bin/realtime')
subprocess.run([command, 'eval', 'Realtime.Release.migrate()'], check=True)
subprocess.run([command, 'eval', 'Realtime.Release.seeds(Realtime.Repo)'], check=True)
ready.touch(mode=0o600)
os.execv(command, [command, 'start'])
