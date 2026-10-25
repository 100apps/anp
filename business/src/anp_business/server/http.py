import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from anp_business.shared.models import model_mode
from anp_business.server.platforms import router as platform_router, RustFSS3Proxy
from anp_business.server.auth import router as auth_router
from anp_business.server.console import router as console_router
from anp_business.server.oauth import router as oauth_router, OAuthSession
from anp_business.server.security import PlatformAccess
from anp_business.shared.telemetry import TelemetryMiddleware
from anp_business.server.ingress import NativeIngress

app = FastAPI(title="ANP 业务扩展", docs_url=None, openapi_url=None)
app.include_router(platform_router)
app.include_router(auth_router)
app.include_router(console_router)
app.include_router(oauth_router)
app.add_middleware(OAuthSession)
app.add_middleware(NativeIngress)
app.add_middleware(RustFSS3Proxy)
app.add_middleware(PlatformAccess)
app.add_middleware(TelemetryMiddleware)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "anp", "model_mode": model_mode()}


@app.get("/api/workbench")
def workbench():
    return {"title": "ANP 工作台", "graphs": ["workflow_demo", "deepagent_demo"],
            "model_mode": model_mode(), "auth_mode": "supabase"}


@app.get("/")
def root():
    return RedirectResponse("/workbench/")


static_dir = Path(os.getenv("ANP_UI_DIST", Path(__file__).resolve().parents[3] / "ui" / "dist"))
@app.get("/workbench/console/")
def console_page():
    return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-store"})


if static_dir.is_dir():
    app.mount("/workbench", StaticFiles(directory=static_dir, html=True), name="workbench")
