import json
from anp.sdk.http import async_client
from anp.config import value, secret


async def save_json(token, path, data):
    async with async_client(timeout=30,trust_env=False) as client:
        response=await client.post(value('supabase','url')+'/storage/v1/object/artifacts/'+path,
            headers={'apikey':secret('ANON_KEY'),'Authorization':'Bearer '+token,
                'Content-Type':'application/json','x-upsert':'true'},
            content=json.dumps(data,ensure_ascii=False,sort_keys=True).encode())
        response.raise_for_status()
    return 'artifacts/'+path


async def read_json(token, path):
    async with async_client(timeout=30,trust_env=False) as client:
        response=await client.get(value('supabase','url')+'/storage/v1/object/authenticated/'+path,
            headers={'apikey':secret('ANON_KEY'),'Authorization':'Bearer '+token})
        response.raise_for_status()
        return response.json()
