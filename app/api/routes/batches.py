"""Endpoints de Batch (Fase 7A): envelope antes del match.

Un batch agrupa N pre_cortes + 1 flash mensual en staging. Permite CRUD
mientras `status == 'draft'`, y luego valida colisiones + coherencia del
periodo del flash antes de dispararse el match (via n8n o directamente).

Endpoints (16):

CRUD del batch:
- POST   /batches                                crea batch draft
- GET    /batches                                lista batches
- GET    /batches/{id}                           detalle con pre_cortes + flash
- PATCH  /batches/{id}                           renombrar / notas
- DELETE /batches/{id}                           eliminar (solo draft/archived/failed)
- POST   /batches/{id}/archive                   archivar

Pre_cortes en el batch:
- POST   /batches/{id}/pre-cortes                sube 1..N pre_cortes al batch
- POST   /batches/{id}/pre-cortes/from-zip       sube ZIP, filtra por regex
- DELETE /batches/{id}/pre-cortes/{carga_id}     quita un pre_corte del batch

Flash del batch:
- POST   /batches/{id}/flash                     sube flash + declara year+month
- DELETE /batches/{id}/flash                     desvincula flash

Preview + ejecucion:
- GET    /batches/{id}/preview                   preview sin persistir
- POST   /batches/{id}/confirm                   pasa a ready_to_match
- POST   /batches/{id}/generate                  persiste runs + genera excels + zip

Descargas:
- GET    /batches/{id}/downloads                 lista archivos generados
- GET    /batches/{id}/downloads/{filename}      stream FileResponse
"""
from __future__ import annotations

import io
import logging
import re
import sqlite3
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app.api import storage
from app.api.dependencies import db_connection
from app.api.security import AuthUser, current_user, ensure_owner_access, require_permission
from app.api.routes.files import _process_upload
from app.api.schemas import (
    BatchDetailResponse,
    BatchPreCorteItem,
    BatchSummary,
    CreateBatchRequest,
    DiaColision,
    DownloadItem,
    DownloadsResponse,
    FlashInfo,
    GenerateResponse,
    PatchBatchRequest,
    PreviewDia,
    PreviewResponse,
    ZipUploadIgnorado,
    ZipUploadResponse,
)
from app.config import (
    FILENAME_PRE_CORTE_REGEX,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES_PER_REQUEST,
    MAX_ZIP_COMPRESSION_RATIO,
    MAX_ZIP_ENTRIES,
    MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES,
    ONEDRIVE_EXPORT_DIR,
)
from app.core import batches as bcore
from app.core.aggregator import aggregate_flash
from app.core.batches import BatchError, validar_flash_periodo
from app.core.date_extractor import extract_production_date_verbose
from app.core.db import persist_run
from app.core.exporters import export_batch_completo
from app.core.matcher import match_by_material
from app.core.storage_adapter import BUCKET_OUTPUTS, get_storage

router = APIRouter(prefix="/batches", tags=["batches"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _handle_batch_error(err: BatchError) -> HTTPException:
    return HTTPException(status_code=err.code, detail=str(err))


def _safe_filename(raw: str | None, fallback: str) -> str:
    base = Path(raw or fallback).name.strip()
    return base or fallback


def _assert_batch_read_access(conn: sqlite3.Connection, batch_id: str, user: AuthUser) -> None:
    row = conn.execute("SELECT owner_user_id FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Batch {batch_id} no encontrado")
    ensure_owner_access(
        user,
        owner_user_id=row["owner_user_id"],
        read_all_permission="batches:read:all",
        read_own_permission="batches:read:own",
    )


def _assert_batch_write_access(conn: sqlite3.Connection, batch_id: str, user: AuthUser) -> None:
    if not user.has_permission("batches:write"):
        raise HTTPException(status_code=403, detail="Permiso insuficiente: batches:write")
    _assert_batch_read_access(conn, batch_id, user)


def _assert_batch_download_access(conn: sqlite3.Connection, batch_id: str, user: AuthUser) -> None:
    row = conn.execute("SELECT owner_user_id FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Batch {batch_id} no encontrado")
    ensure_owner_access(
        user,
        owner_user_id=row["owner_user_id"],
        read_all_permission="download:all",
        read_own_permission="download:own",
    )


def _batch_summary(conn: sqlite3.Connection, batch_id: str) -> BatchSummary:
    try:
        info = bcore.resumen_estado_batch(conn, batch_id)
    except BatchError as err:
        raise _handle_batch_error(err) from err
    flash = None
    if info.get("flash"):
        flash = FlashInfo(**info["flash"])
    return BatchSummary(
        id=info["id"],
        status=info["status"],
        nombre=info.get("nombre"),
        notas=info.get("notas"),
        created_at=info.get("created_at"),
        updated_at=info.get("updated_at"),
        num_pre_cortes=info["num_pre_cortes"],
        flash=flash,
        output_dir=info.get("output_dir"),
    )


def _batch_output_dir(batch_id: str) -> Path:
    p = Path(ONEDRIVE_EXPORT_DIR) / f"batch_{batch_id}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mirror_outputs_to_storage(batch_id: str, out: dict) -> None:
    """Sube consolidado + dailies + zip al Storage adapter activo.

    Con `LocalStorage`: es un no-op efectivo (copia bytes a `data/outputs/`
    junto al output_dir del batch — redundante pero inocuo).
    Con `SupabaseStorage`: sube los blobs al bucket privado. Los downloads
    subsiguientes retornaran signed URLs de Supabase.

    Silencioso si el adapter falla (log warning, no interrumpe el flujo
    principal): el batch queda como `matched` con archivos locales
    servibles. Fase 7E se completa cuando la SECRET key este disponible
    y podamos validar la subida real.
    """
    try:
        storage_adapter = get_storage()
    except Exception as exc:
        logger.warning("No se pudo inicializar adapter de storage: %s", exc)
        return

    def _upload(path: Path) -> None:
        if not path or not path.exists():
            return
        key = f"batch_{batch_id}/{path.name}"
        try:
            storage_adapter.put(BUCKET_OUTPUTS, key, path.read_bytes())
        except Exception as exc:
            logger.warning("Fallo mirror a storage (%s): %s", key, exc)

    consolidado = out.get("consolidado")
    if isinstance(consolidado, Path):
        _upload(consolidado)
    for daily in out.get("dailies", []):
        if isinstance(daily, Path):
            _upload(daily)
    zip_path = out.get("zip")
    if isinstance(zip_path, Path):
        _upload(zip_path)


# ---------------------------------------------------------------------------
# CRUD del batch
# ---------------------------------------------------------------------------
@router.post("", response_model=BatchSummary, status_code=201)
def create_batch(
    req: CreateBatchRequest,
    user: AuthUser = Depends(require_permission("batches:write")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchSummary:
    batch_id = bcore.create_batch(
        conn,
        nombre=req.nombre,
        notas=req.notas,
        owner_user_id=user.user_id,
    )
    return _batch_summary(conn, batch_id)


@router.get("", response_model=list[BatchSummary])
def list_batches(
    status: str | None = None,
    limit: int = 50,
    include_archived: bool = False,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> list[BatchSummary]:
    typed_status = status  # type: ignore[assignment]
    owner_filter: int | None = None
    if user.has_permission("batches:read:all"):
        owner_filter = None
    elif user.has_permission("batches:read:own"):
        owner_filter = user.user_id
    else:
        raise HTTPException(status_code=403, detail="Permiso insuficiente para listar batches")
    rows = bcore.list_batches(
        conn,
        status=typed_status,  # type: ignore[arg-type]
        limit=limit,
        include_archived=include_archived,
        owner_user_id=owner_filter,
    )
    return [_batch_summary(conn, r["id"]) for r in rows]


@router.get("/{batch_id}", response_model=BatchDetailResponse)
def get_batch_detail(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchDetailResponse:
    _assert_batch_read_access(conn, batch_id, user)
    summary = _batch_summary(conn, batch_id)
    pre_rows = bcore.list_pre_cortes(conn, batch_id)
    items = [BatchPreCorteItem(**r) for r in pre_rows]
    return BatchDetailResponse(**summary.model_dump(), pre_cortes=items)


@router.patch("/{batch_id}", response_model=BatchSummary)
def patch_batch(
    batch_id: str,
    req: PatchBatchRequest,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchSummary:
    _assert_batch_write_access(conn, batch_id, user)
    try:
        bcore.update_batch(conn, batch_id, nombre=req.nombre, notas=req.notas)
    except BatchError as err:
        raise _handle_batch_error(err) from err
    return _batch_summary(conn, batch_id)


@router.delete("/{batch_id}", status_code=204)
def delete_batch(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> None:
    _assert_batch_write_access(conn, batch_id, user)
    try:
        bcore.delete_batch(conn, batch_id)
    except BatchError as err:
        raise _handle_batch_error(err) from err


@router.post("/{batch_id}/archive", response_model=BatchSummary)
def archive_batch(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchSummary:
    _assert_batch_write_access(conn, batch_id, user)
    try:
        bcore.archive_batch(conn, batch_id)
    except BatchError as err:
        raise _handle_batch_error(err) from err
    return _batch_summary(conn, batch_id)


# ---------------------------------------------------------------------------
# Pre-cortes en el batch
# ---------------------------------------------------------------------------
@router.post("/{batch_id}/pre-cortes", response_model=BatchDetailResponse)
def upload_pre_cortes_to_batch(
    batch_id: str,
    files: list[UploadFile] = File(...),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchDetailResponse:
    """Sube 1..N pre_cortes al batch. Cada archivo pasa por el mismo
    procesamiento del endpoint global `/files/upload-pre-corte`, y despues
    se linkea al batch.
    """
    _assert_batch_write_access(conn, batch_id, user)
    if len(files) > MAX_UPLOAD_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"Maximo {MAX_UPLOAD_FILES_PER_REQUEST} archivos por solicitud",
        )
    try:
        bcore._require_editable(bcore._require_batch(conn, batch_id))
    except BatchError as err:
        raise _handle_batch_error(err) from err

    for f in files:
        resp = _process_upload(
            "pre_corte",
            f,
            conn,
            uploaded_by_user_id=user.user_id,
        )
        try:
            bcore.add_pre_corte(conn, batch_id, resp.carga_id)
        except BatchError as err:
            raise _handle_batch_error(err) from err
    return get_batch_detail(batch_id=batch_id, user=user, conn=conn)


@router.post("/{batch_id}/pre-cortes/from-zip", response_model=ZipUploadResponse)
def upload_pre_cortes_from_zip(
    batch_id: str,
    file: UploadFile = File(...),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> ZipUploadResponse:
    """Sube un `.zip`, filtra los `.xlsx` que matcheen `FILENAME_PRE_CORTE_REGEX`,
    los procesa como pre_cortes y los linkea al batch. Retorna procesados +
    ignorados con motivo.
    """
    _assert_batch_write_access(conn, batch_id, user)
    try:
        bcore._require_editable(bcore._require_batch(conn, batch_id))
    except BatchError as err:
        raise _handle_batch_error(err) from err

    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(415, f"Se esperaba .zip, recibido '{file.filename}'")

    regex = re.compile(FILENAME_PRE_CORTE_REGEX, flags=re.IGNORECASE)
    procesados = []
    ignorados = []
    contenido = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(contenido) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"ZIP supera el limite permitido ({MAX_UPLOAD_BYTES} bytes)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile as exc:
        raise HTTPException(422, f"ZIP invalido: {exc}") from exc

    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise HTTPException(413, f"ZIP excede el maximo de entradas ({MAX_ZIP_ENTRIES})")
    total_uncompressed = 0

    for info in infos:
        name = info.filename
        base = _safe_filename(name, "")
        if not base:
            continue
        if info.is_dir():
            continue
        if info.file_size > MAX_UPLOAD_BYTES:
            ignorados.append(
                ZipUploadIgnorado(
                    filename=base,
                    motivo=f"tamano descomprimido excede limite ({MAX_UPLOAD_BYTES} bytes)",
                )
            )
            continue
        total_uncompressed += int(info.file_size)
        if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES:
            raise HTTPException(
                413,
                f"ZIP excede el total descomprimido permitido ({MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES} bytes)",
            )
        if info.compress_size > 0:
            ratio = float(info.file_size) / float(info.compress_size)
            if ratio > MAX_ZIP_COMPRESSION_RATIO:
                ignorados.append(
                    ZipUploadIgnorado(
                        filename=base,
                        motivo="entrada sospechosa (ratio de compresion excesivo)",
                    )
                )
                continue
        if base.startswith(".") or "__MACOSX" in name:
            continue
        ext = Path(base).suffix.lower()
        if ext not in {".xlsx", ".xlsm"}:
            ignorados.append(ZipUploadIgnorado(
                filename=base, motivo=f"extension '{ext}' no permitida"
            ))
            continue
        if not regex.search(base):
            ignorados.append(ZipUploadIgnorado(
                filename=base,
                motivo="nombre no matchea 'PRE CORTE dd.mm.yyyy'",
            ))
            continue

        data = zf.read(name)
        # Reusamos _process_upload envolviendo en un UploadFile-like.
        fake = _FakeUpload(base, data)
        try:
            resp = _process_upload(
                "pre_corte",
                fake,
                conn,
                uploaded_by_user_id=user.user_id,
            )  # type: ignore[arg-type]
            bcore.add_pre_corte(conn, batch_id, resp.carga_id)
            procesados.append(resp)
        except HTTPException as exc:
            ignorados.append(ZipUploadIgnorado(filename=base, motivo=str(exc.detail)))
        except BatchError as exc:
            ignorados.append(ZipUploadIgnorado(filename=base, motivo=str(exc)))
    return ZipUploadResponse(procesados=procesados, ignorados=ignorados)


class _FakeUpload:
    """Emula UploadFile para reusar `_process_upload` con bytes en memoria."""

    def __init__(self, filename: str, data: bytes) -> None:
        self.filename = filename
        self.file = io.BytesIO(data)


@router.delete("/{batch_id}/pre-cortes/{pre_corte_carga_id}", response_model=BatchDetailResponse)
def remove_pre_corte_from_batch(
    batch_id: str,
    pre_corte_carga_id: int,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchDetailResponse:
    _assert_batch_write_access(conn, batch_id, user)
    try:
        borrado = bcore.remove_pre_corte(conn, batch_id, pre_corte_carga_id)
    except BatchError as err:
        raise _handle_batch_error(err) from err
    if not borrado:
        raise HTTPException(
            404,
            f"Pre_corte {pre_corte_carga_id} no estaba en el batch {batch_id}",
        )
    return get_batch_detail(batch_id=batch_id, user=user, conn=conn)


# ---------------------------------------------------------------------------
# Flash del batch
# ---------------------------------------------------------------------------
@router.post("/{batch_id}/flash", response_model=BatchDetailResponse)
def upload_flash_to_batch(
    batch_id: str,
    year: int,
    month: int,
    file: UploadFile = File(...),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchDetailResponse:
    """Sube el flash mensual + declara el periodo (year/month).

    Valida que el flash contenga facturas del periodo declarado. Si no
    coincide, retorna 422 con el rango real de fechas del flash.
    """
    _assert_batch_write_access(conn, batch_id, user)
    try:
        bcore._require_editable(bcore._require_batch(conn, batch_id))
    except BatchError as err:
        raise _handle_batch_error(err) from err

    resp = _process_upload(
        "flash",
        file,
        conn,
        uploaded_by_user_id=user.user_id,
    )

    df = storage.load_parsed_df(resp.carga_id)
    ok, mensajes = validar_flash_periodo(df, year, month)
    if not ok:
        raise HTTPException(422, {
            "detail": "El flash no cuadra con el periodo declarado",
            "mensajes": mensajes,
        })
    try:
        bcore.attach_flash(conn, batch_id, resp.carga_id, year, month)
    except BatchError as err:
        raise _handle_batch_error(err) from err
    return get_batch_detail(batch_id=batch_id, user=user, conn=conn)


@router.delete("/{batch_id}/flash", response_model=BatchDetailResponse)
def detach_flash_from_batch(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchDetailResponse:
    _assert_batch_write_access(conn, batch_id, user)
    try:
        bcore.detach_flash(conn, batch_id)
    except BatchError as err:
        raise _handle_batch_error(err) from err
    return get_batch_detail(batch_id=batch_id, user=user, conn=conn)


# ---------------------------------------------------------------------------
# Preview + confirm + generate
# ---------------------------------------------------------------------------
@router.get("/{batch_id}/preview", response_model=PreviewResponse)
def preview_batch(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> PreviewResponse:
    """Ejecuta match en memoria sin persistir. Devuelve una fila por dia +
    colisiones detectadas + fechas no laborales saltadas + validacion del
    periodo del flash. `listo_para_confirmar` es el AND de todos.
    """
    _assert_batch_read_access(conn, batch_id, user)
    b = bcore.get_batch(conn, batch_id)
    if not b:
        raise HTTPException(404, f"Batch {batch_id} no encontrado")

    pre_cortes = bcore.list_pre_cortes(conn, batch_id)
    flash_carga_id = b.get("flash_carga_id")
    year = b.get("flash_periodo_year")
    month = b.get("flash_periodo_month")

    colisiones_raw = bcore.detect_colisiones(conn, batch_id)
    colisiones = [DiaColision(**c) for c in colisiones_raw]

    # Fechas no laborales saltadas: se calcula a partir de los filenames
    # aunque los datos ya estan en la BD, para exponer el motivo al usuario.
    saltos: list[dict[str, Any]] = []
    for pc in pre_cortes:
        try:
            _, dias_saltados, motivos = extract_production_date_verbose(pc["filename"])
        except Exception:
            continue
        if dias_saltados > 0:
            saltos.append({
                "filename": pc["filename"],
                "fecha_produccion_resuelta": pc["fecha_produccion"],
                "dias_saltados": dias_saltados,
                "motivos": motivos,
            })

    flash_ok = flash_carga_id is not None
    flash_mensajes: list[str] = []
    dias: list[PreviewDia] = []

    if not pre_cortes:
        flash_mensajes.append("El batch no tiene pre_cortes.")
    if flash_carga_id is None:
        flash_mensajes.append("El batch no tiene flash asociado.")
    else:
        try:
            flash_df = storage.load_parsed_df(flash_carga_id)
        except FileNotFoundError:
            flash_ok = False
            flash_mensajes.append("Cache del flash no disponible; vuelve a subirlo.")
            flash_df = pd.DataFrame()
        if not flash_df.empty and year and month:
            ok, msgs = validar_flash_periodo(flash_df, year, month)
            flash_ok = flash_ok and ok
            flash_mensajes.extend(msgs)

        # Match en memoria por cada pre_corte del batch
        for pc in pre_cortes:
            try:
                pre_df = storage.load_parsed_df(pc["carga_id"])
            except FileNotFoundError:
                flash_mensajes.append(
                    f"Cache del pre_corte {pc['filename']} no disponible."
                )
                continue
            fecha_prod = pd.to_datetime(pc["fecha_produccion"]).date()
            agg = aggregate_flash(flash_df, fecha_prod)
            result = match_by_material(pre_df, agg, fecha_prod)
            plan = float(result.matched["notificado_unidades"].sum()) if not result.matched.empty else 0.0
            real = float(result.matched["real_unidades_flash"].sum()) if not result.matched.empty else 0.0
            cumpl = (real / plan) if plan else 0.0
            dias.append(PreviewDia(
                fecha_produccion=fecha_prod,
                materiales_matched=len(result.matched),
                materiales_solo_pre=len(result.solo_pre_corte),
                materiales_solo_flash=len(result.solo_flash),
                plan_total=int(round(plan)),
                real_total=int(round(real)),
                delta_total=int(round(real - plan)),
                cumplimiento_pct=cumpl,
            ))

    listo = (
        len(pre_cortes) > 0
        and flash_ok
        and not colisiones
    )

    return PreviewResponse(
        batch_id=batch_id,
        dias=sorted(dias, key=lambda d: d.fecha_produccion),
        colisiones=colisiones,
        fechas_no_laborales_saltadas=saltos,
        flash_periodo_ok=flash_ok,
        flash_periodo_mensajes=flash_mensajes,
        listo_para_confirmar=listo,
    )


@router.post("/{batch_id}/confirm", response_model=BatchSummary)
def confirm_batch(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchSummary:
    """Pasa el batch de `draft` a `ready_to_match`. Bloquea si hay colisiones
    o el flash no cuadra con el periodo declarado.
    """
    _assert_batch_write_access(conn, batch_id, user)
    b = bcore.get_batch(conn, batch_id)
    if not b:
        raise HTTPException(404, f"Batch {batch_id} no encontrado")
    if b["status"] != "draft":
        raise HTTPException(
            409, f"Batch en estado '{b['status']}': solo 'draft' puede confirmarse"
        )
    prev = preview_batch(batch_id, user, conn)
    if prev.colisiones:
        raise HTTPException(409, {
            "detail": "Hay colisiones de fecha_produccion; eliminar duplicados antes de confirmar",
            "colisiones": [c.model_dump(mode="json") for c in prev.colisiones],
        })
    if not prev.listo_para_confirmar:
        raise HTTPException(422, {
            "detail": "El batch no esta listo para confirmar",
            "mensajes": prev.flash_periodo_mensajes,
        })
    try:
        bcore.set_status(conn, batch_id, "ready_to_match")
    except BatchError as err:
        raise _handle_batch_error(err) from err
    return _batch_summary(conn, batch_id)


@router.post("/{batch_id}/generate", response_model=GenerateResponse)
def generate_batch(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> GenerateResponse:
    """Persiste el match de cada pre_corte contra el flash y genera todos
    los excels + zip. Cambia estado a `matched`.

    Idempotente: si el batch ya esta `matched`, retorna los mismos archivos
    sin volver a persistir (no duplica cruce gracias a los UNIQUE de la BD).
    """
    _assert_batch_write_access(conn, batch_id, user)
    b = bcore.get_batch(conn, batch_id)
    if not b:
        raise HTTPException(404, f"Batch {batch_id} no encontrado")
    if b["status"] not in ("ready_to_match", "matched", "failed"):
        raise HTTPException(
            409,
            f"Batch en estado '{b['status']}': se requiere 'ready_to_match' para generar",
        )

    pre_cortes = bcore.list_pre_cortes(conn, batch_id)
    if not pre_cortes:
        raise HTTPException(422, "Batch sin pre_cortes")
    if not b.get("flash_carga_id"):
        raise HTTPException(422, "Batch sin flash")

    bcore.set_status(conn, batch_id, "matching")
    try:
        flash_carga = conn.execute(
            "SELECT * FROM cargas WHERE id = ?", (b["flash_carga_id"],)
        ).fetchone()
        flash_meta: dict[str, Any] = {
            "filename": flash_carga["filename"],
            "tipo": "flash",
            "hash_sha256": flash_carga["hash_sha256"],
            "num_filas_original": flash_carga["num_filas_original"],
            "num_filas_procesadas": flash_carga["num_filas_procesadas"],
        }
        flash_df = storage.load_parsed_df(b["flash_carga_id"])

        fechas_procesadas: list[date] = []
        for pc in pre_cortes:
            pre_df = storage.load_parsed_df(pc["carga_id"])
            pre_carga = conn.execute(
                "SELECT * FROM cargas WHERE id = ?", (pc["carga_id"],)
            ).fetchone()
            pre_meta: dict[str, Any] = {
                "filename": pre_carga["filename"],
                "tipo": "pre_corte",
                "hash_sha256": pre_carga["hash_sha256"],
                "num_filas_original": pre_carga["num_filas_original"],
                "num_filas_procesadas": pre_carga["num_filas_procesadas"],
            }
            fecha_arch = pd.to_datetime(pre_carga["fecha_archivo"]).date()
            fecha_prod = pd.to_datetime(pre_carga["fecha_produccion"]).date()
            agg = aggregate_flash(flash_df, fecha_prod)
            result = match_by_material(pre_df, agg, fecha_prod)
            persist_run(
                conn,
                pre_corte_meta=pre_meta,
                pre_corte_df=pre_df,
                flash_meta=flash_meta,
                flash_agregado_df=agg,
                match_result=result,
                fecha_archivo=fecha_arch,
            )
            fechas_procesadas.append(fecha_prod)

        desde = min(fechas_procesadas)
        hasta = max(fechas_procesadas)
        output_dir = _batch_output_dir(batch_id)

        # Limpiar output anterior (regenera desde cero)
        for old in output_dir.glob("*.*"):
            old.unlink()

        out = export_batch_completo(desde, hasta, output_dir, conn=conn)

        # Fase 7E: si Supabase Storage esta activo, replicar outputs al
        # bucket para descarga con signed URLs. En LocalStorage es no-op
        # a efectos practicos (los archivos ya estan en disco local via
        # el output_dir del batch). El adapter singleton se resuelve por
        # env vars en runtime, sin cambios de codigo.
        _mirror_outputs_to_storage(batch_id, out)

        bcore.set_output_dir(conn, batch_id, str(output_dir))
        bcore.set_status(conn, batch_id, "matched")
    except Exception:
        bcore.set_status(conn, batch_id, "failed")
        raise

    return GenerateResponse(
        batch_id=batch_id,
        consolidado_filename=out["consolidado"].name,
        dailies_filenames=[d.name for d in out["dailies"]],
        zip_filename=out["zip"].name,
        fechas_procesadas=list(out["fechas_procesadas"]),
        fechas_sin_datos_en_rango=list(out["fechas_sin_datos_en_rango"]),
    )


# ---------------------------------------------------------------------------
# Descargas
# ---------------------------------------------------------------------------
def _classify(filename: str) -> str:
    if filename.endswith(".zip"):
        return "zip"
    if filename.startswith("cumplimiento_consolidado_"):
        return "consolidado"
    return "daily"


@router.get("/{batch_id}/downloads", response_model=DownloadsResponse)
def list_downloads(
    batch_id: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> DownloadsResponse:
    _assert_batch_download_access(conn, batch_id, user)
    b = bcore.get_batch(conn, batch_id)
    if not b:
        raise HTTPException(404, f"Batch {batch_id} no encontrado")
    if not b.get("output_dir"):
        raise HTTPException(404, "Este batch no tiene archivos generados aun")
    output_dir = Path(b["output_dir"])
    if not output_dir.exists():
        raise HTTPException(404, f"Directorio '{output_dir}' no existe")

    items: list[DownloadItem] = []
    for f in sorted(output_dir.iterdir()):
        if f.is_file():
            items.append(DownloadItem(
                filename=f.name,
                size_bytes=f.stat().st_size,
                kind=_classify(f.name),  # type: ignore[arg-type]
            ))
    return DownloadsResponse(
        batch_id=batch_id,
        output_dir=str(output_dir),
        items=items,
    )


@router.get("/{batch_id}/downloads/{filename}")
def download_file(
    batch_id: str,
    filename: str,
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
):
    """Descarga un archivo del batch.

    - Si el `StorageAdapter` activo es `SupabaseStorage` y el archivo esta
      en el bucket, retorna un HTTP 307 Redirect a la signed URL (24h de
      expiracion). El navegador descarga directamente de Supabase, no
      pasa por el backend -> mejor throughput + escalabilidad.
    - Si no (LocalStorage o archivo no encontrado en Supabase), sirve el
      `FileResponse` del disco local con proteccion contra path traversal.
    """
    _assert_batch_download_access(conn, batch_id, user)
    b = bcore.get_batch(conn, batch_id)
    if not b:
        raise HTTPException(404, f"Batch {batch_id} no encontrado")
    if not b.get("output_dir"):
        raise HTTPException(404, "Batch sin archivos generados")

    # Intentar Supabase primero si el adapter esta activo.
    try:
        adapter = get_storage()
        if adapter.backend_name == "supabase":
            key = f"batch_{batch_id}/{filename}"
            if adapter.exists(BUCKET_OUTPUTS, key):
                signed = adapter.public_url(BUCKET_OUTPUTS, key, expires_in=86400)
                if signed:
                    return RedirectResponse(url=signed, status_code=307)
    except Exception as exc:
        # Cualquier error de Supabase -> fallback local.
        logger.warning("Fallo descarga via Supabase, usando disco local: %s", exc)

    output_dir = Path(b["output_dir"]).resolve()
    target = (output_dir / filename).resolve()
    # Path traversal protection.
    try:
        target.relative_to(output_dir)
    except ValueError:
        raise HTTPException(400, "Path traversal detectado en filename")
    if not target.exists():
        raise HTTPException(404, f"Archivo '{filename}' no existe en el batch")
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if filename.endswith(".xlsx") else "application/zip"
    )
    return FileResponse(path=target, media_type=media, filename=filename)
