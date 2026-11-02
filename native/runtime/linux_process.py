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
    return listener_snapshot([port])[port]


def listener_snapshot(ports, *, proc_root=Path('/proc')):
    """Read-only inventory snapshot; process mutations always take a fresh scan."""
    result = {port: set() for port in ports}
    sockets = {}
    for filename in (proc_root / 'net/tcp', proc_root / 'net/tcp6'):
        try:
            lines = filename.read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for line in lines:
            parts = line.split()
            port = int(parts[1].rsplit(":", 1)[1], 16)
            if parts[3] == "0A" and port in result:
                sockets.setdefault("socket:[" + parts[9] + "]", set()).add(port)
    for folder in proc_root.iterdir():
        if not folder.name.isdigit():
            continue
        try:
            for fd in (folder / 'fd').iterdir():
                for port in sockets.get(os.readlink(fd), ()):
                    result[port].add(int(folder.name))
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
