"""仅供本机开发工作台的对象API；部署多用户时应接 Supabase Auth 验证身份。"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from anp_business.dubbing import media, service
from anp_business.dubbing import repository as db

router = APIRouter(prefix="/api/dubbing", tags=["视频译配"])


def local_only(request: Request):
    host = request.url.hostname
    origin = request.headers.get("origin")
    if host not in ("127.0.0.1", "localhost") or origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
        raise HTTPException(403, "当前为固定身份的本机开发模式；禁止通过公网或局域网访问")
    if os.getenv("ANP_DUB_LOCAL_API", "true") != "true":
        raise HTTPException(503, "尚未配置多用户认证适配器")


router.dependencies = [Depends(local_only)]


class Create(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    title: str = Field(default="视频译配", min_length=1, max_length=200)
    subtitle: str | None = None
    request_key: UUID


async def invoke(function, *args):
    try:
        return jsonable_encoder(await asyncio.to_thread(function, *args))
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(503, "服务未就绪或请求失败；请查看本项目运行日志") from None


@router.get("/runs")
async def list_runs():
    return await invoke(service.list_runs)


@router.post("/runs", status_code=201)
async def create(body: Create):
    return await invoke(service.create_run, body.input, body.title, body.subtitle, body.request_key)


@router.get("/runs/{run_id}")
async def detail(run_id: UUID):
    return await invoke(service.describe, str(run_id))


def start_process(run_id):
    # 长作业不挂在ASGI事件循环/线程池，前后端热重载不会等待或打断一次真实配音。
    # 数据库运行锁保证多个请求不会重复调用模型；重启容器后显式重试失联阶段。
    log_path = media.folder(run_id) / "worker.log"
    with log_path.open("ab") as log:
        subprocess.Popen([sys.executable, "-m", "anp_business.dubbing.worker", run_id],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=0x08000000 if os.name == "nt" else 0)


@router.post("/runs/{run_id}/start", status_code=202)
async def start(run_id: UUID):
    await invoke(db.get, "runs", run_id)
    await invoke(start_process, str(run_id))
    return {"run_id": str(run_id), "status": "accepted"}


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: UUID):
    return await invoke(service.cancel, str(run_id))


@router.get("/assets/{asset_id}/content")
async def content(asset_id: UUID):
    path = await invoke(media.materialize, str(asset_id))
    return FileResponse(path, filename=Path(path).name, content_disposition_type="inline")
