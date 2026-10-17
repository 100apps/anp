"""验证跨进程流桥的双向流式传输、二进制大包和主动关闭后的回收。"""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'platform/runtime'))
from anp_platform.bridges import Bridges


def test_bridge_duplex_binary_stream_and_server_eof():
    # 项目业务测试使用 psycopg 所需的 Windows Selector；流桥本身运行于 Linux。
    # 本地单独选择支持子进程管道的 Proactor，保持业务测试策略不变。
    factory=asyncio.ProactorEventLoop if sys.platform=='win32' else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:runner.run(exercise_bridge())


async def exercise_bridge():
    payload=bytes(range(256))*1024
    async def upstream(reader,writer):
        size=int.from_bytes(await reader.readexactly(4),'big')
        writer.write(b'ACK');await writer.drain()
        content=await reader.readexactly(size)
        for offset in range(0,len(content),8192):
            writer.write(content[offset:offset+8192]);await writer.drain()
        writer.close();await writer.wait_closed()
    server=await asyncio.start_server(upstream,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    bridge=Bridges()
    helper=Path(__file__).resolve().parents[1]/'src/anp/dev/stdio_bridge.py'
    try:
        await bridge.start([{'port':0,'command':[sys.executable,'-E',str(helper),str(port)]}])
        local=bridge.servers[0].sockets[0].getsockname()[1]
        reader,writer=await asyncio.open_connection('127.0.0.1',local)
        writer.write(len(payload).to_bytes(4,'big'));await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(3),10)==b'ACK'
        writer.write(payload);await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(len(payload)),10)==payload
        assert await asyncio.wait_for(reader.read(),10)==b''
        writer.close();await writer.wait_closed()
    finally:
        await bridge.close();server.close();await server.wait_closed()
    assert not bridge.clients
