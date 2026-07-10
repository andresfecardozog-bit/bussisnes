"""Trabajos en segundo plano para operaciones pesadas.

Las operaciones que tardan mucho (entrevista de agentes ~2 min, generacion de
entregables, cruces consolidados) no pueden responder en una sola peticion HTTP
cuando el backend esta detras de un tunel/proxy con limite de tiempo (Cloudflare
corta a ~100s). La solucion: la peticion arranca el trabajo en un hilo y
responde al instante con un `job_id`; el frontend consulta `GET /jobs/{id}`
hasta que termina y recibe el mismo payload que antes.

Registro en memoria del proceso (single-worker). Suficiente para el uso actual;
si se escala a varios workers habria que mover el estado a la DB.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from app.api.security import AuthUser, current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_MAX_JOBS = 500
_TTL_SECONDS = 3600


def _purge_locked() -> None:
    """Elimina trabajos viejos para no crecer sin limite. Bajo _LOCK."""
    if len(_JOBS) <= _MAX_JOBS:
        return
    ahora = time.time()
    viejos = [
        jid for jid, j in _JOBS.items()
        if j["status"] in {"done", "error"} and ahora - j["created_at"] > _TTL_SECONDS
    ]
    for jid in viejos:
        _JOBS.pop(jid, None)


def create_job(kind: str, owner_user_id: int | None) -> str:
    jid = uuid.uuid4().hex
    with _LOCK:
        _purge_locked()
        _JOBS[jid] = {
            "id": jid,
            "kind": kind,
            "status": "running",
            "result": None,
            "error": None,
            "owner_user_id": owner_user_id,
            "created_at": time.time(),
        }
    return jid


def _update(jid: str, **kw: Any) -> None:
    with _LOCK:
        if jid in _JOBS:
            _JOBS[jid].update(kw)


def run_job(jid: str, fn: Callable[[], Any]) -> None:
    """Ejecuta `fn` en un hilo daemon y guarda su resultado/error en el job.

    Las HTTPException se preservan (status + detail) para que el frontend
    reciba el mismo error accionable que en el flujo sincrono.
    """

    def _target() -> None:
        try:
            resultado = fn()
            _update(jid, status="done", result=resultado)
        except HTTPException as exc:
            _update(
                jid,
                status="error",
                error={"status": exc.status_code, "detail": exc.detail},
            )
        except Exception as exc:  # noqa: BLE001
            _update(
                jid,
                status="error",
                error={"status": 500, "detail": f"{type(exc).__name__}: {exc}"},
            )

    threading.Thread(target=_target, daemon=True).start()


def get_job(jid: str) -> dict[str, Any] | None:
    with _LOCK:
        j = _JOBS.get(jid)
        return dict(j) if j else None


@router.get("/{job_id}")
def get_job_status(job_id: str, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado o expirado")
    owner = job.get("owner_user_id")
    if owner is not None and owner != user.user_id and not user.has_permission("profiles:read:all"):
        raise HTTPException(status_code=403, detail="Acceso denegado")
    return {
        "job_id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "result": job["result"],
        "error": job["error"],
    }
