"""本轮真实 owner 失联的管理员收尾；不取消原运行、不补写单调耗时。"""
import asyncio,json,os,site,subprocess,sys
from hashlib import sha256
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret
from anp.sdk.http import async_client
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_LOST_ATTEMPT_LIVE')!='1',reason='需已精确核对停止并完成管理员租约核对的本轮失联尝试')


def test_actual_owner_loss_closed_once_same_cli_http_contract_and_no_run_cancel(identities,monkeypatch):
    prior=json.loads((ROOT/'.local/deep-dub-omni-orphan-reconcile.json').read_bytes())
    attempt=prior['attempt'];lease=prior['lease'];attempt_id=attempt['id']
    journal=ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json';digest=sha256(journal.read_bytes()).hexdigest()
    monkeypatch.setenv('ANP_PROFILE','lost-owner-live-'+uuid4().hex)
    async def scenario():
        await login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'));actor=await current_actor()
        stage=(await actor.objects.select('deep_dub_stages',id='eq.'+attempt['stage_id']))[0]
        before=(await actor.objects.select('deep_dub_runs',id='eq.'+stage['run_id']))[0]
        body={'lease_ids':[lease['id']]};path=f'/api/apps/deep_dub/attempts/{attempt_id}/close-lost'
        async with async_client(base_url='http://127.0.0.1:8181',timeout=60,trust_env=False) as client:
            assert (await client.post(path,json=body)).status_code==401
            viewer=identities['viewer']
            assert (await client.post(path,json=body,headers={'Authorization':'Bearer '+viewer['token'],'X-Workspace-ID':viewer['workspace_id']})).status_code==403
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
            response=await client.post(path,json=body,headers=headers);assert response.status_code==200,response.text[:500]
            closed=response.json()
            assert (await client.post(path,json=body,headers=headers)).json()==closed
        assert closed['status']=='failed' and closed['error']['code']=='WORKER_LOST'
        assert closed['monotonic_seconds'] is None and closed['outputs']==[] and closed['finished_at']
        assert all(closed[key]==attempt[key] for key in ('queued_at','started_at','attempt_no','created_by','request_key'))
        assert (await actor.objects.select('deep_dub_runs',id='eq.'+before['id']))[0]==before
        assert before['status']=='waiting_review' and before['technical_verified_at'] is None
        assert not await actor.objects.select('deep_dub_artifacts',stage_attempt_id='eq.'+attempt_id)
        assert not await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempt_id)
        after_stage=(await actor.objects.select('deep_dub_stages',id='eq.'+stage['id']))[0]
        if stage['current_attempt_id']==attempt_id:assert after_stage['status']=='failed'
        else:assert after_stage==stage  # 后续重试的状态不能被旧回执重放改写。
        await flush(actor)
        return closed,before
    try:
        closed,before=asyncio.run(scenario())
        environment={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            response=subprocess.run([sys._base_executable,'-m','anp.cli.entry',*options,'deep_dub','close-lost-attempt',attempt_id,'--lease-id',lease['id']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60,env=environment)
            assert response.returncode==0,response.stdout+response.stderr
            assert json.loads(response.stdout)==closed
        assert sha256(journal.read_bytes()).hexdigest()==digest
        (ROOT/'.local/deep-dub-omni-owner-recovery.json').write_bytes(json.dumps({'attempt':closed,'run_before_and_after':before,
            'lease':lease,'two_cli_and_http_same_receipt':True,'no_inference_or_run_cancel':True,'unknown_monotonic_not_fabricated':True},ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
