"""只读核验已完成的译配对象及私有产物，不重新调用模型。"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "business/src"))
load_dotenv(ROOT / "business/.env")

from anp_business.dubbing import media  # noqa: E402
from anp_business.dubbing import repository as db


def inspect(run_id):
    state = db.snapshot(run_id)
    run = state["run"]
    assert run["status"] == "ready_for_review", "必须保留待视听复核状态"
    completed = {row["stage"] for row in state["attempts"] if row["status"] == "succeeded"}
    assert completed == {"acquire", "source_text", "background", "translate", "review_translation", "voices", "synthesize", "audio_review", "mix", "finish"}
    selected = [row for row in state["syntheses"] if row["selected"]]
    assert {str(row["segment_id"]) for row in selected} == {str(row["id"]) for row in state["segments"]}
    assert len(state["deliverables"]) == 1
    manifest = state["deliverables"][0]["manifest"]
    assert set(manifest["synthesis_ids"]) == {str(row["id"]) for row in selected}
    for row in selected:
        translation = db.get("translations", row["translation_id"])
        assert row["segment_id"] == translation["segment_id"]
        assert row["duration_seconds"] <= row["evidence"]["window"] + .05
        assert str(row["translation_id"]) == manifest["translation_ids"][str(row["segment_id"]) ]
    checked = []
    client = media.s3()
    for asset in state["assets"]:
        response = client.get_object(Bucket=asset["bucket"], Key=asset["object_key"])
        hasher = hashlib.sha256()
        size = 0
        with response["Body"] as body:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                hasher.update(chunk)
                size += len(chunk)
        assert hasher.hexdigest() == asset["sha256"] and size == asset["size_bytes"]
        checked.append({"id": str(asset["id"]), "kind": asset["kind"], "sha256": asset["sha256"], "size_bytes": size})
    reviews = [{"kind": row["kind"], "verdict": row["verdict"], "tool_calls": row["evidence"].get("tool_calls"),
                "subject_sha256": row["subject_sha256"]} for row in state["reviews"]]
    for kind in ("translation_semantics", "audio_text"):
        matching = [row for row in reviews if row["kind"] == kind]
        assert matching and matching[-1]["verdict"] == "passed" and matching[-1]["tool_calls"] >= 1
    elapsed = (run["finished_at"] - run["created_at"]).total_seconds()
    return {"run_id": run_id, "status": run["status"], "duration_seconds": db.get("assets", manifest["source_asset_id"])["duration_seconds"],
        "elapsed_seconds_including_failure_and_queue": elapsed, "stages": sorted(completed),
        "segment_count": len(state["segments"]), "translation_versions": len(state["translations"]),
        "asset_readbacks": checked, "reviews": reviews, "manifest": manifest,
        "model_identity": "LiteLLM auto 路由；未核实其底层模型身份", "professional_quality_passed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--output", default="var/evidence/dubbing-acceptance.json")
    args = parser.parse_args()
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "scope": "真实短片技术链路与对象完整性；非长片或完整质量达标", "runs": [inspect(run) for run in args.runs]}
    target = ROOT / args.output
    if not target.resolve().is_relative_to(ROOT):
        raise ValueError("验收输出须在本工作区")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已核验 {len(result['runs'])} 次运行的十阶段、采用对象与全部私有产物 SHA256。")


if __name__ == "__main__":
    main()
