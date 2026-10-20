"""译配输入约束、恢复语义和真实 Supabase 隔离验证。"""
import os
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from anp_business.dubbing import media, nodes, repository as db, service
from anp_business.dubbing.graph import stage
from anp_business.dubbing.settings import local_input


def test_subtitle_chinese_filename_and_timing(tmp_path):
    path = tmp_path / "英文 字幕.srt"
    path.write_text("1\n00:00:00,120 --> 00:00:03,500\nHello, world.\n\n2\n00:00:04,000 --> 00:00:06,200\nDo not forget water.\n", encoding="utf-8")
    rows = nodes.parse_srt(path)
    assert rows[0]["start_seconds"] == pytest.approx(.12)
    assert rows[1]["text"] == "Do not forget water."
    assert media.srt_time(3661.123) == "01:01:01,123"


def test_subtitle_rejects_reverse_window(tmp_path):
    path = tmp_path / "bad.srt"
    path.write_text("1\n00:00:03,000 --> 00:00:01,000\nHello", encoding="utf-8")
    with pytest.raises(ValueError, match="无效时间窗"):
        nodes.parse_srt(path)


def test_file_access_requires_configured_roots(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = tmp_path / "outside.env"
    secret.write_text("example", encoding="utf-8")
    monkeypatch.setenv("ANP_DUB_INPUT_ROOTS", str(allowed))
    with pytest.raises(ValueError, match="不在"):
        local_input(str(secret))


def test_stage_resume_does_not_repeat_paid_work(monkeypatch):
    records = []
    run_id, workspace, project = map(str, (uuid4(), uuid4(), uuid4()))
    run = {"id": run_id, "workspace_id": workspace, "project_id": project, "request": {"input": "source"}, "status": "running"}
    monkeypatch.setattr(db, "get", lambda *_: run)
    monkeypatch.setattr(db, "rows", lambda *_args, **_kwargs: records)
    def insert(_name, **values):
        row = {"id": str(uuid4()), **values}; records.append(row); return row
    def update(_name, key, **values):
        if _name == "attempts":
            next(row for row in records if row["id"] == key).update(values)
    monkeypatch.setattr(db, "insert", insert)
    monkeypatch.setattr(db, "update", update)
    calls = []
    execute = stage("translate", lambda *_: calls.append(1) or {"translation_ids": {"one": "two"}})
    state = {"run_id": run_id, "source_asset_id": "source"}
    assert execute(state) == execute(state)
    assert calls == [1]
    execute({**state, "source_asset_id": "changed"})
    assert calls == [1, 1]


def test_cancelled_run_never_calls_provider(monkeypatch):
    monkeypatch.setattr(db, "get", lambda *_: {"status": "cancelled"})
    with pytest.raises(ValueError, match="取消"):
        stage("translate", lambda *_: pytest.fail("不应请求模型"))({"run_id": str(uuid4())})


def test_media_response_preserves_file_and_supports_range(monkeypatch, tmp_path):
    from anp_business.server.http import app
    source = tmp_path / "media.mp4"
    source.write_bytes(b"0123456789")
    monkeypatch.setattr(media, "materialize", lambda _: source)
    client = TestClient(app, base_url="http://127.0.0.1")
    response = client.get(f"/api/dubbing/assets/{uuid4()}/content", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert "inline" in response.headers["content-disposition"]


def test_local_api_rejects_external_origins():
    from anp_business.server.http import app
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/dubbing/runs", headers={"Origin": "https://example.org"}).status_code == 403


def test_exhausted_tts_candidates_do_not_repeat_paid_generation(monkeypatch):
    from pathlib import Path
    objects = {"voices": {"asset_id": "asset"}, "segments": {"start_seconds": 0, "end_seconds": 5},
               "translations": {"id": "translation", "text": "需要先修正的译稿"}}
    monkeypatch.setattr(db, "get", lambda name, _: objects[name])
    monkeypatch.setattr(db, "rows", lambda *_args, **_kwargs: [{"translation_id": "translation", "voice_id": "voice",
        "selected": False, "generation_factor": 1.35, "duration_seconds": 7}])
    monkeypatch.setattr(media, "materialize", lambda _: Path("unused.wav"))
    monkeypatch.setattr(nodes.providers, "audio_task", lambda *_: pytest.fail("不得原样再次付费调用"))
    with pytest.raises(ValueError, match="候选已用完"):
        nodes.synthesize({"id": "run"}, {"voice_id": "voice", "segment_ids": ["segment"],
                                         "translation_ids": {"segment": "translation"}})


@pytest.fixture
def isolated_database_workspace(monkeypatch):
    """真实数据库测试使用临时空间，只清理本次生成的 UUID，保留已有业务数据。"""
    import psycopg
    from psycopg import sql
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    workspace = uuid4()
    url = os.environ["ANP_DUB_DATABASE_URL"]
    with psycopg.connect(url) as conn:
        conn.execute("INSERT INTO dubbing.workspaces(id,name) VALUES (%s,'pytest 临时工作空间')", (workspace,))
        conn.execute("INSERT INTO dubbing.memberships(workspace_id,user_id) VALUES (%s,%s)",
                     (workspace, os.environ["ANP_DUB_ACTOR_ID"]))
    monkeypatch.setenv("ANP_DUB_WORKSPACE_ID", str(workspace))
    try:
        yield workspace
    finally:
        with psycopg.connect(url) as conn:
            for table in ("deliverables", "reviews", "syntheses", "attempts", "runs", "voices", "translations",
                          "segments", "speakers", "assets", "projects", "memberships", "workspaces"):
                column = "id" if table == "workspaces" else "workspace_id"
                conn.execute(sql.SQL("DELETE FROM dubbing.{} WHERE {}=%s").format(
                    sql.Identifier(table), sql.Identifier(column)), (workspace,))


@pytest.mark.skipif(os.getenv("ANP_DUB_TEST_DATABASE") != "1", reason="显式启用独立 Supabase 数据库集成验证")
def test_real_supabase_rls_and_relations(monkeypatch, isolated_database_workspace):
    import psycopg
    owner = os.environ["ANP_DUB_ACTOR_ID"]
    assert len(db.rows("workspaces", id=str(isolated_database_workspace))) == 1
    project = db.insert("projects", workspace_id=os.environ["ANP_DUB_WORKSPACE_ID"], title="RLS 验收项目")
    # 同一空间内的项目也必须通过复合外键关联，不能串用另一个项目的资产。
    other = db.insert("projects", workspace_id=os.environ["ANP_DUB_WORKSPACE_ID"], title="RLS 另一个项目")
    asset = db.insert("assets", workspace_id=project["workspace_id"], project_id=project["id"], kind="test",
                       bucket="test", object_key=str(uuid4()), sha256="a"*64, size_bytes=1)
    speaker = db.insert("speakers", workspace_id=other["workspace_id"], project_id=other["id"], label="test")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.insert("segments", workspace_id=other["workspace_id"], project_id=other["id"], source_asset_id=asset["id"],
                  speaker_id=speaker["id"], ordinal=0, start_seconds=0, end_seconds=1, text="test", provenance="test")
    monkeypatch.setenv("ANP_DUB_ACTOR_ID", str(uuid4()))
    assert db.rows("projects", id=str(project["id"])) == []
    assert db.rows("workspaces") == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.insert("projects", workspace_id=project["workspace_id"], title="应被拒绝")
    monkeypatch.setenv("ANP_DUB_ACTOR_ID", owner)


@pytest.mark.skipif(os.getenv("ANP_DUB_TEST_DATABASE") != "1", reason="显式启用独立 Supabase 数据库集成验证")
def test_request_key_and_cancel_with_real_supabase(monkeypatch, tmp_path, isolated_database_workspace):
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    # 这里只验证登记幂等与取消，不启动媒体阶段，也不把字节夹具冒充真实视频。
    fixture = tmp_path / "identity-only.bin"
    fixture.write_bytes(b"idempotency fixture; never submitted to media models")
    monkeypatch.setenv("ANP_DUB_INPUT_ROOTS", str(tmp_path))
    source = str(fixture)
    key = uuid4()
    first = service.create_run(source, "幂等与取消验收", request_key=key)
    second = service.create_run(source, "幂等与取消验收", request_key=key)
    assert first["id"] == second["id"]
    service.cancel(str(first["id"]))
    with pytest.raises(ValueError, match="取消"):
        service.execute(str(first["id"]))
