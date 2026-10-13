from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.auth.router import router as auth_router
from src.chat.router import router as chat_router
from src.config import Environment, settings
from src.database import engine

SHOW_DOCS_ENVIRONMENTS = {Environment.LOCAL, Environment.TESTING, Environment.STAGING}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app_configs: dict[str, Any] = {
    "title": "Dental Clinic Assistant API",
    "version": settings.APP_VERSION,
}
if settings.ENVIRONMENT not in SHOW_DOCS_ENVIRONMENTS:
    app_configs["openapi_url"] = None

app = FastAPI(**app_configs, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    # Required so the browser sends the httpOnly refresh-token cookie.
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=settings.CORS_HEADERS,
)


@app.get("/healthcheck", include_in_schema=False)
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(chat_router)
