"""已完成听音证据的只读回归；不重新推理或改写首次回执。"""
import asyncio,json,os
from hashlib import sha256
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.auditory_review import read_latest

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_AUDITORY_READ_LIVE')!='1',reason='需本轮已完成的独立听音证据')


def test_frozen_auditory_evidence_read_keeps_run_and_starts_no_model(identities,monkeypatch):
    saved=json.loads((ROOT/'.local/deep-dub-auditory-live.json').read_bytes())
    project,source,run=saved['project_id'],saved['source_id'],saved['run_id']
    monkeypatch.setenv('ANP_PROFILE','auditory-read-'+uuid4().hex)
    journal=ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json';digest=sha256(journal.read_bytes()).hexdigest()
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=await actor.objects.select('deep_dub_runs',id='eq.'+run)
        assert await read_latest(actor,project,source)==saved['readback']
        async with async_client(base_url='http://127.0.0.1:8181',timeout=90,trust_env=False) as client:
            response=await client.get(f'/api/apps/deep_dub/projects/{project}/sources/{source}/auditory-review',headers={
                'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id})
            assert response.status_code==200 and response.json()==saved['readback']
        assert await actor.objects.select('deep_dub_runs',id='eq.'+run)==before
        assert before[0]['status']=='waiting_review' and before[0]['technical_verified_at'] is None
    try:
        asyncio.run(scenario());assert sha256(journal.read_bytes()).hexdigest()==digest
    finally:session_path().unlink(missing_ok=True)
