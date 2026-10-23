"""用锁定的 Langfuse submodule 生成 macOS Web/Worker 运行目录。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "var/native"
COMMIT = "71c840e0e9cc945df9c250bdf182f99497584d3a"


def main(bundle=None, assemble_only=False, target="macos-arm64"):
    source = ROOT / "platform/sources/langfuse"
    sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    assert sha == COMMIT, "Langfuse 源码提交与原生锁不一致"
    assert not subprocess.check_output(["git", "-C", str(source), "diff", "--name-only", "HEAD"], text=True).strip()
    node = (Path(bundle) if bundle else BASE / ("build/anp-" + target)) / "tools/node/bin"
    tools = BASE / "build-tools/node_modules/.bin"
    rust = BASE / "rust/bin"
    environment = {**os.environ, "PATH": f"{node}:{tools}:{rust}:" + os.environ["PATH"],
        "CI": "true", "HUSKY": "0", "NEXT_TELEMETRY_DISABLED": "1", "DOCKER_BUILD": "1",
        "NEXT_PUBLIC_BASE_PATH": "/langfuse", "NEXT_IGNORE_BUILD_ERRORS": "true",
        "NODE_OPTIONS": "--max-old-space-size=4096", "pnpm_config_verify_deps_before_run": "false",
        "npm_config_registry": "https://registry.npmjs.org/", "CARGO_BUILD_JOBS": "2",
        "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/", "NPM_CONFIG_USERCONFIG": "/dev/null",
        "XDG_CACHE_HOME": str(BASE / "npm-cache"), "NPM_CONFIG_CACHE": str(BASE / "npm-cache/npm"),
        "CARGO_HOME": str(BASE / "cargo-home"), "CARGO_TARGET_DIR": str(BASE / "cargo-target"),
        "CARGO_INCREMENTAL": "0", "CARGO_NET_GIT_FETCH_WITH_CLI": "true", "DISABLE_ERD": "true"}
    # 官方分组件 Rust 安装器把 LLVM 放在 lib/；rust-objcopy 的 rpath
    # 却只寻找 rustlib/<target>/lib。仅构建进程补充工具链库目录。
    environment["DYLD_LIBRARY_PATH"] = str(BASE / "rust/lib")
    build = BASE / "langfuse-build"
    build.mkdir(exist_ok=True)

    def run(args, cwd, label):
        print(label, flush=True)
        with (BASE / (label + ".log")).open("w") as log:
            subprocess.run(list(map(str, args)), cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)

    offline = ["--offline"] if os.getenv("ANP_PNPM_OFFLINE") == "1" else []

    tool_input = ROOT / "native/packages/build-tools"
    tool_output = BASE / "build-tools"
    tool_stamp = tool_output / ".lock-sha256"
    expected = hashlib.sha256((tool_input / "package-lock.json").read_bytes()).hexdigest()
    if not tools.exists() or not tool_stamp.exists() or tool_stamp.read_text() != expected:
        tool_output.mkdir(exist_ok=True)
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(tool_input / name, tool_output / name)
        run([node / "npm", "ci", "--prefix", tool_output, "--registry=https://registry.npmjs.org/", "--no-audit", "--no-fund"], ROOT, "native-tools")
        tool_stamp.write_text(expected)
    # 已完成的运行目录可复用；输入改变时重新编译，不能拿旧产物冒充新配置。
    fingerprint = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runtime = BASE / "langfuse-build/runtime"
    if (runtime / "build.json").exists():
        receipt = json.loads((runtime / "build.json").read_text())
        if not receipt.get("assembly_only") and receipt.get("recipe_sha256") == fingerprint and receipt.get("commit") == sha and receipt.get("target") == target:
            return
    work = build / "work"
    if not work.exists():
        run([tools / "turbo", "prune", "web", "worker", "--docker", "--out-dir=" + str(build / "pruned")], source, "langfuse-prune")
        for section in ("json", "full"):
            shutil.copytree(build / "pruned" / section, work, dirs_exist_ok=True, symlinks=True)
        for name in ("pnpm-lock.yaml", "pnpm-workspace.yaml"):
            if (build / "pruned" / name).exists():
                shutil.copy2(build / "pruned" / name, work / name)
    if not assemble_only:
        run([tools / "pnpm", "install", "--frozen-lockfile", *offline, "--registry=https://registry.npmjs.org/", "--store-dir", BASE / "pnpm-store"], work, "langfuse-install")
        config = work / "web/next.config.mjs"
        text = config.read_text()
        if "    cpus: 2," not in text:
            assert text.count("\n  experimental: {") == 1
            config.write_text(text.replace("\n  experimental: {", "\n  experimental: {\n    cpus: 2,", 1))
        (work / "web/src/middleware.ts").unlink(missing_ok=True)
        assert subprocess.check_output([str(rust / "rustc"), "--version"], text=True).startswith("rustc 1.98.0")
        # 先冻结取齐 Cargo 依赖，再让 napi metadata/build 离线运行，避免重复更新索引。
        cargo = [rust / "cargo", "fetch", "--locked"]
        ready = subprocess.run(list(map(str, [*cargo, "--offline"])), cwd=work / "packages/native", env=environment, capture_output=True)
        if ready.returncode:
            run(cargo, work / "packages/native", "langfuse-cargo-fetch")
        environment["CARGO_NET_OFFLINE"] = "true"
        run([tools / "turbo", "run", "build", "--filter=worker...", "--cache=local:r", "--concurrency=2"], work, "langfuse-worker-build")
        run([node / "node", "packages/shared/clickhouse/scripts/prepare-migrations.mjs", "materialize", "packages/shared/clickhouse/migrations"], work, "langfuse-clickhouse-materialize")
        run([tools / "turbo", "run", "build", "--filter=web...", "--cache=local:r", "--concurrency=2"], work, "langfuse-web-build")
        run([tools / "pnpm", "--filter", "web", "run", "typecheck"], work, "langfuse-web-typecheck")
    runtime.mkdir(exist_ok=True)
    if (runtime / "web").exists():
        shutil.rmtree(runtime / "web")
    shutil.copytree(work / "web/.next/standalone", runtime / "web", dirs_exist_ok=True, symlinks=True)
    for name in ("web/.next/static", "web/public", "packages/shared/prisma", "packages/shared/clickhouse", "packages/shared/scripts"):
        shutil.copytree(work / name, runtime / "web" / name, dirs_exist_ok=True, symlinks=True)
    shutil.copy2(source / "web/entrypoint.sh", runtime / "web/web/entrypoint.sh")
    # pnpm 12 拒绝工作区之外的 deploy 目标。先在工作区生成完整闭包，再搬入运行目录。
    staged = work / ".native-deploy/worker"
    if staged.exists():
        shutil.rmtree(staged)
    run([tools / "pnpm", "--filter", "worker", "deploy", "--prod", "--frozen-lockfile", *offline, "--registry=https://registry.npmjs.org/",
         "--store-dir", BASE / "pnpm-store", str(staged)], work, "langfuse-worker-deploy")
    deploy = runtime / "worker"
    if deploy.exists():
        shutil.rmtree(deploy)
    shutil.move(staged, deploy)
    # Prisma 生成物属于安装后的副作用，需要一起进入部署结果。
    for name in ("@prisma/client", ".prisma"):
        src = work / "worker/node_modules" / name
        if not src.exists():
            candidates = list((work / "node_modules/.pnpm").glob("@prisma+client@*/node_modules/" + name))
            assert candidates, "缺少 Prisma 客户端：" + name
            src = candidates[0]
        dst = deploy / "node_modules" / name
        if dst.is_symlink():
            dst.unlink()
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src.resolve(), dst, symlinks=True)
    prisma = runtime / "prisma"
    prisma.mkdir(exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(ROOT / "native/packages/prisma" / name, prisma / name)
    run([node / "npm", "ci", "--prefix", prisma, "--registry=https://registry.npmjs.org/", "--no-audit", "--no-fund"], ROOT, "langfuse-prisma-runtime")
    engines = prisma / "node_modules/@prisma/engines"
    engine_target = "darwin-arm64" if target == "macos-arm64" else "debian-openssl-3.0.x"
    assert (engines / ("schema-engine-" + engine_target)).exists(), "缺少 Prisma 原生迁移引擎"
    query = "libquery_engine-" + engine_target + (".dylib.node" if target == "macos-arm64" else ".so.node")
    assert (engines / query).exists(), "缺少 Prisma 原生查询引擎"
    shutil.copy2(source / "LICENSE", runtime / "LICENSE")
    (runtime / "build.json").write_text(json.dumps({"commit": sha, "recipe_sha256": fingerprint, "assembly_only": assemble_only, "rust": "1.98.0", "pnpm": "12.8.2", "turbo": "2.11.7",
        "source_lock_sha256": hashlib.sha256((source / "pnpm-lock.yaml").read_bytes()).hexdigest(),
        "target": target, "adaptations": ["Next cpus=2", "Next build and typecheck run sequentially", "native addon for " + target]}, indent=2) + "\n")
    print("Langfuse 原生构建完成：" + str(runtime))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--assemble-only", action="store_true", help="仅调试打包；此产物不供 make build 的编译缓存复用")
    args = parser.parse_args()
    main(assemble_only=args.assemble_only)
