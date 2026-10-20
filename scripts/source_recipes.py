"""同步锁定 submodule 的上游生产 Dockerfile；不修改上游源码。"""
import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPES = {
    "dbx/Dockerfile": ("dbx", "deploy/Dockerfile"),
    "langfuse/web.Dockerfile": ("langfuse", "web/Dockerfile"),
    "langfuse/worker.Dockerfile": ("langfuse", "worker/Dockerfile"),
    "litellm/Dockerfile": ("litellm", "Dockerfile"),
    "supabase/auth.Dockerfile": ("components/auth", "Dockerfile"),
    "supabase/meta.Dockerfile": ("components/postgres-meta", "Dockerfile"),
    "supabase/storage.Dockerfile": ("components/storage", "Dockerfile"),
    "supabase/studio.Dockerfile": ("supabase", "apps/studio/Dockerfile"),
}
result = {}
def adapt(dest, data):
    text = data.decode("utf-8")
    changes = []
    if text.startswith("# syntax="):
        text = text.split("\n", 1)[1]
        changes.append("使用Docker内置BuildKit前端，避免额外拉取构建前端")
    if dest == "dbx/Dockerfile":
        text = text.replace("AS frontend\n", "AS frontend\nARG VITE_DBX_BASE_PATH=/dbx\nENV VITE_DBX_BASE_PATH=$VITE_DBX_BASE_PATH\n", 1)
        changes.append("DBX前端编译写入/dbx前缀")
    if dest == "litellm/Dockerfile":
        text = re.sub(r'^(FROM \$LITELLM_(?:BUILD|RUNTIME)_IMAGE[^\n]*\n)', r'\1RUN printf "%s\\n" https://packages.wolfi.dev/os > /etc/apk/repositories\n', text, flags=re.MULTILINE)
        changes.append("使用已有官方签名密钥验证公开Wolfi软件仓库，避免私有APK仓库认证依赖")
        text = text.replace("RUN npm run build\n", "RUN npm run build -- --webpack\n")
        changes.append("Dashboard使用Next生产Webpack编译，规避锁定版本Turbopack内部冲突")
        text = re.sub(r'ADD --checksum=sha256:([a-f0-9]+)\s+\\?\s*(https://\S+) (/tmp/\S+)',
                      lambda m: 'RUN apk add --no-cache curl && curl -fsSL -o ' + m[3] + ' "' + m[2] + '" && echo "' + m[1] + '  ' + m[3] + '" | sha256sum -c -', text)
        changes.append("远程源码归档在RUN中下载以支持显式代理，保留上游SHA256校验")
    if dest == "supabase/studio.Dockerfile":
        for stage in ("build-next", "build-tanstack"):
            text = text.replace(f"FROM dev AS {stage}\n", f"FROM dev AS {stage}\nARG NEXT_PUBLIC_BASE_PATH=/supabase\nENV NEXT_PUBLIC_BASE_PATH=$NEXT_PUBLIC_BASE_PATH\n", 1)
        changes.append("Studio两个构建目标均写入/supabase前缀")
        patch = (ROOT / "docker/platform/supabase/studio_prefix.py").read_text(encoding="utf-8")
        command = "exec(" + repr(patch) + ")"
        text = text.replace('EXPOSE 8082\n', 'RUN python3 -c ' + shlex.quote(command) + '\nEXPOSE 8082\n', 1)
        changes.append("修正自托管Studio API_URL硬编码/api，使浏览器API请求保留平台前缀；仅镜像内补丁")
        text = text.replace("http://localhost:3000/api/platform/profile", "http://localhost:3000/supabase/api/platform/profile")
        changes.append("Studio镜像健康检查使用实际目录前缀")
    # 临时构建代理仅用于RUN环境，不将代理地址写进生产镜像ENV。
    text = re.sub(r"^RUN ((?:--[^ ]+ )*)", r'RUN \1export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" npm_config_https_proxy="$HTTPS_PROXY"; ', text, flags=re.MULTILINE)
    changes.append("构建命令兼容显式传入的临时HTTP(S)代理；运行镜像不保存代理")
    return text.encode(), changes

for dest, (name, source) in RECIPES.items():
    folder = ROOT / "platform/sources" / name
    sha = subprocess.check_output(["git", "-C", str(folder), "rev-parse", "HEAD"], text=True).strip()
    data = (folder / source).read_bytes()
    upstream_digest = hashlib.sha256(data).hexdigest()
    data, adaptations = adapt(dest, data)
    target = ROOT / "docker/platform" / dest
    target.write_bytes(data)
    ignore = folder / ".dockerignore"
    target.with_name(target.name + ".dockerignore").write_bytes(
        ignore.read_bytes() if ignore.exists() else b".git\nnode_modules\ndist\ntarget\n")
    result[dest] = {"source": name + "/" + source, "sha": sha,
                    "upstream_dockerfile_sha256": upstream_digest,
                    "dockerfile_sha256": hashlib.sha256(data).hexdigest(), "adaptations": adaptations}
(ROOT / "docker/source-recipes.json").write_text(json.dumps(result, indent=2) + "\n")
print("已同步锁定源码的生产配方")
