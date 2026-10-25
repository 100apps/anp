"""macOS 进程身份：libproc 可在离线沙箱内使用，避免执行 setuid ps。"""
import ctypes
import os
import subprocess


class BSDInfo(ctypes.Structure):
    # Darwin SDK sys/proc_info.h 的 proc_bsdinfo；PROC_PIDTBSDINFO = 3。
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved"
    )] + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid")
    ] + [("nice", ctypes.c_int32), ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]


def identity(pid):
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int)
    library.proc_pidinfo.restype = ctypes.c_int
    library.proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
    library.proc_pidpath.restype = ctypes.c_int
    info = BSDInfo()
    if library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        return ""
    if info.pid != pid or info.status == 5:  # SZOMB 不再作为存活服务。
        return ""
    path = ctypes.create_string_buffer(4096)
    if library.proc_pidpath(pid, path, len(path)) <= 0:
        return ""
    # 固定 24 字节的出生标记，保留 owned() 的 PID 重用检查。
    executable = os.fsdecode(path.value)
    # 启动包装器在 exec 前仍是系统 shell；只补其真实 cwd，不保存可能含敏感值的 argv。
    if executable in ("/bin/sh", "/bin/bash", "/bin/zsh"):
        result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                capture_output=True, text=True)
        directory = next((line[1:] for line in result.stdout.splitlines() if line.startswith("n/")), "")
        executable += " cwd=" + directory
    return f"{info.start_seconds:016d}:{info.start_microseconds:06d} " + executable
