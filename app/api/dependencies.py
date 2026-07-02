"""Dependencias reutilizables por los routers de FastAPI."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, HTTPException

from app.config import DB_PATH
from app.core.db import get_conn


def db_connection() -> Iterator[sqlite3.Connection]:
    """Yields una conexion SQLite por request; garantiza cierre."""
    with get_conn(DB_PATH) as conn:
        yield conn


def get_carga_or_404(
    carga_id: int,
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict:
    row = conn.execute("SELECT * FROM cargas WHERE id = ?", (carga_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Carga {carga_id} no encontrada")
    return dict(row)


def get_run_or_404(
    run_id: str,
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict:
    from app.core.db import get_run

    run = get_run(conn, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} no encontrado")
    return run
