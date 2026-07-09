"""Dependencias reutilizables por los routers de FastAPI."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, HTTPException, Request

from app.config import DB_PATH
from app.core.db import get_conn


def db_connection() -> Iterator[sqlite3.Connection]:
    """Yields una conexion SQLite por request; garantiza cierre."""
    with get_conn(DB_PATH) as conn:
        yield conn


def get_carga_or_404(
    carga_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict:
    row = conn.execute("SELECT * FROM cargas WHERE id = ?", (carga_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Carga {carga_id} no encontrada")
    carga = dict(row)
    auth = getattr(request.state, "auth_user", None)
    if auth is not None:
        owner = carga.get("uploaded_by_user_id")
        if owner is not None:
            can_all = auth.has_permission("files:read:all")
            can_own = auth.has_permission("files:read:own") and int(owner) == auth.user_id
            if not (can_all or can_own):
                raise HTTPException(status_code=403, detail="Acceso denegado")
    return carga


def get_run_or_404(
    run_id: str,
    request: Request,
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict:
    from app.core.db import get_run

    run = get_run(conn, run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} no encontrado")
    auth = getattr(request.state, "auth_user", None)
    if auth is not None:
        owner = run.get("owner_user_id")
        can_all = auth.has_permission("run:execute")
        can_own = owner is not None and auth.has_permission("run:execute:own") and int(owner) == auth.user_id
        if not (can_all or can_own):
            raise HTTPException(status_code=403, detail="Acceso denegado")
    return run
