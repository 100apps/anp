"""真实数据库待审状态契约，2秒合成媒体；不是来源或内容质量证据。"""
import asyncio,json,subprocess,time
from uuid import uuid4
import pytest
from anp.auth.identity import actor_from_token
from anp.errors import BusinessError
from apps.deep_dub.nodes.media import executable
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from tests.test_deep_dub_contracts_live import context,pytestmark
from tests.test_deep_dub_runtime_live import setup


def test_wait_gate_jsonb_own_frozen_current_attempt_and_cancel(context,tmp_path):
    _,tokens=context
    video=tmp_path/'fixture.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=gray:s=64x64:r=10:d=2','-c:v','mpeg4',str(video)],
        check=True,capture_output=True,timeout=20)
    async def run():
        actor=await actor_from_token(tokens['admin']);viewer=await actor_from_token(tokens['viewer'])
        _,_,queued=await setup(actor);run_id=queued['id'];started=time.monotonic()
        try:
            ingest=(await execution.begin_stage(actor,run_id,{'node':'source-wait-fixture','input_hash':uuid4().hex*2}))['attempt']['id']
            await execution.mark_stage(actor,ingest,'started')
            artifact=await artifacts.upload(actor,ingest,video,'video/mp4',duration_ms=2000)
            await candidates.source(actor,ingest,{'artifact_id':artifact['id'],'duration_ms':2000,
                'identity_evidence':{'scope':'仅待审RPC契约夹具，2秒合成，无内容质量结论'}})
            await execution.finish_stage(actor,ingest,{'status':'succeeded','outputs':[artifact['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            attempt=(await execution.begin_stage(actor,run_id,{'node':'source_preparation_review','partition':str(uuid4()),'input_hash':uuid4().hex*2}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started')
            async def wait(current):return await current.objects.rpc('deep_dub_wait_for_source_review',p_workspace=current.workspace_id,p_run=run_id,p_attempt=attempt)
            with pytest.raises(BusinessError):await wait(actor)
            note=tmp_path/'note.json';note.write_text(json.dumps({'scope':'仅待审RPC契约夹具，不签发质量通过'}),encoding='utf-8')
            evidence=await artifacts.upload(actor,attempt,note,'application/json')
            await execution.finish_stage(actor,attempt,{'status':'waiting_review','outputs':[evidence['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            result=await wait(actor)
            assert result['status']=='waiting_review' and result['technical_verified_at'] is None
            with pytest.raises(BusinessError):await wait(viewer)
            with pytest.raises(BusinessError):await execution.begin_stage(actor,run_id,{'node':'must-not-produce','input_hash':'b'*64})
            await execution.cancel(actor,run_id)
            with pytest.raises(BusinessError):await wait(actor)
        finally:
            current=(await actor.objects.select('deep_dub_runs',id='eq.'+run_id))[0]
            if current['status']!='cancelled':await execution.cancel(actor,run_id)
    asyncio.run(run())
