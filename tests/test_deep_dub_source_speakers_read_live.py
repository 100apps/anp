"""已完成角色证据的只读回归；绝不重新运行模型或改写原验收回执。"""
import asyncio,json,os
from hashlib import sha256
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.source_speakers import read_latest

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_SPEAKERS_READ_LIVE')!='1',reason='需本轮已完成的整轨角色证据')


def test_frozen_speaker_read_matches_original_and_no_model_is_started(identities,monkeypatch):
    saved=json.loads((ROOT/'.local/deep-dub-source-speakers-live.json').read_bytes())
    project,source,run=saved['project_id'],saved['source_id'],saved['run_id']
    monkeypatch.setenv('ANP_PROFILE','speaker-read-'+uuid4().hex)
    journal=ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json';digest=sha256(journal.read_bytes()).hexdigest()
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=await actor.objects.select('deep_dub_runs',id='eq.'+run)
        assert await read_latest(actor,project,source)==saved['readback']
        async with async_client(base_url='http://127.0.0.1:8181',timeout=90,trust_env=False) as client:
            reply=await client.get(f'/api/apps/deep_dub/projects/{project}/sources/{source}/speaker-timing',headers={
                'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id})
            assert reply.status_code==200 and reply.json()==saved['readback']
        assert await actor.objects.select('deep_dub_runs',id='eq.'+run)==before
        assert before[0]['status']=='waiting_review' and before[0]['technical_verified_at'] is None
    try:
        asyncio.run(scenario());assert sha256(journal.read_bytes()).hexdigest()==digest
    finally:session_path().unlink(missing_ok=True)
