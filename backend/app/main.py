"""FastAPI entry point. Run with:  python -m uvicorn app.main:app --port 8777  (from /backend)"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.discovery_routes import router as discovery_router
from .api.execution_routes import router as execution_router
from .api.live_routes import router as live_router
from .api.routes import router
from .config import ROOT


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.environ.get("MARKETLAB_NO_LIVE") != "1":
        from .live.service import service
        service.start()
    yield


app = FastAPI(title="Market Research Lab", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
app.include_router(live_router)
app.include_router(discovery_router)
app.include_router(execution_router)

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST.resolve() in f.parents:  # icons, manifest, favicon
            return FileResponse(f)
        # never cache the page itself: after a rebuild a plain reload must pick up the new assets
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
