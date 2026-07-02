"""Endpoints para inspeccion y actualizacion del catalogo SKU."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.api import storage
from app.api.dependencies import db_connection
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


@router.get("", response_model=CatalogListResponse)
def get_catalog(
    referencia: str | None = Query(default=None),
    tipo: str | None = Query(default=None),
    formato: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
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
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict:
    """Sube un archivo `homologacion materiales Nuevo.xlsx` y actualiza el catalogo."""
    filename = file.filename or "homologacion.xlsx"
    ext = Path(filename).suffix.lower()
    if ext not in {".xlsx", ".xlsm"}:
        raise HTTPException(
            status_code=415,
            detail=f"La homologacion debe ser .xlsx (recibido '{ext}')",
        )
    content = file.file.read()
    tmp_path = storage.UPLOADS_DIR / f"_tmp_homolog_{filename}"
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
