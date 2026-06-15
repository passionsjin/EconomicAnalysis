"""FastAPI 앱 — 대시보드 HTML + JSON API + 수집 트리거."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import presenter
from . import pipeline
from . import repository as repo
from .analysis import regime as regime_mod
from .config import CATEGORIES, settings
from .db import init_db
from .logging_setup import logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    pipeline.start_scheduler()
    if settings.collect_on_start:
        logger.info("기동 즉시 수집 1회 트리거(COLLECT_ON_START)")
        pipeline.trigger_async()  # 첫 화면이 비지 않도록 즉시 1회(백그라운드)
    yield
    logger.info("종료 — 스케줄러 중지")
    pipeline.shutdown_scheduler()


app = FastAPI(title="거시경제 분석 대시보드", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(settings.web_dir / "static")), name="static")
templates = Jinja2Templates(directory=str(settings.web_dir / "templates"))


# ─────────────────────────── HTML ───────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    data = presenter.build_dashboard()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "d": data,
        "status": pipeline.get_status(),
        "sched": pipeline.scheduler_health(),
        "categories": CATEGORIES,
        "title": "거시경제 시황 대시보드",
    })


@app.get("/report/{snapshot_id}", response_class=HTMLResponse)
def report(request: Request, snapshot_id: int):
    data = presenter.build_dashboard(snapshot_id)
    if data["empty"]:
        raise HTTPException(404, "해당 스냅샷을 찾을 수 없습니다")
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "d": data,
        "status": pipeline.get_status(),
        "sched": pipeline.scheduler_health(),
        "categories": CATEGORIES,
        "title": f"브리핑 #{snapshot_id}",
        "historical": True,
    })


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    return templates.TemplateResponse("history.html", {
        "request": request,
        "snapshots": repo.list_snapshots(100),
        "title": "브리핑 이력",
    })


# ─────────────────────────── JSON API ───────────────────────────

@app.get("/api/dashboard")
def api_dashboard(snapshot_id: int | None = None):
    return JSONResponse(presenter.build_dashboard(snapshot_id))


@app.get("/api/series/{key}")
def api_series(key: str, points: int = 0, days: int = 0):
    n = points or days or 120  # 구버전 프론트(?days=)도 호환 — 이제 둘 다 '개수' 의미
    return {"key": key, "series": repo.get_series(key, n)}


@app.get("/api/series-batch")
def api_series_batch(keys: str, points: int = 60):
    """여러 키 시계열을 한 번에. keys=콤마구분. 스파크라인 N개 동시요청 방지."""
    klist = [k.strip() for k in keys.split(",") if k.strip()][:60]
    return {"points": points, "series": repo.get_series_batch(klist, points)}


@app.get("/api/correlations")
def api_correlations(window: int = 30):
    window = max(10, min(window, 120))
    return regime_mod.snapshot(window)


@app.get("/api/alerts")
def api_alerts(limit: int = 20):
    return {"events": repo.recent_source_events(limit)}


@app.get("/api/snapshots")
def api_snapshots(limit: int = 50):
    return {"snapshots": repo.list_snapshots(limit)}


@app.get("/api/status")
def api_status():
    snap = repo.latest_snapshot()
    return {
        "pipeline": pipeline.get_status(),
        "scheduler": pipeline.scheduler_health(),
        "latest_snapshot_id": snap["id"] if snap else None,
        "latest_finished": snap["finished_utc"] if snap else None,
    }


@app.post("/api/collect")
def api_collect():
    started = pipeline.trigger_async()
    return {"started": started, "reason": None if started else "이미 수집 진행 중"}


@app.get("/healthz")
def healthz():
    return {"ok": True}
