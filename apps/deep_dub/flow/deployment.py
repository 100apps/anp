"""平台挂载的解码子图；不携带业务主机用户会话，也不代表完整生产图。"""
from anp.config import value,secret
from apps.deep_dub.flow.decode import build_decode_graph

graph=build_decode_graph(value('workbench','worker_url'),engine_token=secret('WORKER_TOKEN'))
