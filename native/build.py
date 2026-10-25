"""本机原生发行包构建；仅消费锁定官方产物和真实 submodule。"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "var/native"


def run(args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    # Python 3.12+ data filter拒绝目录穿越、设备和逃逸链接。
    with tarfile.open(archive) as tar:
        if not hasattr(tarfile, "data_filter"):
            raise SystemExit("构建需要 Python 3.12+（或带 tar data filter 的维护版本）")
        tar.extractall(destination, filter="data")


def fetch(item):
    cache = BASE / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    filename = item["url"].rsplit("/", 1)[-1]
    previous = cache / filename
    if previous.exists() and digest(previous) == item["sha256"]:
        return previous
    cache = cache / item["sha256"][:16]
    cache.mkdir(exist_ok=True)
    path = cache / filename
    if not path.exists():
        part = path.with_name(path.name + ".part")
        run(["curl", "--fail", "--location", "--retry", "4", "--connect-timeout", "20",
             "--max-time", "600", item["url"], "-o", part])
        if digest(part) != item["sha256"]:
            raise SystemExit("下载摘要错误：" + path.name)
        part.replace(path)
    if digest(path) != item["sha256"]:
        raise SystemExit("缓存摘要错误：" + path.name)
    return path


def copy_tree(source, destination):
    shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def install_python(project, uv, python, environment):
    """在线使用 frozen sync；离线使用同一锁导出的带哈希 wheel 清单。"""
    wheelhouse = os.getenv("ANP_WHEELHOUSE")
    if not wheelhouse:
        run([uv, "sync", "--project", project, "--frozen", "--no-dev", "--no-install-project", "--python", python], env=environment)
        return
    target = Path(environment["UV_PROJECT_ENVIRONMENT"])
    requirements = BASE / (project.name + "-requirements.txt")
    run([uv, "export", "--project", project, "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt", "--output-file", requirements], env=environment, stdout=subprocess.DEVNULL)
    run([uv, "venv", "--clear", "--python", python, target], env=environment)
    run([uv, "pip", "sync", "--python", target / "bin/python", "--require-hashes", "--no-index", "--find-links", wheelhouse, requirements], env=environment)


def macho_audit(bundle, source_built=None):
    """审计包外依赖；保留有效官方签名，修复无效签名和源码 DBX 签名。"""
    source_built = {"platform/dbx/dbx-web"} if source_built is None else set(source_built)
    result = {"mach_o_files": 0, "resigned": [], "linker_stub_signatures": [], "preserved_upstream_signatures": [], "external_dependencies": []}
    for p in bundle.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        with p.open("rb") as f:
            magic = f.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            continue
        result["mach_o_files"] += 1
        # -L 混合显示 LC_ID_DYLIB、自身路径及 fat 架构标题；它们不是加载依赖。
        # 按实际 load command 判断，不能按构建机路径前缀放宽白名单。
        commands = subprocess.check_output(["otool", "-l", str(p)], text=True)
        loads = {"LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB",
                 "LC_LOAD_UPWARD_DYLIB", "LC_LAZY_LOAD_DYLIB"}
        command = None
        libraries = []
        for line in commands.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == "cmd":
                command = fields[1]
            match = re.fullmatch(r"\s*name (.+) \(offset [0-9]+\)", line)
            if command in loads and match:
                libraries.append(match.group(1))
        forbidden = sorted({name for name in libraries if name.startswith("/")
                            and not name.startswith(("/usr/lib/", "/System/Library/"))})
        if forbidden:
            result["external_dependencies"].append({"path": str(p.relative_to(bundle)), "libraries": forbidden})
        signature = subprocess.run(["codesign", "-dvv", str(p)], capture_output=True, text=True)
        linker_stub = "linker-signed" in signature.stderr
        verified = subprocess.run(["codesign", "--verify", "--strict", str(p)], capture_output=True)
        if linker_stub:
            result["linker_stub_signatures"].append(str(p.relative_to(bundle)))
        # linker stub 的哈希校验可通过，但 macOS 26 AMFI 仍可能拒绝。
        # 在归档前生成完整签名；不修改系统安全设置或下载隔离标记。
        relative = str(p.relative_to(bundle))
        if verified.returncode or (linker_stub and relative in source_built):
            run(["codesign", "--force", "--sign", "-", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run(["codesign", "--verify", "--strict", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            final = subprocess.run(["codesign", "-dvv", str(p)], capture_output=True, text=True, check=True)
            assert "linker-signed" not in final.stderr, "仍存在 linker 简化签名：" + str(p)
            result["resigned"].append(str(p.relative_to(bundle)))
        else:
            result["preserved_upstream_signatures"].append(relative)
    if result["external_dependencies"]:
        raise SystemExit("发现包外动态库依赖：" + json.dumps(result["external_dependencies"]))
    return result


def build(target, profile="platform"):
    version = os.getenv("ANP_VERSION", (ROOT / "native/VERSION").read_text().strip())
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", version):
        raise SystemExit("ANP_VERSION 必须是有效的三段版本号，可附带预发布后缀")
    host = ("macos" if platform.system() == "Darwin" else platform.system().lower()) + "-" + platform.machine()
    host = host.replace("linux-x86_64", "linux-amd64")
    if target != host or target not in ("macos-arm64", "linux-amd64"):
        raise SystemExit(f"需要在目标架构原生构建；当前 host={host}，请求 target={target}")
    if os.geteuid() == 0:
        raise SystemExit("请用普通用户构建和运行")
    lock = json.loads((ROOT / "native/locks" / (target + ".json")).read_text())
    destination = BASE / "build" / ("anp-" + target)
    if (destination / "var").exists() and any((destination / "var").iterdir()):
        raise SystemExit("构建目录已有运行数据，请先备份并移走 var；拒绝覆盖")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="assemble-", dir=destination.parent))
    try:
        for section in ("tools", "storage", "platform", "runtime", "config", "defaults", "manifest"):
            (temporary / section).mkdir()
        with tempfile.TemporaryDirectory(prefix="extract-", dir=BASE) as scratch:
            unpacked = Path(scratch)
            for name, item in lock["artifacts"].items():
                print("官方产物：" + name, flush=True)
                if item.get("install") == "macos-redis":
                    from otel_build import install_redis
                    install_redis(temporary, fetch(item))
                    continue
                folder = unpacked / name
                if item.get("format") == "deb":
                    folder.mkdir()
                    run(["dpkg-deb", "-x", fetch(item), folder])
                    if item.get("install") == "elf-tools":
                        helper = temporary / "tools/elf"
                        helper.mkdir()
                        shutil.copy2(folder / "usr/bin/patchelf", helper / "patchelf")
                        copy_tree(folder / "usr/share/doc/patchelf", helper / "licenses")
                        continue
                    redis = temporary / "storage/redis"
                    redis.mkdir(exist_ok=True)
                    names = ("redis-server",) if name == "redis-server" else ("redis-cli",)
                    for binary in names:
                        shutil.copy2(folder / "usr/bin" / binary, redis / binary)
                    copy_tree(folder / "usr/share/doc" / name, redis / (name + "-licenses"))
                    continue
                extract(fetch(item), folder)
                if item.get("install") == "elf-tools":
                    helper = temporary / "tools/elf"
                    helper.mkdir()
                    shutil.copy2(next(path for path in folder.rglob("patchelf") if path.is_file()), helper / "patchelf")
                    copy_tree(folder / "share/doc/patchelf", helper / "licenses")
                    continue
                children = list(folder.iterdir())
                payload = children[0] if len(children) == 1 and children[0].is_dir() else folder
                section = "storage" if name == "postgres" else "platform" if name == "postgrest" else "tools"
                copy_tree(payload, temporary / section / name)
        for p in (temporary / "storage/postgres/bin").iterdir():
            if p.is_file():
                p.chmod(p.stat().st_mode | 0o111)
                if target == "linux-amd64" and p.read_bytes()[:12].startswith(b"#!/bin/bash"):
                    text = p.read_text()
                    assert "${BASH_SOURCE[0]}" in text, "未知 PostgreSQL Bash 启动器：" + p.name
                    p.write_text(text.replace("#!/bin/bash", "#!/bin/sh", 1).replace("${BASH_SOURCE[0]}", "$0"))
        # PGXS 是扩展开发/回归测试工具，引用上游 Nix 构建路径，不属于运行包。
        shutil.rmtree(temporary / "storage/postgres/lib/pgxs", ignore_errors=True)
        # 官方 PostgREST macOS 包依赖 Homebrew libpq；绑定随包官方 PostgreSQL libpq。
        postgrest = temporary / "platform/postgrest/postgrest"
        if target == "macos-arm64":
            run(["install_name_tool", "-change", "/opt/homebrew/opt/libpq/lib/libpq.5.dylib",
                 "@executable_path/../../storage/postgres/lib/libpq.5.dylib", postgrest])
        python = temporary / "tools/python/bin/python3"
        uv = temporary / "tools/uv/uv"
        run([python, "--version"])
        run([temporary / "tools/node/bin/node", "--version"])
        print("LiteLLM：冻结依赖安装", flush=True)
        environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(BASE / "venvs/litellm"), "UV_PYTHON_DOWNLOADS": "0"}
        install_python(ROOT / "native/packages/litellm", uv, python, environment)
        lite = temporary / "platform/litellm"
        lite.mkdir()
        packages = next((BASE / "venvs/litellm/lib").glob("python*/site-packages"))
        copy_tree(packages, lite / "site-packages")
        shutil.copy2(ROOT / "native/packages/litellm/uv.lock", lite / "uv.lock")
        if profile == "platform":
            import platform_build
            platform_build.install(temporary, fetch, copy_tree, target)
            import otel_build
            otel_build.install(temporary, target, fetch, copy_tree)
        shutil.copy2(ROOT / "native/defaults/oauth.example.json", temporary / "defaults/oauth.example.json")
        shutil.copytree(ROOT / "native/runtime", temporary / "runtime", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if profile == "platform":
            import litellm_build
            litellm_build.generate(temporary, target, fetch)
        shutil.copy2(ROOT / "native/anp", temporary / "anp")
        (temporary / "anp").chmod(0o755)
        defaults = ROOT / "native/defaults" / ("platform.json" if profile == "platform" else "anp.json")
        shutil.copy2(defaults, temporary / "defaults/anp.json")
        if profile == "platform":
            shutil.copy2(ROOT / "native/defaults/external-resources.example.json", temporary / "defaults/external-resources.example.json")
        shutil.copy2(ROOT / "native/README.md", temporary / "README.md")
        (temporary / "VERSION").write_text(version + "\n")
        if target == "macos-arm64":
            audit = macho_audit(temporary)
            (temporary / "manifest/macho-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
        else:
            import linux_elf
            linux_elf.prepare(temporary)
        shutil.copy2(ROOT / "native/locks" / (target + ".json"), temporary / "manifest/sources.lock.json")
        license_dir = temporary / "manifest/licenses"
        license_dir.mkdir(exist_ok=True)
        for name, item in lock.get("licenses", {}).items():
            license_file = ROOT / "native/licenses" / name
            assert digest(license_file) == item["sha256"], "许可证校验失败：" + name
            shutil.copy2(license_file, license_dir / name)
        run([temporary / "storage/postgres/bin/postgres", "--version"])
        run([temporary / "platform/postgrest/postgrest", "--version"])
        run([temporary / "storage/redis/redis-server", "--version"])
        if target == "linux-amd64" and (temporary / "var").exists():
            if (temporary / "var/data").exists():
                raise RuntimeError("版本检查意外生成业务数据，拒绝组装")
            shutil.rmtree(temporary / "var")
        inputs = [ROOT / "native/defaults/oauth.example.json", ROOT / "native/build.py", ROOT / "native/otel_build.py", *list((ROOT / "native/runtime").glob("*.py")),
                  ROOT / "native/anp", ROOT / "native/VERSION", ROOT / "native/README.md", defaults, ROOT / "native/packages/litellm/uv.lock", ROOT / ("native/locks/" + target + ".json")]
        if target == "linux-amd64":
            inputs += [ROOT / "native/linux_elf.py", ROOT / "native/linux/nss.c"]
        if profile == "platform":
            inputs += [ROOT / "native/litellm_build.py", ROOT / "native/runtime/bin/prisma", ROOT / "native/defaults/external-resources.example.json", ROOT / ("native/locks/litellm-prisma-" + target + ".json"), ROOT / "native/packages/litellm-prisma/package-lock.json"]
            inputs += [ROOT / "native/platform_build.py", ROOT / "native/langfuse_build.py", ROOT / "native/dbx_build.py", ROOT / "native/business_build.py", ROOT / ("native/locks/platform-" + target + ".json"), ROOT / ("native/locks/build-tools-" + target + ".json"), ROOT / "native/packages/build-tools/package-lock.json", ROOT / "native/packages/prisma/package-lock.json", ROOT / "native/packages/dbx-tools/package-lock.json", ROOT / "native/packages/business-tools/package-lock.json", ROOT / "business/uv.lock", ROOT / "business/ui/pnpm-lock.yaml"]
        if profile == "platform":
            inputs += [ROOT / ("native/locks/otel-" + target + ".json"), *[p for p in (ROOT / "native/defaults/otel").rglob("*") if p.is_file()]]
            for folder in ("business/src", "business/ui/src", "business/ui/public"):
                inputs += [p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
            inputs += [ROOT / "business/langgraph.json", ROOT / "business/config.example.json"]
        commit = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.run(["git", "-C", ROOT, "diff", "--quiet", "HEAD", "--", "native", "business"], check=False).returncode != 0
        (temporary / "manifest/build.json").write_text(json.dumps({"target": target, "profile": profile, "version": version,
            "source_commit": commit, "source_dirty": dirty,
            "redis_distribution": "official-binary" if target == "macos-arm64" else "official-deb",
            "inputs_sha256": {str(p.relative_to(ROOT)): digest(p) for p in inputs},
            "adaptations": (["postgres bin chmod +x", "remove postgres PGXS development tests", "postgrest and official Redis use bundled PostgreSQL OpenSSL/libpq", "preserve valid upstream signatures; sign modified Mach-O and source-built DBX"] if target == "macos-arm64" else
                            ["official Redis Debian artifacts", "PostgreSQL wrappers use POSIX sh", "bundled ELF library closure", "relocated loader in instance cache without changing PT_LOAD"]),
            "included": ["uv", "python", "node", "postgres", "redis", "postgrest", "litellm"] + (["auth", "storage-api", "meta", "studio", "langfuse-web", "langfuse-worker", "clickhouse", "rustfs", "dbx", "business", "otelcol", "grafana", "prometheus", "loki", "tempo"] if profile == "platform" else []),
            "excluded": ["realtime", "pyroscope", "ebpf"] + ([] if profile == "platform" else ["langfuse", "studio", "auth", "storage-api", "business"])}, indent=2) + "\n")
        hashes = {str(p.relative_to(temporary)): digest(p) for p in temporary.rglob("*") if p.is_file() and not p.is_symlink()}
        (temporary / "manifest/files.sha256.json").write_text(json.dumps(hashes, sort_keys=True, indent=2) + "\n")
        if destination.exists():
            shutil.rmtree(destination)
        temporary.replace(destination)
        print("构建完成：" + str(destination))
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def dist(target):
    bundle = BASE / "build" / ("anp-" + target)
    if not (bundle / "manifest/build.json").exists():
        raise SystemExit("请先 make build")
    # 只按不可变文件清单打包，用户配置、数据、日志、秘密一概不进入制品。
    hashes = json.loads((bundle / "manifest/files.sha256.json").read_text())
    for name, expected in hashes.items():
        if digest(bundle / name) != expected:
            raise SystemExit("构建文件已变化，请重建：" + name)
    exports = ROOT / "var/exports"
    exports.mkdir(parents=True, exist_ok=True)
    version = json.loads((bundle / "manifest/build.json").read_text()).get("version", "0.1.0-poc")
    archive = exports / ("anp-" + version + "-" + target + ".tar.gz")
    part = archive.with_suffix(".part")
    with tarfile.open(part, "w:gz") as tar:
        for section in ("tools", "storage", "platform", "runtime", "config", "defaults", "manifest"):
            tar.add(bundle / section, arcname=bundle.name + "/" + section, recursive=False)
        if (bundle / "business").exists():
            tar.add(bundle / "business", arcname=bundle.name + "/business", recursive=False)
        for name in sorted([*hashes, "manifest/files.sha256.json"]):
            tar.add(bundle / name, arcname=bundle.name + "/" + name, recursive=False)
        # 保留包内相对符号链接；不跟随链接打包包外文件。
        for path in sorted(bundle.rglob("*")):
            if path.relative_to(bundle).parts[0] not in {"tools", "storage", "platform", "runtime", "defaults", "manifest", "business"}:
                continue
            if path.is_symlink():
                if not path.resolve().is_relative_to(bundle.resolve()):
                    raise SystemExit("符号链接逃逸：" + str(path))
                tar.add(path, arcname=bundle.name + "/" + str(path.relative_to(bundle)), recursive=False)
    part.replace(archive)
    checksum = digest(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(checksum + "  " + archive.name + "\n")
    print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size, "sha256": checksum}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "dist"])
    parser.add_argument("--target", default="macos-arm64")
    parser.add_argument("--profile", choices=["poc", "platform"], default="platform")
    args = parser.parse_args()
    BASE.mkdir(parents=True, exist_ok=True)
    with (BASE / "build.lock").open("a") as lockfile:
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("另一个原生构建/打包正在运行")
        if args.command == "build":
            build(args.target, args.profile)
        else:
            dist(args.target)
