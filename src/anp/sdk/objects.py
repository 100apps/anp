"""业务数据统一通过 Supabase REST/RPC，保留调用者身份和 RLS。"""
import httpx
from anp.config import value, secret
from anp.errors import BusinessError


class Objects:
    def __init__(self, token, schema='platform'):
        self.token, self.schema = token, schema

    def in_schema(self, schema):
        return Objects(self.token, schema)

    async def request(self, method, resource, **kwargs):
        headers = {'apikey': secret('ANON_KEY'), 'Authorization': 'Bearer ' + self.token,
                   'Accept-Profile': self.schema, 'Content-Profile': self.schema}
        headers.update(kwargs.pop('headers', {}))
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.request(method, value('supabase', 'url') + '/rest/v1/' + resource,
                                            headers=headers, **kwargs)
        if not response.is_success:
            try:
                data = response.json()
            except ValueError:
                data = {}
            code = data.get('code', '')
            status = 403 if code in ('42501', 'P0001') else response.status_code
            if status >= 500:
                raise BusinessError('对象服务暂时不可用', 503, 'UPSTREAM_UNAVAILABLE')
            raise BusinessError(data.get('message', '对象操作失败'), status, code or 'OBJECT_ERROR')
        return response.json() if response.content else None

    async def rpc(self, name, **params):
        return await self.request('POST', 'rpc/' + name, json=params)

    async def select(self, table, **filters):
        return await self.request('GET', table, params=filters)

    async def insert(self, table, data, **filters):
        return await self.request('POST', table, json=data, params=filters,
                                  headers={'Prefer': 'return=representation'})

    async def patch(self, table, data, **filters):
        return await self.request('PATCH', table, json=data, params=filters,
                                  headers={'Prefer': 'return=representation'})

    async def upsert(self, table, data, conflict):
        return await self.request('POST', table, json=data, params={'on_conflict': conflict},
            headers={'Prefer': 'resolution=merge-duplicates,return=representation'})
