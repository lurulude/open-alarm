from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.alarm_browser import router as alarm_browser_router
from .api.alarm_controls import router as alarm_controls_router
from .api.engineering_table import router as engineering_table_router
from .api.notifications import router as notification_router
from .api.router import router as api_router
from .db.database import apply_migrations, checkpoint_wal, connect, verify_integrity
from .ha.users import HomeAssistantAdminAuthorizer
from .i18n.catalog import TranslationCatalog
from .runtime.host import RuntimeHost

DEFAULT_DATA_DIR = Path("/data")
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend_dist"
INGRESS_PROXY_IP = "172.30.32.2"
LIVENESS_PATH = "/healthz"
translations = TranslationCatalog()


def database_path() -> Path:
    data_dir = Path(os.environ.get("OPEN_ALARM_DATA_DIR", str(DEFAULT_DATA_DIR)))
    return data_dir / "open_alarm.db"


def _env_enabled(name: str, *, default: bool) -> bool:
    fallback = "true" if default else "false"
    value = os.environ.get(name, fallback).strip().lower()
    return value not in {"0", "false", "no", "off"}


def ingress_source_enforced() -> bool:
    return _env_enabled("OPEN_ALARM_ENFORCE_INGRESS_SOURCE", default=False)


def ingress_source_allowed(host: str | None) -> bool:
    return not ingress_source_enforced() or host == INGRESS_PROXY_IP


def ingress_request_allowed(host: str | None, path: str) -> bool:
    # Supervisor's watchdog must be able to probe process/database liveness
    # directly on the app network. All real UI/API routes remain Ingress-only.
    return path == LIVENESS_PATH or ingress_source_allowed(host)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    apply_migrations(connection)
    verify_integrity(connection)
    runtime_host = RuntimeHost(connection)

    app.state.database = connection
    app.state.runtime_host = runtime_host
    app.state.ha_admin_authorizer = HomeAssistantAdminAuthorizer(cache_ttl_s=60.0)

    await runtime_host.start()
    try:
        yield
    finally:
        await runtime_host.stop()
        try:
            checkpoint_wal(connection, truncate=True)
        finally:
            connection.close()


app = FastAPI(title="Open Alarm", lifespan=lifespan)


@app.middleware("http")
async def require_ingress_proxy(request: Request, call_next):
    client_host = None if request.client is None else request.client.host
    if not ingress_request_allowed(client_host, request.url.path):
        return JSONResponse(
            status_code=403,
            content={"detail": "Open Alarm web access is available through Home Assistant Ingress only"},
        )
    response = await call_next(request)
    if request.url.path in {"/", "/index.html"}:
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(api_router)
app.include_router(alarm_controls_router, prefix="/api")
app.include_router(notification_router)
app.include_router(engineering_table_router)
app.include_router(alarm_browser_router)


@app.get(LIVENESS_PATH, include_in_schema=False)
async def liveness() -> dict[str, str]:
    app.state.database.execute("SELECT 1").fetchone()
    return {"status": "ok"}


@app.get("/api/health")
async def health() -> dict[str, object]:
    app.state.database.execute("SELECT 1").fetchone()
    return {
        "status": "ok",
        "database": "ok",
        "runtime": app.state.runtime_host.status_payload(),
    }


@app.get("/api/runtime/status")
async def runtime_status() -> dict[str, object]:
    return app.state.runtime_host.status_payload()


@app.get("/api/i18n/{locale}")
async def i18n_bundle(locale: str) -> dict[str, object]:
    return translations.bundle(locale)


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
