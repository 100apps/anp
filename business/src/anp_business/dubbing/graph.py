"""LangGraph 并行编排；阶段结果与重试次数持久化到 Supabase。"""
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from anp_business.dubbing import media, nodes
from anp_business.dubbing import repository as db


class State(TypedDict, total=False):
    run_id: str
    source_asset_id: str
    pcm_asset_id: str
    duration: float
    segment_ids: list[str]
    speaker_id: str
    background_asset_id: str
    vocals_asset_id: str
    translation_ids: dict[str, str]
    voice_id: str
    synthesis_ids: list[str]
    audio_text_checked: bool
    output_asset_id: str
    deliverable_id: str


def now():
    return datetime.now(timezone.utc)


def stage(name, implementation):
    def execute(state):
        run = db.get("runs", state["run_id"])
        if run["status"] == "cancelled":
            raise ValueError("运行已取消")
        key = media.fingerprint({"revision": "dubbing-v1", "request": run["request"], "state": state, "stage": name})
        history = db.rows("attempts", run_id=str(run["id"]), stage=name)
        for prior in reversed(history):
            if prior["status"] == "succeeded" and prior["input_sha256"] == key:
                print(f"复用阶段 {name}（当前输入一致）", flush=True)
                return prior["output"]
        attempt = db.insert("attempts", **nodes.scoped(run), stage=name, attempt=len(history) + 1,
                             input_sha256=key, status="running")
        print(f"执行阶段 {name}，尝试 {len(history)+1}", flush=True)
        db.update("runs", run["id"], current_stage=name)
        try:
            output = implementation(run, state)
            if db.get("runs", run["id"])["status"] == "cancelled":
                raise ValueError("运行已取消；本阶段未提交为成功")
            db.update("attempts", attempt["id"], status="succeeded", output=output, finished_at=now())
            return output
        except Exception as exc:
            # 上游异常正文可能含鉴权请求，不向公共对象或HTTP返回其原文。
            reason = str(exc) if isinstance(exc, (ValueError, LookupError, RuntimeError, TimeoutError)) else type(exc).__name__
            db.update("attempts", attempt["id"], status="failed", error=reason[:500], finished_at=now())
            raise RuntimeError(f"{name}：{reason[:500]}") from None
    return execute


def build_graph():
    builder = StateGraph(State)
    for name in ("acquire", "source_text", "background", "translate", "review_translation", "voices", "synthesize", "audio_review", "mix", "finish"):
        builder.add_node(name, stage(name, getattr(nodes, name)))
    builder.add_edge(START, "acquire")
    builder.add_edge("acquire", "source_text")
    builder.add_edge("acquire", "background")
    builder.add_edge("source_text", "translate")
    builder.add_edge("translate", "review_translation")
    builder.add_edge(["source_text", "background"], "voices")
    builder.add_edge(["review_translation", "voices"], "synthesize")
    builder.add_edge("synthesize", "audio_review")
    builder.add_edge("audio_review", "mix")
    builder.add_edge("mix", "finish")
    builder.add_edge("finish", END)
    return builder.compile(name="video_dubbing")


graph = build_graph()


class EntryState(TypedDict, total=False):
    run_id: str
    status: str
    deliverable_ids: list[str]


def execute_entry(state):
    from anp_business.dubbing.service import execute
    result = execute(state["run_id"])
    return {"status": result["run"]["status"], "deliverable_ids": [str(row["id"]) for row in result["deliverables"]]}


entry = StateGraph(EntryState)
entry.add_node("execute_persisted_run", execute_entry)
entry.add_edge(START, "execute_persisted_run")
entry.add_edge("execute_persisted_run", END)
entry_graph = entry.compile(name="video_dubbing")
