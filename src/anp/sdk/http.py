"""复用证书解析结果；HTTP 连接、Cookie 与身份仍归每个独立客户端所有。"""
from functools import lru_cache
import ssl
import certifi
import httpx


@lru_cache(maxsize=1)
def trust_context():
    # 与 HTTPX trust_env=False 的默认根证书一致，不关闭证书或主机名校验。
    return ssl.create_default_context(cafile=certifi.where())


def async_client(**kwargs):
    kwargs.setdefault('verify',trust_context())
    kwargs.setdefault('trust_env',False)
    return httpx.AsyncClient(**kwargs)
