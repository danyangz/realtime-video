"""The one server: shared services + every registered app.

    /                 app launcher
    /<slug>/          an app's page (apps/<name>/static)
    /<slug>/ws ...    an app's routes
    /shared/          shared frontend code (core/static: audio I/O, worklets)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from apps import APPS
from core.config import ROOT
from core.services import Services

log = logging.getLogger("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = Services()
    await services.start()
    app.state.services = services
    for p in services.settings.problems():
        log.warning("配置问题：%s", p)
    log.info("apps: %s | STT=%s", ", ".join(a.slug for a in APPS), services.stt_label)
    yield
    await services.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/api/apps")
def list_apps() -> list[dict]:
    return [{"slug": a.slug, "title": a.title, "description": a.description, "emoji": a.emoji} for a in APPS]


def _redirect(slug: str):
    return lambda: RedirectResponse(f"/{slug}/")


for spec in APPS:
    app.include_router(spec.router, prefix=f"/{spec.slug}")
    app.add_api_route(f"/{spec.slug}", _redirect(spec.slug), include_in_schema=False)
    app.mount(f"/{spec.slug}", StaticFiles(directory=spec.static_dir, html=True), name=spec.slug)

app.mount("/shared", StaticFiles(directory=ROOT / "core" / "static"), name="shared")
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="home")
