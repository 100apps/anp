"""Linux ELF 搬移缓存：内核使用本包加载器，原始制品保持不可变。"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def set_interpreter(path, loader):
    """只更新既有 PT_INTERP 的文件位置，保持所有 PT_LOAD 和代码地址不变。"""
    value = os.fsencode(loader) + b"\0"
    if len(value) > 4096:
        raise RuntimeError("加载器路径过长")
    with path.open("r+b") as stream:
        header = stream.read(64)
        if header[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", header, 18)[0] != 62:
            raise RuntimeError("只支持 Linux x86_64 ELF")
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        for index in range(count):
            position = offset + size * index
            stream.seek(position)
            entry = bytearray(stream.read(size))
            if struct.unpack_from("<I", entry)[0] != 3:
                continue
            stream.seek(0, 2)
            location = stream.tell()
            stream.write(value)
            struct.pack_into("<Q", entry, 8, location)
            struct.pack_into("<Q", entry, 32, len(value))
            struct.pack_into("<Q", entry, 40, len(value))
            stream.seek(position)
            stream.write(entry)
            return
    raise RuntimeError("ELF 没有 PT_INTERP")


def execute(relative, arguments):
    source = (ROOT / relative).resolve()
    if not source.is_relative_to(ROOT) or not source.is_file():
        raise RuntimeError("无效的包内 ELF 路径")
    cache = ROOT / "var/cache/elf"
    cache.mkdir(parents=True, exist_ok=True)
    loader = ROOT / "tools/libc/ld-linux-x86-64.so.2"
    libraries = ROOT / "tools/libc"
    slim_node = source.name == ".node-real.elf" and source.relative_to(ROOT).parts[0] == "platform"
    with (cache / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original = ROOT
        destination = cache
        # 镜像祖先目录布局；Python/Node/PG 根据自身位置找到相邻 lib/share。
        for part in source.relative_to(ROOT).parts[:-1]:
            for child in original.iterdir():
                target = destination / child.name
                if child.name == "var" and original == ROOT:
                    continue
                if child.name != part:
                    if target.is_symlink():
                        if os.readlink(target) != str(child):
                            target.unlink()
                            target.symlink_to(child, target_is_directory=child.is_dir())
                    elif not target.exists():
                        target.symlink_to(child, target_is_directory=child.is_dir())
            original = original / part
            destination = destination / part
            if destination.is_symlink():
                destination.unlink()
            destination.mkdir(exist_ok=True)
        target = destination / source.name
        for child in original.iterdir():
            sibling = destination / child.name
            if child == source:
                continue
            if sibling.is_symlink() and os.readlink(sibling) != str(child):
                sibling.unlink()
            if not sibling.exists() and not sibling.is_symlink():
                sibling.symlink_to(child, target_is_directory=child.is_dir())
        receipt = target.with_name(target.name + ".json")
        expected = {"root": str(ROOT), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "recipe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        if target.is_symlink():
            target.unlink()
        if not target.exists() or not receipt.exists() or json.loads(receipt.read_text()) != expected:
            temporary = target.with_name(target.name + ".tmp")
            shutil.copy2(source, temporary)
            patcher = ROOT / "tools/elf/patchelf"
            if not patcher.is_file():
                raise RuntimeError("缺少包内静态 patchelf，请升级完整发行包")
            # 先设置传递性 RPATH，再以原位程序头修改 INTERP；不向宿主子进程泄漏 glibc。
            subprocess.run([str(patcher), "--force-rpath", "--set-rpath", str(libraries), str(temporary)],
                           check=True, capture_output=True)
            nss = libraries / "libanp_nss.so"
            if nss.is_file() and not slim_node:
                # DT_NEEDED 只影响本包 ELF，不像 LD_PRELOAD 一样污染宿主 shell。
                subprocess.run([str(patcher), "--add-needed", str(nss), str(temporary)], check=True, capture_output=True)
            set_interpreter(temporary, loader)
            temporary.replace(target)
            receipt.write_text(json.dumps(expected))
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH", "LD_PRELOAD"):
        environment.pop(key, None)
    if slim_node and (libraries / "libanp_nss.so").is_file():
        # 此官方 Node 的 ELF 布局不支持新增 NEEDED；构造器在 main 前清除临时预载。
        # glibc 以空格/冒号分割 LD_PRELOAD，绝对解压路径可能含空格。
        # 只传 SONAME，依靠上面已写入的包内 RPATH 查找，路径不会被拆开。
        environment["LD_PRELOAD"] = "libanp_nss.so"
    # 自派生 ELF 通过自身 RPATH 找到库；/bin/sh 等宿主工具使用宿主库。
    os.execve(target, [str(target), *arguments], environment)


if __name__ == "__main__":
    execute(sys.argv[1], sys.argv[2:])
