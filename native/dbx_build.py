"""DBX Web：Mac 无官方 Web 产物，使用固定源码和冻结依赖构建。"""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "var/native"
COMMIT = "46b773d05e3162170a5358805588ce1fa60f3306"


def main(bundle=None):
    source = ROOT / "platform/sources/components/dbx"
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != COMMIT:
        raise RuntimeError("DBX submodule 与锁定版本不一致")
    if subprocess.check_output(["git", "-C", str(source), "diff", "--name-only", "HEAD"], text=True).strip():
        raise RuntimeError("DBX 源码有修改，拒绝构建")
    fingerprint = hashlib.sha256(Path(__file__).read_bytes() + (ROOT / "native/packages/dbx-tools/package-lock.json").read_bytes()).hexdigest()
    binary = BASE / "dbx-target/release/dbx-web"
    receipt = BASE / "dbx-build/build.json"
    if binary.exists() and receipt.exists():
        value = json.loads(receipt.read_text())
        if value.get("recipe_sha256") == fingerprint and value.get("commit") == COMMIT and value.get("binary_sha256") == hashlib.file_digest(binary.open("rb"), "sha256").hexdigest():
            return
    work = BASE / "dbx-build/work"
    bundle = bundle or BASE / "build/anp-macos-arm64"
    node = bundle / "tools/node/bin"
    tools = BASE / "dbx-tools"
    environment = {**os.environ, "PATH": f"{node}:{tools}/node_modules/.bin:{BASE}/rust/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                   "HUSKY": "0", "CI": "1", "DBX_PUBLIC_BASE_PATH": "/dbx",
                   "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/", "NPM_CONFIG_USERCONFIG": "/dev/null",
                   "CARGO_HOME": str(BASE / "cargo-home"), "CARGO_TARGET_DIR": str(BASE / "dbx-target"),
                   "CARGO_BUILD_JOBS": "3", "CARGO_INCREMENTAL": "0"}
    def run(args, **kw):
        subprocess.run([str(x) for x in args], env=environment, check=True, **kw)
    if not work.exists():
        work.mkdir(parents=True)
        archive = BASE / "dbx-build/source.tar"
        run(["git", "-C", source, "archive", "--format=tar", "--output", archive, COMMIT])
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        archive.unlink()
    run([node / "npm", "ci", "--prefix", ROOT / "native/packages/dbx-tools", "--ignore-scripts"])
    # npm 的锁文件属于仓库，实际工具安装目录放在可重建缓存。
    import shutil
    tools.mkdir(parents=True, exist_ok=True)
    if (tools / "node_modules").exists():
        shutil.rmtree(tools / "node_modules")
    shutil.copytree(ROOT / "native/packages/dbx-tools/node_modules", tools / "node_modules", symlinks=True)
    run(["pnpm", "install", "--frozen-lockfile", "--store-dir", BASE / "dbx-pnpm-store"], cwd=work)
    run(["pnpm", "build"], cwd=work)
    run(["cargo", "build", "--locked", "--release", "-p", "dbx-web", "--no-default-features", "--features",
         "embed-static,duckdb-sidecar,dynamodb,mq-admin,dbx-core/sqlite-bundled"], cwd=work)
    (BASE / "dbx-build/build.json").write_text(json.dumps({"version": "0.6.51", "commit": COMMIT,
        "source": "https://github.com/t8y2/dbx.git", "frontend_base": "/dbx", "native_web": True,
        "recipe_sha256": fingerprint, "binary_sha256": hashlib.file_digest(binary.open("rb"), "sha256").hexdigest()}, indent=2) + "\n")


if __name__ == "__main__":
    main()
