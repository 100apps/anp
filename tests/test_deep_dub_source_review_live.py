"""真实整片的同一复核队列经本地CLI、冻结HTTP及私有媒体读取；不改变审核或生产状态。"""
import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.session import login, current_actor, session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.source_review import read

pytestmark = pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_REVIEW_LIVE') != '1', reason='需本轮整片证据与冻结生产API')


def test_real_source_queue_two_clis_permissions_and_media_without_mutation(identities, monkeypatch):
    record = json.loads((ROOT/'.local/deep-dub-source-preparation-final.json').read_bytes())
    project, source, run = record['project_id'], record['acquisition']['source_id'], record['run_id']
    monkeypatch.setenv('ANP_PROFILE', 'source-review-live-'+uuid4().hex)
    before = {}
    async def inspect(actor):
        return {table: await actor.objects.select(table, project_id='eq.'+project, order='id.asc') for table in
                ('deep_dub_stage_attempts', 'deep_dub_audits', 'deep_dub_segment_sets', 'deep_dub_translations', 'deep_dub_runs')}
    async def scenario():
        await login(identities['admin']['email'], identities['admin']['password'])
        actor = await current_actor()
        before.update(await inspect(actor))
        expected = await read(actor, project, source)
        assert expected['total'] == 213 and expected['counts'] == {'invalid_word_time': 1, 'reference_difference': 152, 'word_coverage': 17, 'chunk_boundary': 43}
        assert expected['numeric_differences'] == 20
        path = f'/api/apps/deep_dub/projects/{project}/sources/{source}/review-queue'
        headers = {'Authorization': 'Bearer '+actor.token, 'X-Workspace-ID': actor.workspace_id}
        async with async_client(base_url='http://127.0.0.1:8181', timeout=90, trust_env=False) as client:
            response = await client.get(path, headers=headers)
            assert response.status_code == 200 and response.json() == expected
            numeric = await client.get(path, params={'kind': 'numbers', 'expected_hash': expected['packet_sha256']}, headers=headers)
            assert numeric.status_code == 200 and numeric.json()['matched'] == 20
            assert all(i['contains_number'] for i in numeric.json()['items'])
            collected = []
            for offset in range(0, expected['total'], 100):
                page = await client.get(path, params={'offset': offset, 'limit': 100, 'expected_hash': expected['packet_sha256']}, headers=headers)
                assert page.status_code == 200
                collected.extend(page.json()['items'])
            assert len({i['id'] for i in collected}) == 213
            assert all(i['review_status'] == 'not_measured' for i in collected)
            for params, status in [({'expected_hash': '0'*64}, 409), ({'limit': 0}, 422), ({'kind': 'passed'}, 422)]:
                assert (await client.get(path, params=params, headers=headers)).status_code == status
            assert (await client.get(path)).status_code == 401
            outside = identities['outside']
            assert (await client.get(path, headers={'Authorization': 'Bearer '+outside['token'], 'X-Workspace-ID': outside['workspace_id']})).status_code == 404
            pcm = expected['binding']['pcm_artifact_id']
            session = await client.post('/api/apps/deep_dub/media/session', headers=headers)
            assert session.status_code == 200
            media = await client.get('/api/apps/deep_dub/media/artifacts/'+pcm, headers={'Range': 'bytes=0-255'})
            assert media.status_code == 206 and media.content[:4] == b'RIFF'
            assert (await client.delete('/api/apps/deep_dub/media/session')).status_code == 204
        return expected, actor
    try:
        expected, actor = asyncio.run(scenario())
        for options in [('--endpoint', 'http://127.0.0.1:1'), ('--transport', 'http', '--endpoint', 'http://127.0.0.1:8181')]:
            result = subprocess.run([sys.executable, '-m', 'anp.cli.entry', *options, 'deep_dub', 'projects', 'source-review-queue', project, source],
                                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=120)
            assert result.returncode == 0, result.stdout+result.stderr
            assert json.loads(result.stdout) == expected
        after = asyncio.run(inspect(actor))
        assert before == after
        assert next(x for x in after['deep_dub_runs'] if x['id'] == run)['status'] == 'waiting_review'
        (ROOT/'.local/deep-dub-source-review-live.json').write_bytes(json.dumps({
            'project_id': project, 'source_id': source, 'run_id': run, 'packet_sha256': expected['packet_sha256'],
            'counts': expected['counts'], 'total': expected['total'], 'numeric_differences': expected['numeric_differences'],
            'same_local_http_contract': True, 'cross_workspace_rejected': True, 'private_pcm_range': 206,
            'stage_audit_run_unchanged': True, 'source_review': expected['source_review']}, ensure_ascii=False, indent=2).encode())
    finally:
        session_path().unlink(missing_ok=True)
