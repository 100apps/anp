"""WSL 到 Windows 回环服务的双向流桥；不修改全局网络或开放宿主网卡。"""
import asyncio


class Bridges:
    def __init__(self):self.servers=[];self.clients=set()

    async def start(self,specifications):
        for spec in specifications:
            async def connection(reader,writer,spec=spec):
                task=asyncio.current_task();self.clients.add(task)
                process=None;copies=[]
                try:
                    process=await asyncio.create_subprocess_exec(*spec['command'],stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                    async def forward(source,destination):
                        while data:=await source.read(65536):
                            destination.write(data);await destination.drain()
                    copies=[asyncio.create_task(forward(reader,process.stdin)),asyncio.create_task(forward(process.stdout,writer))]
                    await asyncio.wait(copies,return_when=asyncio.FIRST_COMPLETED)
                except (OSError,ConnectionError):pass
                finally:
                    for copy in copies:copy.cancel()
                    await asyncio.gather(*copies,return_exceptions=True)
                    if process:
                        process.stdin.close()
                        try:await asyncio.wait_for(process.wait(),5)
                        except asyncio.TimeoutError:process.kill();await process.wait()
                    writer.close()
                    self.clients.discard(task)
            self.servers.append(await asyncio.start_server(connection,'127.0.0.1',spec['port']))

    async def close(self):
        for server in self.servers:server.close()
        await asyncio.gather(*(server.wait_closed() for server in self.servers))
        for task in list(self.clients):task.cancel()
        await asyncio.gather(*self.clients,return_exceptions=True)
