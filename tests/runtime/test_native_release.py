"""执行实际工作流中的发布代码，覆盖尚未创建标签的草稿。"""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SHA = "4" * 40


def execute_publisher(monkeypatch, tmp_path, *, existing=False, partial=False,
                      published=False, corrupt=False, wrong_source=False):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "archive.tar.gz").write_bytes(b"accepted archive")
    (artifacts / "receipt.json").write_text('{"passed":true}')
    (tmp_path / "release-notes.md").write_text("已验收的发行包")
    assets = [{"name": p.name, "size": p.stat().st_size,
               "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()}
              for p in sorted(artifacts.iterdir())]
    state = {"release": {"id": 42, "tag_name": "native-v0.3.5", "draft": not published,
                         "target_commitish": "0" * 40 if wrong_source else SHA,
                         "assets": assets[:1] if partial else assets} if existing else None,
             "created": 0, "uploads": 0, "published": 0}

    def run(args, **kwargs):
        if args[:3] == ["gh", "release", "create"]:
            assert state["release"] is None, "重复创建草稿"
            state["created"] += 1
            state["release"] = {"id": 42, "tag_name": "native-v0.3.5", "draft": True,
                                "target_commitish": SHA, "assets": assets}
        elif args[:3] == ["gh", "release", "upload"]:
            state["uploads"] += 1
            state["release"]["assets"] = assets
        else:
            raise AssertionError("意外的写操作：" + repr(args))
        return subprocess.CompletedProcess(args, 0)

    def check_output(args, **kwargs):
        endpoint = next(x for x in args if x.startswith("repos/"))
        if "/releases/tags/" in endpoint:
            # 真实第五轮的失败：新标签未公开，按标签查询草稿得到 404。
            raise subprocess.CalledProcessError(1, args, stderr=b"Not Found (HTTP 404)")
        if endpoint.endswith("releases?per_page=100"):
            return json.dumps([[state["release"]] if state["release"] else []]).encode()
        assert endpoint.endswith("releases/42")
        if "PATCH" in args:
            assert not corrupt
            assert json.loads(kwargs["input"]) == {"draft": False}
            state["published"] += 1
            state["release"]["draft"] = False
        value = json.loads(json.dumps(state["release"]))
        if corrupt:
            value["assets"][0]["digest"] = "sha256:" + "0" * 64
        return json.dumps(value).encode()

    workflow = yaml.safe_load((ROOT / ".github/workflows/native-release.yml").read_text())
    step = next(s for s in workflow["jobs"]["release"]["steps"]
                if s.get("name") == "上传草稿、核对服务端摘要后发布")
    script = "\n".join(step["run"].splitlines()[1:-1])
    monkeypatch.chdir(tmp_path)
    for key, value in {"GITHUB_REPOSITORY": "100apps/anp", "ANP_VERSION": "0.3.5", "GITHUB_SHA": SHA}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "check_output", check_output)
    exec(compile(script, str(ROOT / ".github/workflows/native-release.yml"), "exec"), {})
    return state


def test_new_draft_without_public_tag_is_verified_and_published_by_id(monkeypatch, tmp_path):
    state = execute_publisher(monkeypatch, tmp_path)
    assert state["created"] == 1 and state["published"] == 1
    assert state["release"]["draft"] is False


def test_existing_complete_draft_is_resumed_without_duplicate_creation(monkeypatch, tmp_path):
    state = execute_publisher(monkeypatch, tmp_path, existing=True)
    assert state["created"] == 0 and state["uploads"] == 0 and state["published"] == 1


def test_partial_draft_uploads_missing_assets_before_publishing(monkeypatch, tmp_path):
    state = execute_publisher(monkeypatch, tmp_path, existing=True, partial=True)
    assert state["created"] == 0 and state["uploads"] == 1 and state["published"] == 1


def test_published_release_is_only_verified_on_retry(monkeypatch, tmp_path):
    state = execute_publisher(monkeypatch, tmp_path, existing=True, published=True)
    assert state["created"] == 0 and state["uploads"] == 0 and state["published"] == 0


def test_corrupt_server_digest_prevents_publishing(monkeypatch, tmp_path):
    with pytest.raises(AssertionError, match="服务端制品摘要不一致"):
        execute_publisher(monkeypatch, tmp_path, existing=True, corrupt=True)


def test_existing_release_from_other_source_is_rejected(monkeypatch, tmp_path):
    with pytest.raises(AssertionError, match="源码提交不一致"):
        execute_publisher(monkeypatch, tmp_path, existing=True, wrong_source=True)
