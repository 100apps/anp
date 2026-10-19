"""既有完整原音证据的原生词候选定位；不启动模型、不裁决正文、不解除待审。"""
import asyncio,json,os,site,subprocess,sys
from pathlib import Path
from hashlib import sha256
from statistics import median
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.source_review import read

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_NATIVE_REVIEW_LIVE')!='1',reason='需已保存完整Parakeet/Nemotron证据与冻结API')


def test_native_word_queue_permissions_snapshot_two_cli_and_no_writes(identities,monkeypatch):
    monkeypatch.setenv('ANP_PROFILE','native-review-'+uuid4().hex)
    source_record=json.loads((ROOT/'.local/deep-dub-parakeet-live.json').read_bytes())
    old=json.loads((ROOT/'.local/deep-dub-cross-review-live.json').read_bytes())
    project,source=source_record['project_id'],source_record['source_id']
    journal=ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json';before_journal=sha256(journal.read_bytes()).hexdigest()
    async def snapshot(actor):
        return {table:await actor.objects.select(table,project_id='eq.'+project,order='id.asc')
            for table in ('deep_dub_stage_attempts','deep_dub_audits','deep_dub_segment_sets','deep_dub_translations','deep_dub_runs')}
    async def run():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=await snapshot(actor)
        base=await read(actor,project,source)
        nemo=await read(actor,project,source,include_secondary=True)
        assert base['packet_sha256']==old['original_packet_sha256'] and base['total']==213
        assert nemo['packet_sha256']==old['packet_sha256'] and nemo['total']==460
        expected=await read(actor,project,source,include_secondary=True,include_parakeet=True)
        assert expected['total']==600 and expected['counts']['parakeet_difference']==140
        assert expected['binding']['secondary_evidence_artifact_id']==nemo['binding']['secondary_evidence_artifact_id']
        assert expected['binding']['parakeet_evidence_artifact_id']==source_record['readback']['evidence_artifact_id']
        async with async_client(base_url='http://127.0.0.1:8181',timeout=120,trust_env=False) as client:
            path=f'/api/apps/deep_dub/projects/{project}/sources/{source}/review-queue'
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
            params={'include_secondary':'true','include_parakeet':'true'}
            response=await client.get(path,params=params,headers=headers);assert response.status_code==200 and response.json()==expected
            rows=[]
            for offset in range(0,600,100):
                reply=await client.get(path,params={**params,'offset':offset,'limit':100,'expected_hash':expected['packet_sha256']},headers=headers)
                assert reply.status_code==200;rows+=reply.json()['items']
            assert len({r['id'] for r in rows})==600
            native=[r for r in rows if r['kind']=='parakeet_difference']
            assert len(native)==140 and all(r['review_status']=='not_measured' for r in rows)
            assert all(0<=r['audition']['start_sample']<r['audition']['end_sample']<=41380932 for r in native)
            precise=[r for r in native if r['audition']['precision']=='native-word-candidate'];assert precise
            spans=[r['audition']['end_ms']-r['audition']['start_ms'] for r in precise]
            coarse=[r['fallback_audition']['end_ms']-r['fallback_audition']['start_ms'] for r in precise]
            assert median(spans)<median(coarse)
            assert (await client.get(path,params=params)).status_code==401
            outside=identities['outside']
            assert (await client.get(path,params=params,headers={'Authorization':'Bearer '+outside['token'],'X-Workspace-ID':outside['workspace_id']})).status_code==404
            assert (await client.get(path,params={'kind':'parakeet_difference'},headers=headers)).status_code==422
            assert (await client.get(path,params={**params,'expected_hash':nemo['packet_sha256']},headers=headers)).status_code==409
        env={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            args=[sys._base_executable,'-m','anp.cli.entry',*options,'deep_dub','projects','source-review-queue',project,source,'--include-secondary','--include-parakeet']
            result=await asyncio.to_thread(subprocess.run,args,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert result.returncode==0 and json.loads(result.stdout)==expected
        assert await snapshot(actor)==before and sha256(journal.read_bytes()).hexdigest()==before_journal
        return {'project_id':project,'source_id':source,'run_id':source_record['run_id'],'packet':expected,'all_items':rows,
            'native_word_localizations':len(precise),'native_fallbacks':len(native)-len(precise),
            'median_native_window_ms':median(spans),'median_coarse_window_ms':median(coarse),
            'old_base_and_nemotron_hashes_preserved':True,'two_cli_verified':True,'permissions_verified':True,
            'no_stage_audit_version_run_or_gpu_journal_changes':True,'gpu_journal_sha256':before_journal}
    try:
        record=asyncio.run(run())
        (ROOT/'.local/deep-dub-native-review-live.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
