"""初始化同一 ClickHouse 实例中的 Lens 独立数据库和最小范围账号。"""
import os
import base64
from urllib.request import Request,urlopen


def sql(statement):
    credential=base64.b64encode(('anp:'+os.environ['CH_PASSWORD']).encode()).decode()
    request=Request('http://clickhouse:8123/',data=statement.encode(),
        headers={'Authorization':'Basic '+credential})
    with urlopen(request,timeout=30) as response:
        response.read()


def literal(value):
    return "'"+value.replace('\\','\\\\').replace("'","\\'")+"'"


sql('CREATE DATABASE IF NOT EXISTS litellm')
for user,key in [('litellm_writer','LITELLM_CH_WRITE_PASSWORD'),('litellm_reader','LITELLM_CH_READ_PASSWORD')]:
    sql(f'CREATE USER IF NOT EXISTS {user} IDENTIFIED WITH sha256_password BY '+literal(os.environ[key]))
sql('GRANT ALL ON litellm.* TO litellm_writer')
sql('GRANT SELECT ON litellm.* TO litellm_reader')
# Rust 客户端自行设置 readonly=1；账号权限仅有 SELECT，禁止 INSERT/DDL。
sql('ALTER USER litellm_reader SETTINGS readonly=0')
print('Lens ClickHouse 数据库、写入账号和只读账号已就绪')
