"""Endpoints de ciclo de vida de runs: batch start, get, approve, reject, list."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import db_connection, get_run_or_404
from app.api.security import AuthUser, current_user, ensure_run_permission
from app.api.schemas import (
    ApproveRequest,
    RejectRequest,
    RunSummary,
    StartBatchRequest,
    StartBatchResponse,
)
from app.core.db import create_run, list_recent_runs, update_run

router = APIRouter(prefix="/runs", tags=["runs"])


def _assert_carga_access(conn: sqlite3.Connection, carga_id: int, user: AuthUser) -> None:
    if user.has_permission("run:execute"):
        return
    row = conn.execute(
        "SELECT uploaded_by_user_id FROM cargas WHERE id = ?",
        (carga_id,),
    ).fetchone()
    owner = row["uploaded_by_user_id"] if row else None
    ensure_run_permission(user, owner_user_id=owner)


def _row_to_summary(d: dict) -> RunSummary:
    return RunSummary(
        id=d["id"],
        parent_run_id=d.get("parent_run_id"),
        status=d["status"],
        current_step=d.get("current_step"),
        fecha_produccion=d.get("fecha_produccion"),
        pre_corte_carga_id=d.get("pre_corte_carga_id"),
        flash_carga_id=d.get("flash_carga_id"),
        started_at=d.get("started_at"),
        ended_at=d.get("ended_at"),
        summary=d.get("summary"),
        notes=d.get("notes"),
    )


@router.post("/start-batch", response_model=StartBatchResponse)
def start_batch(
    req: StartBatchRequest,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> StartBatchResponse:
    """Crea un run maestro + N sub-runs (uno por PRE CORTE contra el mismo FLASH)."""
    _assert_carga_access(conn, req.flash_carga_id, user)
    flash_carga = conn.execute(
        "SELECT id, tipo FROM cargas WHERE id = ?", (req.flash_carga_id,)
    ).fetchone()
    if not flash_carga or flash_carga["tipo"] != "flash":
        raise HTTPException(400, f"flash_carga_id {req.flash_carga_id} invalido o no es flash")

    pre_cargas = conn.execute(
        f"SELECT id, tipo, fecha_produccion FROM cargas "
        f"WHERE id IN ({','.join('?' * len(req.pre_corte_carga_ids))})",
        tuple(req.pre_corte_carga_ids),
    ).fetchall()
    if len(pre_cargas) != len(req.pre_corte_carga_ids):
        encontrados = {r["id"] for r in pre_cargas}
        faltan = set(req.pre_corte_carga_ids) - encontrados
        raise HTTPException(400, f"pre_corte_carga_ids no encontrados: {sorted(faltan)}")
    invalid = [r["id"] for r in pre_cargas if r["tipo"] != "pre_corte"]
    if invalid:
        raise HTTPException(400, f"cargas no son de tipo pre_corte: {invalid}")
    for r in pre_cargas:
        _assert_carga_access(conn, int(r["id"]), user)

    master_id = create_run(
        conn,
        owner_user_id=user.user_id,
        status="running",
        current_step="batch_created",
    )

    sub_run_ids = []
    for pc in pre_cargas:
        sub_id = create_run(
            conn,
            parent_run_id=master_id,
            owner_user_id=user.user_id,
            pre_corte_carga_id=pc["id"],
            flash_carga_id=req.flash_carga_id,
            fecha_produccion=pc["fecha_produccion"],
            status="running",
            current_step="created",
        )
        sub_run_ids.append(sub_id)

    return StartBatchResponse(
        master_run_id=master_id,
        sub_run_ids=sub_run_ids,
        total_sub_runs=len(sub_run_ids),
    )


@router.get("/{run_id}", response_model=RunSummary)
def get_run_endpoint(
    run: dict = Depends(get_run_or_404),
) -> RunSummary:
    return _row_to_summary(run)


@router.get("", response_model=list[RunSummary])
def list_runs(
    limit: int = 30,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> list[RunSummary]:
    rows = list_recent_runs(conn, limit=limit)
    if user.has_permission("run:execute"):
        return [_row_to_summary(r) for r in rows]
    own = [r for r in rows if r.get("owner_user_id") == user.user_id]
    return [_row_to_summary(r) for r in own]


@router.get("/{run_id}/sub-runs", response_model=list[RunSummary])
def list_sub_runs(
    run_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> list[RunSummary]:
    master = conn.execute("SELECT owner_user_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if master is None:
        raise HTTPException(404, f"Run {run_id} no encontrado")
    ensure_run_permission(user, owner_user_id=master["owner_user_id"])
    rows = conn.execute(
        "SELECT * FROM runs WHERE parent_run_id = ? ORDER BY started_at ASC, rowid ASC",
        (run_id,),
    ).fetchall()
    return [_row_to_summary(dict(r)) for r in rows]


@router.post("/{run_id}/approve", response_model=RunSummary)
def approve_run(
    run_id: str,
    req: ApproveRequest,
    run: dict = Depends(get_run_or_404),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> RunSummary:
    ensure_run_permission(user, owner_user_id=run.get("owner_user_id"))
    if run["status"] not in ("awaiting_approval", "running"):
        raise HTTPException(
            409, f"Run en status '{run['status']}' no puede aprobarse"
        )
    update_run(
        conn,
        run_id,
        status="approved",
        current_step="approved",
        notes=f"Aprobado por {req.aprobado_por or 'anonimo'}. {req.comentarios or ''}".strip(),
    )
    return _row_to_summary({**run, "status": "approved", "current_step": "approved"})


@router.post("/{run_id}/reject", response_model=RunSummary)
def reject_run(
    run_id: str,
    req: RejectRequest,
    run: dict = Depends(get_run_or_404),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> RunSummary:
    ensure_run_permission(user, owner_user_id=run.get("owner_user_id"))
    update_run(
        conn,
        run_id,
        status="rejected",
        current_step="rejected",
        notes=f"Rechazado por {req.rechazado_por or 'anonimo'}: {req.motivo}",
        ended=True,
    )
    return _row_to_summary({**run, "status": "rejected", "current_step": "rejected"})
