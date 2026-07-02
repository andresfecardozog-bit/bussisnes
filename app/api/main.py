"""FastAPI application entrypoint.

Levanta la API en `http://127.0.0.1:8000` con Swagger en `/docs`.

Uso local:
    uvicorn app.api.main:app --reload

Uso en produccion (batch en la maquina del analista):
    uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import batches, calendario, catalog, exports, files, pipeline, runs
from app.api.schemas import HealthResponse
from app.config import API_CORS_ORIGINS
from app.core.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", version=__version__, now=datetime.now(timezone.utc)
    )


app.include_router(files.router)
app.include_router(runs.router)
app.include_router(pipeline.router)
app.include_router(catalog.router)
app.include_router(exports.router)
app.include_router(calendario.router)
app.include_router(batches.router)
