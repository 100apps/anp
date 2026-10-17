"""供 WSL 调用的 Windows 回环 TCP 桥；标准输出只承载二进制数据。"""
import os
import socket
import sys
import threading


def main():
    port=int(sys.argv[1])
    if not 1<=port<=65535:raise ValueError('端口无效')
    if sys.platform=='win32':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(),os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(),os.O_BINARY)
    connection=socket.create_connection(('127.0.0.1',port),timeout=5)
    connection.settimeout(None)
    def receive():
        try:
            while data:=connection.recv(65536):
                sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
        except (OSError,BrokenPipeError):pass
        finally:
            # 服务端已关闭时立即关闭标准输出，避免等待客户端输入形成半关闭死锁。
            os._exit(0)
    down=threading.Thread(target=receive,daemon=True);down.start()
    try:
        while data:=os.read(sys.stdin.fileno(),65536):connection.sendall(data)
    except (OSError,BrokenPipeError):pass
    finally:
        try:connection.shutdown(socket.SHUT_WR)
        except OSError:pass
        down.join(timeout=3);connection.close()


if __name__=='__main__':main()
