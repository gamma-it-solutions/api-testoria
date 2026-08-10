import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1 import (
    api_keys,
    auth,
    ci_integration,
    defects,
    milestones,
    projects,
    reports,
    tags,
    test_cases,
    test_results,
    test_runs,
    test_suites,
    websocket,
)
from app.api.v1 import (
    files as files_legacy,
)
from app.api.v1.users import roles_router, users_router
from app.config import settings
from app.core import storage
from app.core.email_worker import EmailWorker
from app.core.redis import close_redis

logger = logging.getLogger(__name__)

email_worker = EmailWorker()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        await storage.ensure_bucket()
    except Exception as err:  # noqa: BLE001 — startup best-effort; the first upload will retry
        logger.warning("MinIO bucket ensure failed at startup: %s", err)
    email_worker.start()
    try:
        yield
    finally:
        await email_worker.stop()
        await close_redis()


app = FastAPI(
    title="Testoria API",
    description="Test management platform REST API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(api_keys.router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(roles_router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(test_suites.router, prefix="/api/v1")
app.include_router(test_cases.router, prefix="/api/v1")
app.include_router(tags.router, prefix="/api/v1")
app.include_router(milestones.router, prefix="/api/v1")
app.include_router(test_runs.router, prefix="/api/v1")
app.include_router(test_results.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(ci_integration.router, prefix="/api/v1")
app.include_router(websocket.router, prefix="/api/v1")
app.include_router(defects.router, prefix="/api/v1")
app.include_router(files_legacy.router, prefix="/api/v1")


@app.get("/api/v1/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}
