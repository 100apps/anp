import hashlib
"""平台扩展构建：官方原生产物优先，Langfuse 从锁定源码编译。"""
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "var/native"


def artifact_binary(directory, name):
    """排除同名目录与 shell 补全，仅安装唯一 ELF/Mach-O 原生文件。"""
    magic = {b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
             b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
             b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
    candidates = []
    for path in directory.rglob(name):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in magic:
                    candidates.append(path)
    if len(candidates) != 1:
        raise RuntimeError("官方归档必须有唯一原生二进制 " + name + "，实际：" + str(len(candidates)))
    return candidates[0]


def strip_runtime_environment_files(directory):
    """发行目录只接受启动器注入的环境；不读取上游默认环境文件的值。"""
    names = {".env", ".env.local", ".env.production", ".env.production.local",
             ".env.development", ".env.development.local", ".env.test", ".env.test.local"}
    removed = []
    for path in directory.rglob("*"):
        if path.name in names and path.is_file():
            removed.append(str(path.relative_to(directory)))
            path.unlink()
    return removed


def repair_prisma_snapshots(runtime, node):
    """pnpm 12 同时保留规范快照与兼容快照；生成物必须同步到每个加载位置。"""
    modules = runtime / "worker/node_modules"
    # Web 的追踪输出已带上实际 schema 生成的客户端；worker 中的安装后
    # 副作用可能只是空客户端，不能以 glob 的第一个快照为依据。
    client = (runtime / "web/node_modules/@prisma/client").resolve()
    generated = client.parent.parent / ".prisma"
    assert client.exists() and generated.exists(), "缺少编译阶段生成的 Prisma 客户端"
    targets = [p.parent for p in modules.rglob("package.json")
               if p.parent.name == "client" and p.parent.parent.name == "@prisma"
               ]
    repaired = []
    for target in targets:
        own_generated = target.parent.parent / ".prisma"
        for source, destination in ((client, target), (generated, own_generated)):
            if destination.is_symlink():
                destination.unlink()
            elif destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination, symlinks=True)
        repaired.append(str(target.relative_to(runtime)))
    shared = next(modules.glob(".pnpm/@langfuse+shared@*/node_modules/@langfuse/shared/package.json"))
    subprocess.run([str(node), "-e", "const r=require('module').createRequire(process.argv[1]);if(!r('@prisma/client').MonitorSeverity)throw Error('Prisma enum missing')", str(shared)], check=True)
    (runtime / "prisma-deployment.json").write_text(json.dumps({"repaired_snapshots": repaired,
        "shared_package_enum_load_verified": True}, indent=2) + "\n")


def rust_licenses(destination):
    """保留静态链接 Rust 依赖的许可证，不把构建缓存放进运行包。"""
    result = subprocess.run([str(BASE / "rust/bin/cargo"), "metadata", "--offline", "--locked", "--format-version", "1"],
        cwd=BASE / "langfuse-build/work/packages/native", env={**os.environ, "CARGO_HOME": str(BASE / "cargo-home"),
            "PATH": str(BASE / "rust/bin") + ":" + os.environ["PATH"]}, capture_output=True, text=True, check=True)
    entries = []
    for package in json.loads(result.stdout)["packages"]:
        source = Path(package["manifest_path"]).parent
        files = {p for p in source.iterdir() if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))}
        if package.get("license_file"):
            candidate = (source / package["license_file"]).resolve()
            if candidate.is_relative_to(BASE / "cargo-home") and candidate.is_file():
                files.add(candidate)
        target = destination / (package["name"] + "-" + package["version"])
        if files:
            target.mkdir(parents=True, exist_ok=True)
            for p in files:
                shutil.copy2(p, target / p.name)
        entries.append({"name": package["name"], "version": package["version"], "license": package.get("license"),
            "repository": package.get("repository"), "files": sorted(p.name for p in files)})
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "index.json").write_text(json.dumps(entries, indent=2) + "\n")


def unpack(archive, destination):
    """所有链接/成员先经过 tar data filter；zip 不允许逃逸。"""
    destination.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if not (destination / info.filename).resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError("zip 路径逃逸")
            z.extractall(destination)
        return
    if archive.name.endswith(".zst"):
        # macOS 自带 bsdtar 重打包归档流；无需额外安装 zstd。
        # 不在这里提取文件，最终提取仍受 Python data filter 约束。
        with tempfile.TemporaryFile() as stream:
            args = ["zstd", "-dc", str(archive)] if platform.system() == "Linux" else ["/usr/bin/tar", "-cf", "-", "@" + str(archive)]
            subprocess.run(args, stdout=stream, check=True)
            stream.seek(0)
            with tarfile.open(fileobj=stream) as tar:
                tar.extractall(destination, filter="data")
    else:
        with tarfile.open(archive) as tar:
            tar.extractall(destination, filter="data")


def install(bundle, fetch, copy_tree, target="macos-arm64"):
    lock_path = ROOT / "native/locks" / ("platform-" + target + ".json")
    lock = json.loads(lock_path.read_text())
    import langfuse_build
    from build import extract, run
    tool_lock = json.loads((ROOT / "native/locks" / ("build-tools-" + target + ".json")).read_text())
    if not (BASE / "rust/bin/rustc").exists():
        for item in tool_lock["artifacts"].values():
            with tempfile.TemporaryDirectory(prefix="rust-install-", dir=BASE) as scratch:
                extract(fetch(item), Path(scratch))
                package = next(Path(scratch).iterdir())
                run(["/bin/sh", package / "install.sh", "--prefix=" + str(BASE / "rust"), "--disable-ldconfig"])
    # rustc 分组件安装器没有为 rust-objcopy 的相对 rpath 放置 LLVM 链接。
    # Cargo 会重写 DYLD_LIBRARY_PATH，因此在项目自己的工具链内修复相对布局。
    llvm = BASE / "rust/lib/rustlib/aarch64-apple-darwin/lib/libLLVM.dylib"
    if target == "macos-arm64" and not llvm.exists():
        llvm.symlink_to("../../../libLLVM.dylib")
    langfuse_build.main(bundle, target=target)
    # npm 安装脚本下载的引擎也需要固定原始文件校验，不能只依赖包版本。
    runtime = BASE / "langfuse-build/runtime"
    verified = []
    for name, item in lock["prisma_engines"].items():
        paths = list(runtime.rglob(name))
        if not paths:
            raise RuntimeError("缺少 Prisma 官方引擎：" + name)
        for path in paths:
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["uncompressed_sha256"]:
                raise RuntimeError("Prisma 官方引擎校验失败：" + name)
            verified.append({"path": path.relative_to(runtime).as_posix(), "uncompressed_sha256": item["uncompressed_sha256"], "url": item["url"]})
    (bundle / "manifest/prisma-engines-verified.json").write_text(json.dumps({"phase": "before ELF relocation and code signing", "files": verified}, indent=2) + "\n")
    print("Prisma 官方引擎固定校验通过：" + str(len(verified)) + " 个文件", flush=True)
    repair_prisma_snapshots(BASE / "langfuse-build/runtime", bundle / "tools/node/bin/node")
    for name, item in lock["artifacts"].items():
        print("平台官方产物：" + name, flush=True)
        destination = bundle / item["destination"]
        with tempfile.TemporaryDirectory(prefix="platform-extract-", dir=BASE) as scratch:
            folder = Path(scratch)
            unpack(fetch(item), folder)
            if item.get("root"):
                folder = folder / item["root"]
            if item.get("binary"):
                destination.mkdir(parents=True, exist_ok=True)
                candidate = artifact_binary(folder, item["binary"])
                installed_name = item.get("installed_name", item["binary"])
                shutil.copy2(candidate, destination / installed_name)
                (destination / installed_name).chmod(0o755)
                for license_file in folder.rglob("LICENSE*"):
                    if license_file.is_file():
                        target_license = destination / "licenses" / license_file.relative_to(folder)
                        target_license.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(license_file, target_license)
            else:
                copy_tree(folder, destination)
        strip_runtime_environment_files(destination)
    # 保留原始许可证；官方 Auth 二进制包未携带 LICENSE。
    licenses = bundle / "manifest/platform-licenses"
    licenses.mkdir()
    rust_licenses(licenses / "rust")
    for name, item in lock.get("licenses", {}).items():
        source = ROOT / "native/licenses" / name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"], "平台许可证校验失败：" + name
        shutil.copy2(source, licenses / name)
    for relative in ("platform/sources/components/auth/LICENSE", "platform/sources/langfuse/LICENSE", "platform/sources/langfuse/ee/LICENSE"):
        source = ROOT / relative
        if source.exists():
            shutil.copy2(source, licenses / ("-".join(source.parts[-3:]).replace(".", "_") + ".txt"))
    langfuse = BASE / "langfuse-build/runtime"
    if not (langfuse / "web/web/server.js").exists() or not (langfuse / "worker/dist/index.js").exists():
        raise RuntimeError("请先执行 python3 native/langfuse_build.py，完成锁定源码的原生构建")
    receipt = json.loads((langfuse / "build.json").read_text())
    assert receipt["commit"] == lock["sources"]["langfuse"]["commit"]
    copy_tree(langfuse, bundle / "platform/langfuse")
    dbx = bundle / "platform/dbx"
    if target == "macos-arm64":
        import dbx_build
        dbx_build.main(bundle)
        dbx.mkdir()
        shutil.copy2(BASE / "dbx-target/release/dbx-web", dbx / "dbx-web")
        shutil.copy2(BASE / "dbx-build/build.json", dbx / "build.json")
    for source in (ROOT / "platform/sources/components/dbx").glob("LICENSE*"):
        shutil.copy2(source, dbx / source.name)
    import business_build
    business_build.install(bundle, copy_tree)
    shutil.copy2(lock_path, bundle / "manifest/platform-sources.lock.json")
