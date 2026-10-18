"""阻塞文件操作必须真正完成后才传播取消，避免后台线程仍运行却释放设备。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from hashlib import sha256
from pathlib import Path

_workers=ThreadPoolExecutor(max_workers=4,thread_name_prefix='deep-dub-file')


async def await_blocking(function,*args):
    # 独立的 concurrent Future 不会被 asyncio 的 cancel-all-tasks 提前标为完成。
    future=_workers.submit(copy_context().run,function,*args)
    bridge=asyncio.wrap_future(future)
    bridge.add_done_callback(lambda item:None if item.cancelled() else item.exception())
    try:return await asyncio.shield(bridge)
    except asyncio.CancelledError:
        while not future.done():
            try:await asyncio.shield(bridge)
            except asyncio.CancelledError:pass
            except Exception:break
        if future.done() and not future.cancelled():
            # 取出终态异常，取消保持为上层的主要结果。
            future.exception()
        raise


def file_identity(path):
    digest=sha256();size=0
    with Path(path).open('rb') as source:
        while block:=source.read(1024*1024):
            size+=len(block);digest.update(block)
    return {'sha256':digest.hexdigest(),'bytes':size}
