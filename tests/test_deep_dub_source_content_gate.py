"""独立合成库验证正文审核的方法门禁；不声称夹具完成了内容审核。"""
from uuid import uuid4

import psycopg
from pydantic import ValidationError
import pytest

from anp import ROOT
from apps.deep_dub.services.versions import AuditInput
from tests.test_deep_dub_foundation import actor, database, pytestmark
from tests.test_deep_dub_versions import versions, seed, put, audit
from tests.test_deep_dub_execution import executions, work, qualified_render

MIGRATION = ROOT/'apps/deep_dub/migrations/018_source_content_audit_gate.sql'


@pytest.fixture
def gate(work):
    with work['connect']() as c:
        for _ in range(2): c.execute(MIGRATION.read_text(encoding='utf-8'), prepare=False)
    return work


def legacy(c, s):
    c.execute('RESET ROLE')
    fingerprint = c.execute("SELECT platform.deep_dub_subject_hash(%s,'source',%s)",
                            (s['workspace_id'], s['source'])).fetchone()[0]
    key = put(c, 'audits', workspace_id=s['workspace_id'], project_id=s['project'], source_id=s['source'],
              quality_profile_id=s['profile'], rule_key='transcript_coverage', rule_revision='legacy-fixture',
              subject_hash=fingerprint, status='passed', method='technical', evidence_artifact_id=s['evidence'],
              details={'fixture_only': True}, request_key=str(uuid4()), request_hash='f'*64, created_by=s['created_by'])
    actor(c, s['created_by'])
    return key


def assert_source(c, s):
    # 内部消费函数不向普通用户开放；独立库直测用管理员，公开验收走 verify_run。
    c.execute('RESET ROLE')
    c.execute("SELECT platform.deep_dub_assert_latest_audits(%s,%s,'source',%s,%s,ARRAY['transcript_coverage'])",
              (s['workspace_id'], s['project'], s['source'], s['profile']))
    actor(c, s['created_by'])


def test_python_and_database_reject_technical_content_pass_but_keep_measurements(gate):
    s = gate
    payload = dict(kind='source', subject_id=s['source'], quality_profile_id=s['profile'], rule_key='transcript_coverage',
                   rule_revision='fixture', status='passed', method='technical', evidence_artifact_id=s['evidence'])
    with pytest.raises(ValidationError, match='人工或独立模型'):
        AuditInput.model_validate(payload)
    with s['connect']() as c:
        actor(c, s['created_by'])
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): audit(c, s, 'source', 'transcript_coverage', method='technical')
        for status in ('failed', 'not_measured'):
            AuditInput.model_validate({**payload, 'status': status})
            audit(c, s, 'source', 'transcript_coverage', status=status, method='technical')
        # 真实时长、音轨等技术项目依旧可测；并未一刀切禁用 technical。
        audit(c, s, 'source', 'duration', method='technical')
        for method in ('human', 'independent_model'):
            AuditInput.model_validate({**payload, 'method': method})
            audit(c, s, 'source', 'transcript_coverage', method=method)
            assert_source(c, s)


def test_legacy_technical_pass_remains_visible_but_never_counts_as_content_review(gate):
    s = gate
    with s['connect']() as c:
        actor(c, s['created_by'])
        audit(c, s, 'source', 'transcript_coverage', method='human')
        old = legacy(c, s)
        before = c.execute('SELECT to_jsonb(a) FROM platform.deep_dub_audits a WHERE id=%s', (old,)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): assert_source(c, s)
        assert c.execute('SELECT to_jsonb(a) FROM platform.deep_dub_audits a WHERE id=%s', (old,)).fetchone()[0] == before
        audit(c, s, 'source', 'transcript_coverage', method='independent_model')
        assert_source(c, s)
        audit(c, s, 'source', 'transcript_coverage', status='failed', method='technical')
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction(): assert_source(c, s)


def test_full_render_verification_rejects_old_technical_coverage_without_changing_run(gate):
    s = gate
    with s['connect']() as c:
        render = qualified_render(c, s)
        old = legacy(c, s)
        before = c.execute('SELECT to_jsonb(r) FROM platform.deep_dub_runs r WHERE id=%s', (s['run'],)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():
                c.execute('SELECT platform.deep_dub_verify_run(%s,%s,%s)', (s['workspace_id'], s['run'], render))
        assert c.execute('SELECT to_jsonb(r) FROM platform.deep_dub_runs r WHERE id=%s', (s['run'],)).fetchone()[0] == before
        assert c.execute('SELECT status,method FROM platform.deep_dub_audits WHERE id=%s', (old,)).fetchone() == ('passed','technical')
        audit(c, s, 'source', 'transcript_coverage', method='human')
        result = c.execute('SELECT platform.deep_dub_verify_run(%s,%s,%s)', (s['workspace_id'], s['run'], render)).fetchone()[0]
        assert result['verified'] is True  # 仅合成关系测试；没有真实媒体或内容审核。
