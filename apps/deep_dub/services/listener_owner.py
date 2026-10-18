"""每次读取 Windows 内核 TCP 监听归属；不缓存端口、PID或授权结果。

结构和表类别遵循 Windows IP Helper GetExtendedTcpTable：
https://learn.microsoft.com/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable
"""
import ctypes
import socket
import sys


DWORD = ctypes.c_uint32


class _Row4(ctypes.Structure):
    _fields_ = [(name, DWORD) for name in (
        'state', 'local_address', 'local_port', 'remote_address', 'remote_port', 'pid')]


class _Row6(ctypes.Structure):
    _fields_ = [('local_address', ctypes.c_ubyte * 16), ('local_scope', DWORD),
        ('local_port', DWORD), ('remote_address', ctypes.c_ubyte * 16),
        ('remote_scope', DWORD), ('remote_port', DWORD), ('state', DWORD), ('pid', DWORD)]


def _owners(api, family, row_type, port):
    class Table(ctypes.Structure):
        _fields_ = [('count', DWORD), ('rows', row_type * 1)]

    size = DWORD()
    buffer = None
    for _ in range(5):
        result = api(buffer, ctypes.byref(size), False, family, 3, 0)
        if result == 122:  # ERROR_INSUFFICIENT_BUFFER；表可能在两次调用间增长。
            if not 4 <= size.value <= 64 * 1024**2:
                raise OSError('TCP监听表大小无效')
            buffer = ctypes.create_string_buffer(size.value)
            continue
        if result:
            raise OSError(result, '不能读取TCP监听归属')
        if buffer is None or size.value < Table.rows.offset:
            raise OSError('TCP监听表头不完整')
        count = DWORD.from_buffer(buffer).value
        end = Table.rows.offset + count * ctypes.sizeof(row_type)
        if end > size.value or end > ctypes.sizeof(buffer):
            raise OSError('TCP监听表长度不一致')
        owners = set()
        for index in range(count):
            row = row_type.from_buffer(buffer, Table.rows.offset + index * ctypes.sizeof(row_type))
            if row.state == 2 and socket.ntohs(row.local_port & 0xffff) == port:
                owners.add(row.pid)
        return owners
    raise OSError('TCP监听表持续增长，无法核实归属')


def listener_owners(port):
    if sys.platform != 'win32':
        raise OSError('当前监听归属检查仅支持Windows')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('TCP端口无效')
    # 固定系统DLL，不按工作目录或PATH搜索第三方DLL。
    dll = ctypes.WinDLL('iphlpapi.dll', winmode=0x00000800)
    api = dll.GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWORD), ctypes.c_int,
        DWORD, ctypes.c_int, DWORD]
    api.restype = DWORD
    return _owners(api, socket.AF_INET, _Row4, port) | _owners(api, socket.AF_INET6, _Row6, port)
