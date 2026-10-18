"""已有公开整片的新ASR验收；不是新下载/生产，不计算整片RTF。"""
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import probe_media
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.source_recognition import recognize,read_latest,record_scores
from apps.deep_dub.services.blocking import await_blocking,file_identity
from tests.test_deep_dub_contracts_live import PROFILE,pytestmark as base_mark
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

pytestmark=[base_mark,pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_ASR_LIVE')!='1',reason='需显式启用整轨真实模型识别')]
REFERENCE=Path('D:/github/dub-bench-zh/dubbing-studio-an/work/tasks/task-19e0d4a36aeb/task.json')


def test_whole_existing_source_asr_one_model_process_and_three_entry_replay(identities,waveform_api,tmp_path,monkeypatch):
    if not REFERENCE.is_file():pytest.skip('需本机已有公开原片，仅供识别验收')
    source_file=Path(json.loads(REFERENCE.read_bytes())['source']['path'])
    monkeypatch.setenv('ANP_PROFILE','source-asr-'+uuid4().hex);monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=await projects.create(actor,{'name':'[整轨来源ASR验收] Yale既有原片','source_url':'https://www.youtube.com/watch?v=QXXp1bHd6gI'})
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':profile['id'],
            'flow_revision':'source-asr-acceptance-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id']);success=False
        try:
            prepared_at=time.monotonic();identity=await await_blocking(file_identity,source_file)
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'provided_whole_original','input_hash':identity['sha256']}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started')
            probe=await probe_media(source_file);asset=await artifacts.upload(actor,attempt,source_file,'video/mp4',probe['duration_ms'])
            assert asset['sha256']==identity['sha256'] and asset['bytes']==identity['bytes']
            original=await candidates.source(actor,attempt,{'artifact_id':asset['id'],'duration_ms':probe['duration_ms'],
                'identity_evidence':{'scope':'已有公开整片用于新ASR验收，未新下载，非整片生产或质量通过','source_sha256':asset['sha256']}})
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[asset['id']],
                'elapsed_seconds':time.monotonic()-prepared_at,'operation_stopped':True})
            body={'source_id':original['id'],'language':'English','chunk_seconds':60,'timeout_seconds':3600};key=uuid4().hex
            endpoint=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/recognize-source'
            async with async_client(timeout=3650,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(endpoint,json=body,headers={'Authorization':''})).status_code==401
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                response=await client.post(endpoint,json=body,headers={'Idempotency-Key':key})
                if response.status_code!=200:Path('.local/deep-dub-source-asr-last-failure.json').write_bytes(response.content)
                assert response.status_code==200,'整轨ASR失败；详细诊断留在私有模型日志'
                stage=response.json();assert stage['status']=='succeeded'
                view=waveform_api+f'/api/apps/deep_dub/projects/{project["id"]}/sources/{original["id"]}/recognition'
                visible=await client.get(view,headers={'Authorization':'Bearer '+identities['viewer']['token']});assert visible.status_code==200
                result=visible.json();assert result['coverage']['sample_coverage']==1 and result['coverage']['chunk_count']>1
                assert result['recognition']['session']['requests']==result['coverage']['chunk_count']
                assert result['recognition']['semantic_review']=='not_measured'
                assert (await client.get(view,headers={'Authorization':''})).status_code==401
                assert (await client.get(view,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
            assert stage==await recognize(actor,queued['id'],body,key)
            await record_scores(actor,stage['attempt'])
            scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+stage['attempt']['id']);assert len(scores)==5
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+stage['attempt']['id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped'] is True
            for table in ('deep_dub_audits','deep_dub_segment_sets','deep_dub_translations','deep_dub_timeline_revisions'):
                assert not await actor.objects.select(table,project_id='eq.'+project['id'])
            await login(identities['admin']['email'],identities['admin']['password'])
            def cli(*options):
                completed=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','recognize-source',queued['id'],original['id'],'--request-key',key],
                    cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
                assert completed.returncode==0,'来源识别CLI重放失败';return json.loads(completed.stdout)
            assert stage==await asyncio.to_thread(cli,'--endpoint','http://127.0.0.1:1')
            assert stage==await asyncio.to_thread(cli,'--transport','http','--endpoint',waveform_api)
            await flush(actor)
            record={'project_id':project['id'],'run_id':queued['id'],'source_id':original['id'],'source_artifact_id':asset['id'],
                'source_duration_ms':probe['duration_ms'],'stage_attempt_id':stage['attempt']['id'],'readback':result,
                'three_entry_replay':True,'viewer_read_only':True,'no_segment_or_quality_audit':True,'lease_released':True,
                'score_ids':[s['id'] for s in scores],'actor_user_id':actor.user['id'],'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'purpose':'acceptance','new_download':False,'run_kept_for_browser':os.environ.get('ANP_DEEP_DUB_SOURCE_KEEP')=='1'}
            Path('.local/deep-dub-source-asr-live.json').write_bytes(json.dumps(record,ensure_ascii=False,indent=2).encode())
            success=True
        finally:
            if not (success and os.environ.get('ANP_DEEP_DUB_SOURCE_KEEP')=='1'):
                await execution.cancel(actor,queued['id']);await flush(actor)
    try:asyncio.run(run())
    finally:session_path().unlink(missing_ok=True)
