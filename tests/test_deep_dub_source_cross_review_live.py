"""整轨跨模型复核的真实读回；不推理、不改稿、不解除待审。"""
import asyncio
from hashlib import sha256
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.session import current_actor, login, session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.source_review import read

pytestmark = pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_CROSS_REVIEW_LIVE') != '1',
                                reason='需当前来源的两套整轨识别及冻结业务API')


def test_real_cross_model_queue_two_clis_permissions_and_no_writes(identities, monkeypatch):
    record = json.loads((ROOT/'.local/deep-dub-independent-live.json').read_bytes())
    project, source, run = record['project_id'], record['source_id'], record['run_id']
    monkeypatch.setenv('ANP_PROFILE', 'source-cross-review-'+uuid4().hex)
    journal = ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json'
    journal_hash = sha256(journal.read_bytes()).hexdigest()
    async def inspect(actor):
        return {table: await actor.objects.select(table, project_id='eq.'+project, order='id.asc') for table in
                ('deep_dub_stage_attempts', 'deep_dub_audits', 'deep_dub_segment_sets', 'deep_dub_translations', 'deep_dub_runs')}
    async def scenario():
        await login(identities['admin']['email'], identities['admin']['password'])
        actor = await current_actor()
        before = await inspect(actor)
        original = await read(actor, project, source)
        assert original['total'] == 213
        expected = await read(actor, project, source, include_secondary=True)
        assert expected['total'] == 460 and expected['counts']['cross_model_difference'] == 247
        assert expected['base_packet_sha256'] == original['packet_sha256']
        assert expected['binding']['secondary_evidence_artifact_id'] == record['readback']['evidence_artifact_id']
        headers = {'Authorization': 'Bearer '+actor.token, 'X-Workspace-ID': actor.workspace_id}
        path = f'/api/apps/deep_dub/projects/{project}/sources/{source}/review-queue'
        async with async_client(base_url='http://127.0.0.1:8181', timeout=90, trust_env=False) as client:
            response = await client.get(path, params={'include_secondary': 'true'}, headers=headers)
            assert response.status_code == 200 and response.json() == expected
            collected = []
            for offset in range(0, expected['total'], 100):
                response = await client.get(path, params={'offset': offset, 'limit': 100,
                    'include_secondary': 'true', 'expected_hash': expected['packet_sha256']}, headers=headers)
                assert response.status_code == 200
                collected.extend(response.json()['items'])
            assert len({i['id'] for i in collected}) == 460
            assert all(i['review_status'] == 'not_measured' for i in collected)
            for item in collected:
                assert 0 <= item['audition']['start_sample'] < item['audition']['end_sample'] <= 41380932
            cross = await client.get(path, params={'include_secondary': 'true', 'kind': 'cross_model_difference'}, headers=headers)
            assert cross.status_code == 200 and cross.json()['matched'] == 247
            for params, status in [({'include_secondary':'true', 'expected_hash':original['packet_sha256']},409),
                                   ({'kind':'cross_model_difference'},422)]:
                assert (await client.get(path, params=params, headers=headers)).status_code == status
            assert (await client.get(path, params={'include_secondary':'true'})).status_code == 401
            outside = identities['outside']
            assert (await client.get(path, params={'include_secondary':'true'}, headers={
                'Authorization':'Bearer '+outside['token'], 'X-Workspace-ID':outside['workspace_id']})).status_code == 404
        return actor, before, original, expected
    try:
        actor, before, original, expected = asyncio.run(scenario())
        for options in [('--endpoint','http://127.0.0.1:1'),
                        ('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            result = subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','projects',
                'source-review-queue',project,source,'--include-secondary'], cwd=ROOT, capture_output=True,
                text=True, encoding='utf-8', timeout=120)
            assert result.returncode == 0, result.stdout+result.stderr
            assert json.loads(result.stdout) == expected
        assert asyncio.run(inspect(actor)) == before
        assert sha256(journal.read_bytes()).hexdigest() == journal_hash
        assert next(row for row in before['deep_dub_runs'] if row['id']==run)['status'] == 'waiting_review'
        (ROOT/'.local/deep-dub-cross-review-live.json').write_bytes(json.dumps({
            'project_id': project, 'source_id': source, 'run_id': run,
            'original_packet_sha256': original['packet_sha256'], 'packet_sha256': expected['packet_sha256'],
            'counts': expected['counts'], 'total': expected['total'], 'numeric_differences': expected['numeric_differences'],
            'same_local_http_contract': True, 'cross_workspace_rejected': True,
            'no_model_restart': True, 'gpu_journal_sha256': journal_hash,
            'stage_audit_run_unchanged': True, 'source_review': expected['source_review']}, ensure_ascii=False, indent=2).encode())
    finally:
        session_path().unlink(missing_ok=True)
