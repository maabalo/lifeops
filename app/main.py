"""
LifeOps web app: the API (/api/...) and the page (web/), served by one small FastAPI server.

    .venv/bin/uvicorn --factory app.main:create_app --port 8780     # http://127.0.0.1:8780 on this Mac

(create_app is called when the server starts, so importing this file has no side effects.)

API docs (/docs) are switched off: this is a private app, not a public API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, closing
from functools import cache

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import connect, migrate
from . import workflow
from .documents import MAX_FILE_BYTES
from .push import make_sender
from .reminders import Scheduler
from .routers import account, auth, documents, inbox, push, tasks
from .settings import ROOT, load_settings

MAX_JSON_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = MAX_FILE_BYTES + 64 * 1024  # the upload route only: the file plus form overhead
UPLOAD_PATH = "/api/inbox/upload"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; manifest-src 'self'; worker-src 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def create_app() -> FastAPI:
    settings = load_settings()
    with closing(connect(settings)) as conn:
        migrate(conn)
        workflow.fail_interrupted(conn)  # reads cut off by a stop/restart: show them as failed, ready to retry
    @cache
    def push_sender():
        """The real push sender, made on first use (it loads or creates the VAPID key). Tests replace it."""
        return make_sender(settings.data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler = None
        if settings.scheduler_seconds > 0:
            scheduler = Scheduler(settings, lambda sub, payload: app.state.push_sender()(sub, payload), settings.scheduler_seconds)
            scheduler.start()
        yield
        if scheduler is not None:
            scheduler.stop()

    app = FastAPI(title="LifeOps", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.push_sender = push_sender

    @app.middleware("http")
    async def per_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Size limit, one database connection per request, and security headers on every answer."""
        size = request.headers.get("content-length")
        limit = MAX_UPLOAD_BYTES if request.url.path == UPLOAD_PATH else MAX_JSON_BYTES
        if size and size.isdigit() and int(size) > limit:
            return finish(JSONResponse({"detail": "That request is too large."}, status_code=413), request)
        request.state.conn = connect(settings)
        try:
            response = await call_next(request)
        finally:
            request.state.conn.close()
        return finish(response, request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        """Plain-words validation errors (never echo the submitted values back)."""
        problems = [f"{'.'.join(str(p) for p in e['loc'][1:]) or 'request'}: {e['msg']}" for e in error.errors()]
        return JSONResponse({"detail": "Please check: " + "; ".join(problems)}, status_code=422)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    app.include_router(auth.router)
    app.include_router(inbox.router)
    app.include_router(tasks.router)
    app.include_router(documents.router)
    app.include_router(account.router)
    app.include_router(push.router)
    app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
    return app


def finish(response: Response, request: Request) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"  # personal data never sits in a cache
    else:
        response.headers["Cache-Control"] = "no-cache"  # page files: always check for a newer version after an update
    return response

