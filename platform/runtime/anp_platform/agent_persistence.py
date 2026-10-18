"""修正固定版本 inmem 运行时的序列化视图覆盖落盘对象问题。

不复制上游实现、不重放或补写历史状态。根保存器仍使用上游原有持久化入口；
只让带不同解码器的只读视图复用根字典，避免重新注册同名磁盘文件。
"""
from importlib.metadata import version


def install():
    from langgraph_runtime_inmem import checkpoint
    current=checkpoint.InMemorySaver
    if getattr(current,'_anp_shared_persistence_view',False):return
    if (version('langgraph-api'),version('langgraph-runtime-inmem'))!=('0.16.1','0.36.1'):
        raise RuntimeError('Agent 持久化适配需要重新核对上游版本，禁止静默套用旧补丁')

    class SharedViewSaver(current):
        _anp_shared_persistence_view=True

        def __init__(self,*,serde=None,__persistence_hook__=None):
            owner=checkpoint.MEMORY
            self._anp_is_view=owner is not None and serde is not None
            if not self._anp_is_view:
                super().__init__(serde=serde,__persistence_hook__=__persistence_hook__)
                return
            # 普通内存字典不会注册、读取、关闭或写入根保存器的三个文件。
            checkpoint.InMemorySaverBase.__init__(self,serde=serde)
            self.filename=owner.filename
            self.latest_iter=None
            self.storage=owner.storage
            self.writes=owner.writes
            self.blobs=owner.blobs

        def __exit__(self,*args):
            if not self._anp_is_view:return super().__exit__(*args)

        async def __aexit__(self,*args):
            if not self._anp_is_view:return await super().__aexit__(*args)

    checkpoint.InMemorySaver=SharedViewSaver
