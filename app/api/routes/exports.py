"""Endpoints de export final para el equipo de BI (Fase 6).

- `GET /kpis/excel?desde=YYYY-MM-DD&hasta=YYYY-MM-DD`: descarga el
  `cumplimiento_YYYYMMDD_YYYYMMDD.xlsx` con Portada + Resumen +
  Por_Categoria + Detalle_Material + No_Cruzados.

El archivo se genera bajo demanda en `data/onedrive_export/` y se sirve
como `FileResponse`. Si el rango esta vacio (no hay filas en `cruce` para
ese rango) igual se devuelve el archivo con las hojas vacias, para que
el equipo de BI sepa que la consulta corrio.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.api.dependencies import db_connection
from app.config import ONEDRIVE_EXPORT_DIR
from app.core.exporters import export_cumplimiento_xlsx, suggested_export_filename

router = APIRouter(prefix="/kpis", tags=["kpis"])

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@router.get("/excel", summary="Descarga el .xlsx de cumplimiento (Fase 6)")
def get_kpis_excel(
    desde: date = Query(..., description="Fecha inicial (YYYY-MM-DD)"),
    hasta: date = Query(..., description="Fecha final inclusive (YYYY-MM-DD)"),
    conn: sqlite3.Connection = Depends(db_connection),
) -> FileResponse:
    """Genera y sirve el Excel formateado con el KPI de cumplimiento.

    - `desde` <= `hasta`, ambos inclusivos.
    - El archivo se cachea en `data/onedrive_export/`; llamadas subsecuentes
      con el mismo rango sobreescriben la version anterior (siempre refleja
      el estado actual de la DB).
    """
    if hasta < desde:
        raise HTTPException(
            status_code=422,
            detail=(
                f"El parametro 'hasta' ({hasta}) no puede ser menor que "
                f"'desde' ({desde})."
            ),
        )

    filename = suggested_export_filename(desde, hasta)
    dest = Path(ONEDRIVE_EXPORT_DIR) / filename
    export_cumplimiento_xlsx(desde, hasta, dest, conn=conn)

    return FileResponse(
        path=dest,
        media_type=XLSX_MEDIA_TYPE,
        filename=filename,
    )
