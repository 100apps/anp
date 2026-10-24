"""构建时收集 Linux 动态库闭包，并生成不依赖宿主 glibc 的入口。"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess


def elf(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def output(*arguments):
    return subprocess.check_output(list(map(str, arguments)), text=True)


def prune_node_prebuilds(bundle):
    """上游 npm 包同时携带多个平台预编译件；Linux 包只保留 x64 glibc。"""
    removed = []
    for path in list(bundle.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        parts = path.relative_to(bundle).parts
        if "node_modules" not in parts:
            continue
        reason = None
        if "prebuilds" in parts:
            index = parts.index("prebuilds")
            if index + 1 < len(parts) and re.match(r"(?:darwin|win32|linux|linuxglibc|linuxmusl)-", parts[index + 1]) and parts[index + 1] not in {"linux-x64", "linuxglibc-x64"}:
                reason = "other-platform-prebuild"
        if "musl" in path.name and path.suffix in {".node", ".so"}:
            reason = "musl-prebuild-with-bundled-glibc"
        if reason:
            removed.append({"path": path.relative_to(bundle).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "reason": reason})
            path.unlink()
    return removed


def normalize_slim_node_launchers(bundle):
    """保留官方 Node 版本和 execPath 预载，统一交给本包 ELF 搬移入口。"""
    changed = []
    for name in ("storage-api", "meta", "studio"):
        path = bundle / "platform" / name / "node/bin/node"
        if not path.exists():
            continue
        original = path.read_text()
        if 'exec "$LOADER"' not in original or 'SLIM_NODE_WRAPPER=' not in original:
            raise RuntimeError("官方 Slim Node 启动器格式已改变：" + name)
        changed.append({"path": path.relative_to(bundle).as_posix(), "upstream_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        path.write_text('''#!/bin/sh
set -eu
case "$0" in
    /*) ANP_PUBLIC_NODE=$0 ;;
    *) ANP_PUBLIC_NODE="$PWD/$0" ;;
esac
ANP_NODE_BIN=$(CDPATH= cd -- "${ANP_PUBLIC_NODE%/*}" && pwd -P)
unset LD_AUDIT GLIBC_TUNABLES GCONV_PATH LOCALE_ARCHIVE LOCPATH NSS_MODULE_PATH
export SLIM_NODE_WRAPPER="$ANP_PUBLIC_NODE"
exec "$ANP_NODE_BIN/.node-real" --require "$ANP_NODE_BIN/.node-execpath.cjs" "$@"
''')
        path.chmod(0o755)
    return changed


def replace_runpath_in_place(path, previous, replacement):
    """替换已有 RUNPATH 字符串，不移动 ELF 段或程序头。"""
    old, new = previous.encode() + b"\0", replacement.encode() + b"\0"
    value = path.read_bytes()
    if not previous or len(new) > len(old) or value.count(old) != 1:
        raise RuntimeError("无法安全原位修改 RUNPATH：" + str(path))
    offset = value.index(old)
    with path.open("r+b") as stream:
        stream.seek(offset)
        stream.write(new.ljust(len(old), b"\0"))


def make_runpath_transitive(path):
    """把既有 DT_RUNPATH 改为 DT_RPATH，使 libc 的加载器依赖也沿包内路径解析。"""
    with path.open("r+b") as stream:
        header = stream.read(64)
        if header[:6] != b"\x7fELF\x02\x01":
            raise RuntimeError("要求 64 位小端 ELF")
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        for index in range(count):
            stream.seek(offset + size * index)
            entry = stream.read(size)
            if struct.unpack_from("<I", entry)[0] != 2:
                continue
            start = struct.unpack_from("<Q", entry, 8)[0]
            length = struct.unpack_from("<Q", entry, 32)[0]
            for position in range(start, start + length, 16):
                stream.seek(position)
                tag = struct.unpack("<Q", stream.read(8))[0]
                if tag == 29:
                    stream.seek(position)
                    stream.write(struct.pack("<Q", 15))
                    return
                if tag == 0:
                    break
    raise RuntimeError("ELF 没有既有 DT_RUNPATH")


def normalize_langfuse_native_loader(bundle):
    root = bundle / "platform/langfuse"
    if not root.exists():
        return []
    changed = []
    for path in root.rglob("index.js"):
        package = path.parent / "package.json"
        if path.parent.name not in {"native", "xxhash"} or path.is_symlink() or not package.is_file():
            continue
        if json.loads(package.read_text()).get("name") not in {"@langfuse/native", "@node-rs/xxhash"}:
            continue
        original = path.read_text()
        marker = "const isMusl = () => {\n"
        if original.count(marker) != 1:
            raise RuntimeError("Langfuse NAPI 加载器格式已改变")
        updated = original.replace(marker, marker + "  // Prefer the bundled Node runtime over the host ldd implementation.\n  if (process.report?.getReport?.().header?.glibcVersionRuntime) return false\n", 1)
        path.write_text(updated)
        changed.append({"path": path.relative_to(bundle).as_posix(), "upstream_sha256": hashlib.sha256(original.encode()).hexdigest(), "patched_sha256": hashlib.sha256(updated.encode()).hexdigest()})
    if not changed:
        raise RuntimeError("未找到 Langfuse NAPI 加载器")
    return changed


def prepare(bundle):
    libraries = bundle / "tools/libc"
    libraries.mkdir(parents=True)
    subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-Wl,-z,defs", "-Wl,-soname,libanp_nss.so",
                    "-o", str(libraries / "libanp_nss.so"), str(Path(__file__).parent / "linux/nss.c")], check=True)
    helper = bundle / "tools/elf/patchelf"
    if not helper.is_file():
        raise RuntimeError("缺少锁定的官方 patchelf 工具")
    patcher = str(helper)
    if "Requesting program interpreter" in output("readelf", "-l", helper):
        raise RuntimeError("运行时 ELF 修补工具必须是官方静态版本")
    native_loaders = normalize_langfuse_native_loader(bundle)
    launchers = normalize_slim_node_launchers(bundle)
    pruned = prune_node_prebuilds(bundle)
    paths = [path for path in bundle.rglob("*") if elf(path)]
    for path in paths:
        with path.open("rb") as stream:
            header = stream.read(20)
        if header[4:6] != b"\x02\x01" or int.from_bytes(header[18:20], "little") != 62:
            raise RuntimeError("发现非 Linux x86_64 ELF：" + str(path.relative_to(bundle)))
    python = (bundle / "tools/python/bin/python3").resolve().relative_to(bundle).as_posix() + ".elf"
    provided = {}
    for path in paths:
        provided.setdefault(path.name, []).append(path)
    system = {}
    for line in output("/sbin/ldconfig", "-p").splitlines():
        match = re.match(r"\s*(\S+)\s+.*=>\s+(\S+)", line)
        if match:
            system[match[1]] = Path(match[2])
    system["ld-linux-x86-64.so.2"] = Path("/lib64/ld-linux-x86-64.so.2")
    receipts = []
    queue = list(paths)
    copied = set()
    while queue:
        path = queue.pop()
        dynamic = output("readelf", "-d", path)
        for name in re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic):
            basename = Path(name).name
            if name != basename:
                subprocess.run([patcher, "--replace-needed", name, basename, str(path)], check=True)
            # 通用 SONAME 使用同一发行版的闭包，避免混用不同 glibc 私有 ABI。
            source = system.get(basename) or next(iter(provided.get(basename, [])), None)
            if not source or not source.exists():
                raise RuntimeError(f"缺少 ELF 依赖：{path.relative_to(bundle)} -> {name}")
            if basename not in copied:
                target = libraries / basename
                shutil.copy2(source.resolve(), target)
                copied.add(basename)
                provided[basename] = [target]
                queue.append(target)
                receipts.append({"name": basename, "source": str(source),
                                 "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    loader = libraries / "ld-linux-x86-64.so.2"
    if not loader.exists():
        shutil.copy2(system[loader.name].resolve(), loader)
    # RUNPATH 只作用于直接依赖，每个收集到的库都需要自己的相对搜索路径。
    for library in libraries.iterdir():
        if library.name not in {loader.name, "libc.so.6"} and "(NEEDED)" in output("readelf", "-d", library):
            subprocess.run([patcher, "--set-rpath", "$ORIGIN", str(library)], check=True)
    executables = []
    runpath_updates = []
    for path in paths:
        if path == helper:
            continue
        headers = output("readelf", "-l", path)
        if "Requesting program interpreter" not in headers:
            # 共享库按自身位置找到库闭包；静态 ELF 无须包装。
            if "(NEEDED)" in output("readelf", "-d", path):
                relative = os.path.relpath(libraries, path.parent)
                old = output(patcher, "--print-rpath", path).strip()
                safe = [p for p in old.split(":") if p.startswith("$ORIGIN")]
                subprocess.run([patcher, "--set-rpath", ":".join(["$ORIGIN", "$ORIGIN/" + relative, *safe]), str(path)], check=True)
            continue
        if path.relative_to(bundle).as_posix() == "storage/clickhouse/clickhouse":
            previous = output(patcher, "--print-rpath", path).strip()
            replacement = "$ORIGIN/" + os.path.relpath(libraries, path.parent)
            replace_runpath_in_place(path, previous, replacement)
            make_runpath_transitive(path)
            runpath_updates.append({"path": path.relative_to(bundle).as_posix(), "previous": previous, "replacement": replacement, "transitive": True})
        original = path.with_name(path.name + ".elf")
        path.replace(original)
        relative = original.relative_to(bundle).as_posix()
        root = os.path.relpath(bundle, path.parent)
        script = f'''#!/bin/sh
set -eu
ANP_SCRIPT=$0
while [ -L "$ANP_SCRIPT" ]; do
    ANP_LINK=$(readlink "$ANP_SCRIPT")
    case "$ANP_LINK" in
        /*) ANP_SCRIPT=$ANP_LINK ;;
        *) ANP_SCRIPT=${{ANP_SCRIPT%/*}}/$ANP_LINK ;;
    esac
done
ANP_ROOT=$(CDPATH= cd -- "${{ANP_SCRIPT%/*}}/{root}" && pwd)
unset LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH
export PYTHONHOME="$ANP_ROOT/tools/python" PYTHONNOUSERSITE=1
exec "$ANP_ROOT/tools/libc/ld-linux-x86-64.so.2" --library-path "$ANP_ROOT/tools/libc" "$ANP_ROOT/{python}" -S -B "$ANP_ROOT/runtime/elf_exec.py" "{relative}" "$@"
'''
        path.write_text(script)
        path.chmod(0o755)
        executables.append(str(path.relative_to(bundle)))
    license_dir = bundle / "manifest/linux-os-licenses"
    license_dir.mkdir()
    packages = {}
    for source in [entry["source"] for entry in receipts]:
        result = subprocess.run(["dpkg-query", "-S", source], capture_output=True, text=True)
        if result.returncode:
            continue
        for line in result.stdout.splitlines():
            package = line.split(": ", 1)[0]
            if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?", package):
                continue
            packages[package] = output("dpkg-query", "-W", "-f=${Version}", package).strip()
            copyright = Path("/usr/share/doc") / package.split(":")[0] / "copyright"
            if copyright.exists():
                shutil.copy2(copyright, license_dir / (package.replace(":", "-") + "-copyright"))
    # /usr/share/common-licenses 中的正文是 Debian copyright 常见引用目标。
    shutil.copytree("/usr/share/common-licenses", license_dir / "common-licenses")
    (bundle / "manifest/elf-audit.json").write_text(json.dumps({"elf_files": len(paths), "pruned_optional_prebuilds": pruned, "normalized_slim_node_launchers": launchers, "runpath_updates": runpath_updates, "langfuse_native_loaders": native_loaders, "wrapped": executables, "patchelf_version": output(helper, "--version").strip(),
        "bundled_libraries": receipts, "debian_packages": packages, "host_library_path_required": False}, indent=2) + "\n")
    # 运行时缓存需设置绝对 RPATH，防止全局 LD_LIBRARY_PATH 污染旧 glibc 宿主 shell。
    # 保留来自锁定官方发行包的静态 patchelf，原始 ELF 始终不变。
