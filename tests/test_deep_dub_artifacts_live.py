"""真实私有 RustFS 对象经 Supabase Storage 授权；仅验收 JSON，不生产媒体。"""
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

import httpx
import pytest

from anp.auth.identity import actor_from_token
from anp.config import secret, value
from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.services.artifacts import service as artifacts, object_url, headers
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.observations import deliver_one
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.versions import service as versions
from tests.test_deep_dub_contracts_live import context, PROFILE

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_LIVE')!='1',reason='需显式开启真实 V3 对象验收')


def test_private_immutable_object_identity_range_and_expiry(context):
    async def run():
        _,tokens=context
        actor=await actor_from_token(tokens['admin'])
        viewer=await actor_from_token(tokens['viewer'])
        project=(await projects.list(actor))[0]
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'quality_profile_id':profile['id'],
            'purpose':'acceptance','flow_revision':'private-object-contract-v1','expected_version':project['row_version']})
        result=None
        try:
            await execution.start(actor,queued['id'])
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_private_object','input_hash':'b'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started')
            started=time.monotonic()
            content=json.dumps({'acceptance_only':True,'key':str(uuid4()),'内容':'私有对象不可变与摘要验收'},ensure_ascii=False).encode()
            with tempfile.TemporaryDirectory(prefix='anp-artifact-test-') as folder:
                file=Path(folder)/'evidence.json';file.write_bytes(content)
                result=await artifacts.upload(actor,attempt,file,'application/json')
                assert result==await artifacts.upload(actor,attempt,file,'application/json')
                with pytest.raises(BusinessError) as denied:
                    await artifacts.upload(viewer,attempt,file,'application/json')
                assert denied.value.status==403
            url=object_url(result['object_path'])
            authenticated=url.replace('/storage/v1/object/','/storage/v1/object/authenticated/',1)
            async with async_client(timeout=20,trust_env=False) as client:
                fetched=await client.get(authenticated,headers=headers(actor))
                assert fetched.status_code==200 and fetched.content==content
                partial=await client.get(authenticated,headers={**headers(actor),'Range':'bytes=0-15'})
                assert partial.status_code==206 and partial.content==content[:16]
                anonymous=await client.get(authenticated)
                assert anonymous.status_code in (400,401,403)
                cross=await client.get(authenticated.replace(actor.workspace_id,str(uuid4())),headers=headers(actor))
                assert not cross.is_success
                overwrite=await client.put(url,headers={**headers(actor),'Content-Type':'application/json'},content=b'changed')
                assert not overwrite.is_success
                upsert=await client.post(url,headers={**headers(actor),'Content-Type':'application/json','x-upsert':'true'},content=b'changed')
                assert not upsert.is_success
                removed=await client.request('DELETE',value('supabase','url')+'/storage/v1/object/deep-dub',
                    headers=headers(actor),json={'prefixes':[result['object_path'].removeprefix('deep-dub/')]})
                assert not removed.is_success or removed.json()==[]
                assert (await client.get(authenticated,headers=headers(actor))).content==content
                signed=await client.post(url.replace('/storage/v1/object/','/storage/v1/object/sign/',1),
                                         headers=headers(actor),json={'expiresIn':1})
                assert signed.is_success
                signed_path=signed.json()['signedURL']
                signed_url=value('supabase','url')+'/storage/v1'+signed_path
                assert (await client.get(signed_url)).content==content
                await asyncio.sleep(2)
                expired=await client.get(signed_url)
                assert not expired.is_success
            with pytest.raises(BusinessError) as corrupt:
                await artifacts.verify(actor,result['object_path'],'0'*64,len(content))
            assert corrupt.value.code=='ARTIFACT_INTEGRITY_FAILED'
            verified=await artifacts.verify(actor,result['object_path'],sha256(content).hexdigest(),len(content))
            receipt=await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[result['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            assert receipt['committed']
            for _ in range(20):
                delivered=await deliver_one(actor)
                if not delivered['claimed']:break
                assert delivered['sent']
            Path('.local/deep-dub-artifacts-live.json').write_bytes((json.dumps({
                'checked_at_utc':datetime.now(timezone.utc).isoformat(),'purpose':'acceptance',
                'scope':'真实 Supabase Storage/RustFS 的私有 JSON，非生产媒体',
                'run_id':queued['id'],'attempt_id':attempt,'artifact_id':result['id'],
                'sha256':verified['sha256'],'bytes':verified['bytes'],
                'range_206':True,'anonymous_denied':True,'cross_workspace_denied':True,
                'viewer_upload_denied':True,'overwrite_denied':True,'delete_denied':True,
                'signed_url_expiry_verified':True,'bad_hash_rejected':True,
                'replay_same_artifact':True},ensure_ascii=False,indent=2)+'\n').encode())
        finally:
            await execution.cancel(actor,queued['id'])
    asyncio.run(run())
