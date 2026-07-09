"""Endpoints para inspeccion y actualizacion del catalogo SKU."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.api import storage
from app.api.dependencies import db_connection
from app.api.security import AuthUser, require_permission
from app.api.schemas import (
    CatalogEntry,
    CatalogListResponse,
    ManualMappingRequest,
    ManualMappingResponse,
)
from app.core.sku_catalog import (
    catalog_stats,
    import_from_homologacion,
    list_catalog,
    upsert_entry,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _safe_filename(raw: str | None, fallback: str) -> str:
    base = Path(raw or fallback).name.strip()
    return base or fallback


def _read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Archivo excede el limite permitido")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("", response_model=CatalogListResponse)
def get_catalog(
    referencia: str | None = Query(default=None),
    tipo: str | None = Query(default=None),
    formato: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    _: AuthUser = Depends(require_permission("catalog:read")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> CatalogListResponse:
    """Lista entradas del catalogo con filtros opcionales."""
    stats = catalog_stats(conn)
    rows = list_catalog(
        conn,
        referencia=referencia,
        tipo=tipo,
        formato=formato,
        limit=limit,
    )
    entradas = [CatalogEntry(**r) for r in rows]
    return CatalogListResponse(
        total=stats["total_entradas"],
        por_fuente=stats["por_fuente"],
        entradas=entradas,
    )


@router.post("/manual-mapping", response_model=ManualMappingResponse)
def post_manual_mapping(
    payload: ManualMappingRequest,
    _: AuthUser = Depends(require_permission("catalog:write")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> ManualMappingResponse:
    """Registra un mapping manual (prioridad maxima: sobrescribe otros)."""
    entry_id, es_nueva = upsert_entry(
        conn,
        referencia=payload.referencia,
        tipo=payload.tipo,
        formato=payload.formato,
        unidades_por_empaque=payload.unidades_por_empaque,
        material_sap=payload.material_sap,
        nombre_notificacion=payload.nombre_notificacion,
        fuente="manual",
    )
    conn.commit()
    return ManualMappingResponse(
        id=entry_id,
        es_nueva=es_nueva,
        material_sap=payload.material_sap,
    )


@router.post("/import-homologacion", response_model=dict)
def post_import_homologacion(
    file: UploadFile = File(...),
    _: AuthUser = Depends(require_permission("catalog:write")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict:
    """Sube un archivo `homologacion materiales Nuevo.xlsx` y actualiza el catalogo."""
    from app.config import MAX_UPLOAD_BYTES

    filename = _safe_filename(file.filename, "homologacion.xlsx")
    ext = Path(filename).suffix.lower()
    if ext not in {".xlsx", ".xlsm"}:
        raise HTTPException(
            status_code=415,
            detail=f"La homologacion debe ser .xlsx (recibido '{ext}')",
        )
    content = _read_upload_limited(file, MAX_UPLOAD_BYTES)
    if not content.startswith(b"PK"):
        raise HTTPException(status_code=415, detail="La homologacion no parece un Excel valido")
    tmp_path = storage.UPLOADS_DIR / f"_tmp_homolog_{uuid.uuid4().hex[:8]}_{filename}"
    tmp_path.write_bytes(content)
    try:
        stats = import_from_homologacion(tmp_path, conn)
        conn.commit()
    finally:
        tmp_path.unlink(missing_ok=True)
    stats_out = catalog_stats(conn)
    return {
        "filename": filename,
        "import_stats": stats,
        "catalog_stats": stats_out,
    }
