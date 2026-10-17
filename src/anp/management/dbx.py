import json
import httpx
from anp import ROOT
from anp.config import value,secret


async def configure_dbx(verify=True):
    configs=json.loads((ROOT/'.local/generated/dbx-connections.private.json').read_text())
    async with httpx.AsyncClient(base_url=value('dbx','url'),timeout=40,trust_env=False) as client:
        response=await client.post('/api/auth/login',json={'password':secret('DBX_PASSWORD')})
        response.raise_for_status()
        # 配置按 ID upsert，不移除用户自行添加的连接。
        response=await client.post('/api/connection/save',json={'configs':configs,'removedIds':[]})
        response.raise_for_status()
        results=[]
        if verify:
            listed=await client.get('/api/connection/list')
            listed.raise_for_status()
            ids={c['id'] for c in configs}
            for config in (c for c in listed.json() if c['id'] in ids):
                # 验证 Web 实际拿到的保存凭据引用，而非仅验证初始化明文密码。
                config['driver_profile']=config.get('driver_profile') or config['db_type']
                config['show_database_links']=config.get('show_database_links') is not False
                response=await client.post('/api/connection/test',json={'config':config})
                response.raise_for_status()
                response=await client.post('/api/connection/connect',json={'config':config})
                response.raise_for_status()
                results.append({'id':config['id'],'status':'ok'})
        return {'connections':results or [c['id'] for c in configs]}
