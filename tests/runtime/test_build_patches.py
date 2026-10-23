"""构建补丁必须只修改真实配置，保留注释中的上游示例。"""
from pathlib import Path
import shlex
import subprocess
import sys


def test_langfuse_worker_patch_ignores_commented_example(tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    import generate_dockerfile
    line=next(line for line in generate_dockerfile.langfuse('web').splitlines()
              if 'rm -f ./web/src/middleware.ts && python3' in line)
    args=shlex.split(line)
    payload=args[args.index('python3')+2]
    path=tmp_path/'web/next.config.mjs';path.parent.mkdir()
    original="export default {\n  experimental: {\n    clientRouterFilter: false,\n  },\n  // Example: experimental: { appDir: true }\n}\n"
    path.write_text(original)
    result=subprocess.run([sys.executable,'-c',payload],cwd=tmp_path,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    output=path.read_text()
    assert '  experimental: {\n    cpus: 2,' in output
    assert '// Example: experimental: { appDir: true }' in output
