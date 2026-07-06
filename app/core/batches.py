"""Modelo Batch: envelope antes del match (Fase 7A).

Un `batch` agrupa N pre_cortes diarios + 1 flash mensual en staging antes
de que se dispare el match. Permite CRUD (agregar/quitar pre_cortes,
cambiar flash, renombrar) hasta que el usuario confirma y pasa a
`ready_to_match`.

Estados:
- `draft`          : el usuario esta armando el batch (todas las
                     operaciones CRUD estan permitidas).
- `ready_to_match` : el usuario confirmo el preview. Espera trigger del
                     orquestador (n8n) para pasar a `matching`.
- `matching`       : el orquestador esta ejecutando `export_batch_completo`.
- `matched`        : archivos generados y disponibles para descarga.
- `failed`         : la generacion fallo. Puede volver a `draft` si se
                     corrigen los datos y se reintenta.
- `archived`       : batch retirado de las listas activas (soft delete).

Reglas de validacion:
- Solo se pueden agregar/quitar pre_cortes en estado `draft`.
- Solo se puede cambiar el flash en estado `draft`.
- `confirm()` valida: >= 1 pre_corte, flash presente, flash con periodo
  consistente con las fechas del propio flash, y sin colisiones de
  `fecha_produccion_resuelta`.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any, Literal

STATUS_ORDER = (
    "draft", "ready_to_match", "matching", "matched", "failed", "archived",
)
BatchStatus = Literal[
    "draft", "ready_to_match", "matching", "matched", "failed", "archived"
]

_STATUS_QUE_PERMITE_CRUD = {"draft"}
_STATUS_ELIMINABLES = {"draft", "archived", "failed"}


class BatchError(RuntimeError):
    """Error de negocio de batches (409/422 en API)."""

    def __init__(self, msg: str, code: int = 409):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _touch(conn: sqlite3.Connection, batch_id: str) -> None:
    conn.execute(
        "UPDATE batches SET updated_at = ? WHERE id = ?",
        (_now(), batch_id),
    )


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# CRUD basico
# ---------------------------------------------------------------------------
def create_batch(
    conn: sqlite3.Connection,
    nombre: str | None = None,
    notas: str | None = None,
) -> str:
    """Crea un batch nuevo en estado `draft`. Retorna su id (uuid hex)."""
    batch_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO batches (id, status, nombre, notas)
        VALUES (?, 'draft', ?, ?)
        """,
        (batch_id, nombre, notas),
    )
    conn.commit()
    return batch_id


def get_batch(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    return _row_to_dict(row)


def list_batches(
    conn: sqlite3.Connection,
    status: BatchStatus | None = None,
    limit: int = 50,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    q = "SELECT * FROM batches WHERE 1=1"
    params: list[Any] = []
    if status:
        q += " AND status = ?"
        params.append(status)
    elif not include_archived:
        q += " AND status != 'archived'"
    q += " ORDER BY updated_at DESC, rowid DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def update_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    nombre: str | None = None,
    notas: str | None = None,
) -> dict[str, Any]:
    """Renombrar o agregar notas. Solo en estados no terminales."""
    b = _require_batch(conn, batch_id)
    if b["status"] == "archived":
        raise BatchError(f"El batch {batch_id} esta archivado; no se puede editar")
    fields, values = [], []
    if nombre is not None:
        fields.append("nombre = ?")
        values.append(nombre)
    if notas is not None:
        fields.append("notas = ?")
        values.append(notas)
    if fields:
        fields.append("updated_at = ?")
        values.append(_now())
        values.append(batch_id)
        conn.execute(
            f"UPDATE batches SET {', '.join(fields)} WHERE id = ?", tuple(values)
        )
        conn.commit()
    return _require_batch(conn, batch_id)


def delete_batch(conn: sqlite3.Connection, batch_id: str) -> None:
    """Elimina el batch. Solo permitido en draft/archived/failed.

    Los `batch_pre_cortes` se eliminan en cascada. Las `cargas` reales
    (archivos xlsx subidos) quedan en la BD por trazabilidad; una tarea
    de limpieza aparte las purga si no estan en ningun batch.
    """
    b = _require_batch(conn, batch_id)
    if b["status"] not in _STATUS_ELIMINABLES:
        raise BatchError(
            f"El batch {batch_id} esta en estado '{b['status']}' y no se puede eliminar. "
            f"Estados eliminables: {sorted(_STATUS_ELIMINABLES)}",
            code=409,
        )
    conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    conn.commit()


def archive_batch(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    _require_batch(conn, batch_id)
    conn.execute(
        "UPDATE batches SET status = 'archived', updated_at = ? WHERE id = ?",
        (_now(), batch_id),
    )
    conn.commit()
    return _require_batch(conn, batch_id)


def set_status(
    conn: sqlite3.Connection, batch_id: str, status: BatchStatus
) -> dict[str, Any]:
    _require_batch(conn, batch_id)
    if status not in STATUS_ORDER:
        raise BatchError(f"Status invalido: {status}", code=422)
    conn.execute(
        "UPDATE batches SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), batch_id),
    )
    conn.commit()
    return _require_batch(conn, batch_id)


def set_output_dir(
    conn: sqlite3.Connection, batch_id: str, output_dir: str
) -> None:
    conn.execute(
        "UPDATE batches SET output_dir = ?, updated_at = ? WHERE id = ?",
        (output_dir, _now(), batch_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Pre-cortes en un batch
# ---------------------------------------------------------------------------
def add_pre_corte(
    conn: sqlite3.Connection, batch_id: str, pre_corte_carga_id: int
) -> bool:
    """Linkea un pre_corte al batch. Idempotente (retorna False si ya existia).

    Requiere batch en `draft` y que la carga sea tipo pre_corte.
    """
    b = _require_batch(conn, batch_id)
    _require_editable(b)
    carga = conn.execute(
        "SELECT tipo FROM cargas WHERE id = ?", (pre_corte_carga_id,)
    ).fetchone()
    if not carga:
        raise BatchError(f"Carga {pre_corte_carga_id} no existe", code=404)
    if carga["tipo"] != "pre_corte":
        raise BatchError(
            f"Carga {pre_corte_carga_id} es tipo '{carga['tipo']}', no 'pre_corte'",
            code=422,
        )
    try:
        conn.execute(
            "INSERT INTO batch_pre_cortes (batch_id, pre_corte_carga_id) VALUES (?, ?)",
            (batch_id, pre_corte_carga_id),
        )
    except sqlite3.IntegrityError:
        return False
    _touch(conn, batch_id)
    conn.commit()
    return True


def remove_pre_corte(
    conn: sqlite3.Connection, batch_id: str, pre_corte_carga_id: int
) -> bool:
    """Desliga un pre_corte del batch. Solo en `draft`. Retorna True si borro."""
    b = _require_batch(conn, batch_id)
    _require_editable(b)
    cur = conn.execute(
        "DELETE FROM batch_pre_cortes WHERE batch_id = ? AND pre_corte_carga_id = ?",
        (batch_id, pre_corte_carga_id),
    )
    if cur.rowcount:
        _touch(conn, batch_id)
    conn.commit()
    return bool(cur.rowcount)


def list_pre_cortes(
    conn: sqlite3.Connection, batch_id: str
) -> list[dict[str, Any]]:
    """Lista pre_cortes del batch enriquecidos con metadata de la carga."""
    rows = conn.execute(
        """
        SELECT c.id AS carga_id, c.filename, c.fecha_archivo, c.fecha_produccion,
               c.hash_sha256, c.num_filas_original, c.num_filas_procesadas,
               c.cargado_en, bp.agregado_en
        FROM batch_pre_cortes bp
        JOIN cargas c ON c.id = bp.pre_corte_carga_id
        WHERE bp.batch_id = ?
        ORDER BY c.fecha_produccion ASC, c.id ASC
        """,
        (batch_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Flash del batch
# ---------------------------------------------------------------------------
def attach_flash(
    conn: sqlite3.Connection,
    batch_id: str,
    flash_carga_id: int,
    year: int,
    month: int,
) -> dict[str, Any]:
    """Asocia el flash al batch con su periodo year/month declarado."""
    b = _require_batch(conn, batch_id)
    _require_editable(b)
    carga = conn.execute(
        "SELECT tipo FROM cargas WHERE id = ?", (flash_carga_id,)
    ).fetchone()
    if not carga:
        raise BatchError(f"Carga {flash_carga_id} no existe", code=404)
    if carga["tipo"] != "flash":
        raise BatchError(
            f"Carga {flash_carga_id} es tipo '{carga['tipo']}', no 'flash'", code=422
        )
    if not (1 <= month <= 12):
        raise BatchError(f"Mes invalido: {month} (esperado 1-12)", code=422)
    if year < 2000 or year > 2100:
        raise BatchError(f"Anio fuera de rango razonable: {year}", code=422)
    conn.execute(
        """
        UPDATE batches
           SET flash_carga_id = ?, flash_periodo_year = ?, flash_periodo_month = ?,
               updated_at = ?
         WHERE id = ?
        """,
        (flash_carga_id, year, month, _now(), batch_id),
    )
    conn.commit()
    return _require_batch(conn, batch_id)


def detach_flash(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    b = _require_batch(conn, batch_id)
    _require_editable(b)
    conn.execute(
        """
        UPDATE batches
           SET flash_carga_id = NULL, flash_periodo_year = NULL,
               flash_periodo_month = NULL, updated_at = ?
         WHERE id = ?
        """,
        (_now(), batch_id),
    )
    conn.commit()
    return _require_batch(conn, batch_id)


# ---------------------------------------------------------------------------
# Colisiones y periodo del flash (Fase 6.6 - resuelve pendiente)
# ---------------------------------------------------------------------------
def detect_colisiones(
    conn: sqlite3.Connection, batch_id: str
) -> list[dict[str, Any]]:
    """Detecta pre_cortes con la misma `fecha_produccion` resuelta.

    Retorna [{fecha, pre_corte_carga_ids: [id1, id2, ...]}]. Vacio si no hay.
    """
    rows = conn.execute(
        """
        SELECT c.fecha_produccion, c.id
        FROM batch_pre_cortes bp
        JOIN cargas c ON c.id = bp.pre_corte_carga_id
        WHERE bp.batch_id = ? AND c.fecha_produccion IS NOT NULL
        ORDER BY c.fecha_produccion, c.id
        """,
        (batch_id,),
    ).fetchall()
    por_fecha: dict[str, list[int]] = {}
    for r in rows:
        por_fecha.setdefault(str(r["fecha_produccion"]), []).append(int(r["id"]))
    return [
        {"fecha": f, "pre_corte_carga_ids": ids}
        for f, ids in por_fecha.items()
        if len(ids) > 1
    ]


def validar_flash_periodo(
    flash_df: Any, year: int, month: int
) -> tuple[bool, list[str]]:
    """Verifica que el flash contenga fechas del `year-month` declarado.

    Retorna `(ok, mensajes)`. Si `ok=False`, `mensajes` explica que no cuadra.
    """
    import pandas as pd

    mensajes: list[str] = []
    if flash_df is None or getattr(flash_df, "empty", True):
        return False, ["El flash no tiene filas parseadas."]
    if "fecha_factura" not in flash_df.columns:
        return False, ["El flash no tiene columna 'fecha_factura'."]

    fechas = pd.to_datetime(flash_df["fecha_factura"], errors="coerce").dropna()
    if fechas.empty:
        return False, ["El flash no tiene fechas validas."]

    fechas_periodo = fechas[(fechas.dt.year == year) & (fechas.dt.month == month)]
    if fechas_periodo.empty:
        rango = f"{fechas.min().date()} a {fechas.max().date()}"
        mensajes.append(
            f"El flash NO contiene facturas del periodo {year}-{month:02d}. "
            f"Rango real: {rango}."
        )
        return False, mensajes

    total = len(fechas)
    del_mes = len(fechas_periodo)
    pct = del_mes / total * 100 if total else 0
    if pct < 50:
        mensajes.append(
            f"Solo el {pct:.0f}% de las facturas del flash son del periodo "
            f"{year}-{month:02d} ({del_mes} de {total}). ¿Estas seguro del mes?"
        )
    return True, mensajes


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _require_batch(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any]:
    b = get_batch(conn, batch_id)
    if not b:
        raise BatchError(f"Batch {batch_id} no encontrado", code=404)
    return b


def _require_editable(batch: dict[str, Any]) -> None:
    if batch["status"] not in _STATUS_QUE_PERMITE_CRUD:
        raise BatchError(
            f"El batch {batch['id']} esta en estado '{batch['status']}'. "
            f"Solo estados {sorted(_STATUS_QUE_PERMITE_CRUD)} permiten modificacion.",
            code=409,
        )


def resumen_estado_batch(
    conn: sqlite3.Connection, batch_id: str
) -> dict[str, Any]:
    """Snapshot legible para listados: cuenta pre_cortes + datos del flash."""
    b = _require_batch(conn, batch_id)
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM batch_pre_cortes WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()["n"]
    flash_info = None
    if b["flash_carga_id"]:
        row = conn.execute(
            "SELECT filename, num_filas_original FROM cargas WHERE id = ?",
            (b["flash_carga_id"],),
        ).fetchone()
        if row:
            flash_info = {
                "flash_carga_id": b["flash_carga_id"],
                "filename": row["filename"],
                "periodo_year": b["flash_periodo_year"],
                "periodo_month": b["flash_periodo_month"],
                "num_filas_original": row["num_filas_original"],
            }
    return {**b, "num_pre_cortes": n, "flash": flash_info}
