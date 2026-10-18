"""冻结HTTP、默认本地CLI及直接认证RPC均不得将技术测量当正文通过。"""
import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.session import current_actor, login, session_path
from anp.errors import BusinessError
from anp.sdk.http import async_client

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_CONTENT_GATE_LIVE')!='1',reason='需当前待审来源与冻结平台')


def test_current_pending_source_rejects_false_technical_pass_in_all_entries(identities,monkeypatch,tmp_path):
    record=json.loads((ROOT/'.local/deep-dub-independent-live.json').read_bytes())
    project,source,run=record['project_id'],record['source_id'],record['run_id']
    monkeypatch.setenv('ANP_PROFILE','source-content-gate-'+uuid4().hex)
    async def inspect(actor):
        return {name:await actor.objects.select(name,project_id='eq.'+project,order='id.asc') for name in
                ('deep_dub_runs','deep_dub_audits','deep_dub_segment_sets','deep_dub_stage_attempts')}
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=await inspect(actor);row=next(x for x in before['deep_dub_runs'] if x['id']==run)
        body={'kind':'source','subject_id':source,'quality_profile_id':row['quality_profile_id'],
              'rule_key':'transcript_coverage','rule_revision':'source-coverage-rejection-probe-v1',
              'status':'passed','method':'technical','evidence_artifact_id':record['readback']['evidence_artifact_id']}
        async with async_client(timeout=30,trust_env=False) as client:
            response=await client.post(f'http://127.0.0.1:8181/api/apps/deep_dub/projects/{project}/audits',
                headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id},json=body)
            assert response.status_code==422
        with pytest.raises(BusinessError,match='人工或独立模型'):
            await actor.objects.rpc('deep_dub_record_audit',p_workspace=actor.workspace_id,p_project=project,
                p_kind='source',p_subject=source,p_profile=row['quality_profile_id'],p_rule='transcript_coverage',
                p_rule_revision=body['rule_revision'],p_status='passed',p_method='technical',
                p_evidence=body['evidence_artifact_id'],p_details={},p_request_key=uuid4().hex)
        assert await inspect(actor)==before
        return actor,before,body
    try:
        actor,before,body=asyncio.run(scenario())
        file=tmp_path/'invalid-technical-content-pass.json';file.write_bytes(json.dumps(body).encode())
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            reply=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','audit',project,'--input',str(file)],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
            assert reply.returncode!=0
            assert '人工或独立模型' in reply.stdout+reply.stderr
        assert asyncio.run(inspect(actor))==before
        (ROOT/'.local/deep-dub-source-content-gate-live.json').write_bytes(json.dumps({
            'project_id':project,'source_id':source,'run_id':run,'http_rejected':422,
            'authenticated_rpc_rejected':True,'local_and_http_cli_rejected':True,
            'audit_stage_segments_run_unchanged':True,'source_status':'not_measured'},ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
