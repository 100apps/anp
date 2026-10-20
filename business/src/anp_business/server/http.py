import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from anp_business.shared.models import model_mode
from anp_business.dubbing.api import router as dubbing_router
from anp_business.server.platforms import router as platform_router

app = FastAPI(title="ANP3 业务扩展", docs_url=None, openapi_url=None)
app.include_router(dubbing_router)
app.include_router(platform_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "anp3", "model_mode": model_mode()}


@app.get("/api/workbench")
def workbench():
    return {"title": "ANP3 工作台", "graphs": ["workflow_demo", "deepagent_demo", "video_dubbing"],
            "model_mode": model_mode(), "auth_mode": "local-only"}


@app.get("/")
def root():
    return RedirectResponse("/workbench/")


static_dir = Path(os.getenv("ANP_UI_DIST", Path(__file__).resolve().parents[3] / "ui" / "dist"))
if static_dir.is_dir():
    app.mount("/workbench", StaticFiles(directory=static_dir, html=True), name="workbench")
