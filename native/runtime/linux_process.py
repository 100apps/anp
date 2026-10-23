"""Linux 生命周期只使用 /proc；无需安装 ps、lsof 或其他宿主工具。"""
import os
from pathlib import Path


def identity(pid):
    try:
        folder = Path("/proc") / str(pid)
        stat = (folder / "stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return ""
        command = (folder / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        return f"{int(stat[19]):024d} " + command
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ""


def listener_pids(port):
    sockets = set()
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(filename).read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for line in lines:
            parts = line.split()
            if parts[3] == "0A" and int(parts[1].rsplit(":", 1)[1], 16) == port:
                sockets.add("socket:[" + parts[9] + "]")
    result = set()
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            if any(os.readlink(fd) in sockets for fd in (folder / "fd").iterdir()):
                result.add(int(folder.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return result


def node_owned(root, pid):
    try:
        folder = Path("/proc") / str(pid)
        executable = (folder / "exe").resolve()
        allowed = {(root / "tools/node/bin/node").resolve(), (root / "var/cache/elf/tools/node/bin/node.elf").resolve()}
        return executable in allowed and (folder / "cwd").resolve() in {
            (root / "platform/langfuse/web").resolve(), (root / "platform/langfuse/web/web").resolve()}
    except (FileNotFoundError, PermissionError):
        return False


def label(pid):
    try:
        return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except OSError:
        return "未知进程"
