"""FastAPI application entrypoint.

Levanta la API en `http://127.0.0.1:8000` con Swagger en `/docs`.

Uso local:
    uvicorn app.api.main:app --reload

Uso en produccion (batch en la maquina del analista):
    uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.rate_limit import is_request_allowed
from app.api.routes import auth
from app.api.routes import (
    batches,
    calendario,
    catalog,
    catalogo,
    exports,
    files,
    pipeline,
    profiles,
    runs,
)
from app.api.security import require_auth, require_permission
from app.api.schemas import HealthResponse
from app.config import (
    API_CORS_HEADERS,
    API_CORS_METHODS,
    API_CORS_ORIGINS,
    ENABLE_API_DOCS,
    IS_PRODUCTION,
    security_config_issues,
)
from app.core.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # El gate de seguridad aborta el arranque en produccion si la config es
    # insegura. Bajo pytest NO se aborta (los TestClient levantan la app con
    # config de prueba); security_config_issues() se sigue validando por
    # separado en test_config.py.
    issues = security_config_issues()
    if issues and "PYTEST_VERSION" not in os.environ:
        raise RuntimeError(
            "Configuracion insegura detectada:\n- " + "\n- ".join(issues)
        )
    init_db()
    yield


app = FastAPI(
    title="PRE CORTE vs FLASH API",
    description=(
        "Backend HTTP para el pipeline de comparacion PRE CORTE vs FLASH.\n\n"
        "Cada endpoint del pipeline es atomico e invocable individualmente por "
        "un orquestador externo (Power Automate) que mantiene el estado del "
        "run en SQLite."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=API_CORS_METHODS,
    allow_headers=API_CORS_HEADERS,
)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    """Landing JSON con links utiles. Evita 502 cuando alguien pega la URL raiz."""
    out = {
        "service": "NutriAvicola - PRE CORTE vs FLASH API",
        "version": __version__,
        "health": "/health",
    }
    if ENABLE_API_DOCS:
        out["docs"] = "/docs"
        out["openapi"] = "/openapi.json"
    return out


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", version=__version__, now=datetime.now(timezone.utc)
    )


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    if not is_request_allowed(request):
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Demasiadas solicitudes; intenta de nuevo en unos minutos."
            },
        )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-XSS-Protection", "0")
    if IS_PRODUCTION:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    if request.url.path.startswith(
        ("/profiles", "/batches", "/runs", "/pipeline", "/catalog", "/kpis", "/files")
    ):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _include_private_router(router, *deps):
    dependencies = [Depends(require_auth), *deps]
    app.include_router(router, dependencies=dependencies)


app.include_router(auth.router)
_include_private_router(files.router)
_include_private_router(runs.router)
_include_private_router(pipeline.router)
_include_private_router(catalog.router)
_include_private_router(exports.router, Depends(require_permission("download:all")))
_include_private_router(calendario.router)
_include_private_router(batches.router)
_include_private_router(profiles.router)
_include_private_router(catalogo.router)
