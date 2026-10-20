"""可独立启动的轻量计算适配器：容器上传真实字节，宿主持有原生GPU进程。"""
import asyncio
import base64
import secrets
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from anp_business.dubbing import media, providers
from anp_business.dubbing.settings import required

app = FastAPI(title="ANP2 独立音频计算端", docs_url=None, openapi_url=None)


class Task(BaseModel):
    task: Literal["asr", "tts", "separation"]
    request: dict = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)


@app.post("/tasks")
async def execute(body: Task, authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, "Bearer " + required("ANP_DUB_AUDIO_TOKEN")):
        raise HTTPException(401, "音频计算端认证失败")
    # 不接受客户端指定宿主文件路径或模型配置；每个任务只能使用它上传的真实音频。
    if set(body.files) - {"audio", "voice_ref"} or not body.files.get("audio"):
        raise HTTPException(422, "需要音频字节，不接受宿主路径")
    allowed = {"text", "language", "seed", "do_sample", "num_beams", "max_tokens", "options"}
    if set(body.request) - allowed:
        raise HTTPException(422, "存在不支持的模型请求字段")
    worker_id = "worker-" + str(uuid4())
    request = dict(body.request)
    for name, encoded in body.files.items():
        if len(encoded) > 90 * 1024 ** 2:
            raise HTTPException(413, "音频文件超限")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise HTTPException(422, "音频编码不合法") from None
        target = media.folder(worker_id) / (name + ".wav")
        await asyncio.to_thread(target.write_bytes, raw)
        request[name] = str(target)
    if body.task == "tts" and "voice_ref" not in request:
        raise HTTPException(422, "配音需要上传固定音色")
    try:
        return await asyncio.to_thread(providers.audio_task, worker_id, body.task, request)
    except Exception:
        raise HTTPException(502, "本次音频计算失败，参见本项目计算端日志") from None
