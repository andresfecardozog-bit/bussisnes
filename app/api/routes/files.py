"""Endpoints de upload: recibe multipart, parsea, cachea y crea carga en DB."""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api import storage
from app.api.dependencies import db_connection
from app.api.security import AuthUser, current_user
from app.api.schemas import BatchUploadResponse, FileUploadResponse
from app.config import MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES_PER_REQUEST
from app.core.date_extractor import (
    FilenameDateError,
    extract_file_date,
    extract_production_date_verbose,
)
from app.core.db import get_or_insert_carga
from app.core.loaders import load_flash, load_pre_corte

router = APIRouter(prefix="/files", tags=["files"])


_ALLOWED_EXT_BY_TIPO: dict[str, set[str]] = {
    "pre_corte": {".xlsx", ".xlsm"},
    "flash": {".xlsx", ".xlsm", ".csv"},
}


def _safe_filename(raw: str | None, fallback: str = "archivo") -> str:
    base = Path(raw or fallback).name.strip()
    return base or fallback


def _read_upload_limited(upload: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Archivo supera el limite permitido ({max_bytes} bytes)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_magic_bytes(ext: str, content: bytes, filename: str) -> None:
    if ext in {".xlsx", ".xlsm"} and not content.startswith(b"PK"):
        raise HTTPException(
            status_code=415,
            detail=f"El archivo '{filename}' no parece un Excel valido (.xlsx/.xlsm).",
        )


def _load_by_tipo(tipo: str, path: Path):
    if tipo == "pre_corte":
        return load_pre_corte(path)
    if tipo == "flash":
        return load_flash(path)
    raise HTTPException(status_code=400, detail=f"tipo invalido: {tipo}")


def _process_upload(
    tipo: str,
    upload: UploadFile,
    conn: sqlite3.Connection,
    uploaded_by_user_id: int | None = None,
) -> FileUploadResponse:
    filename = _safe_filename(upload.filename, "archivo_sin_nombre")
    ext = Path(filename).suffix.lower() or (".csv" if tipo == "flash" else ".xlsx")

    permitidas = _ALLOWED_EXT_BY_TIPO.get(tipo, set())
    if ext not in permitidas:
        raise HTTPException(
            status_code=415,
            detail=(
                f"El archivo '{filename}' tiene extension '{ext}' no permitida para "
                f"tipo '{tipo}'. Permitidas: {sorted(permitidas)}"
            ),
        )

    content = _read_upload_limited(upload, max_bytes=MAX_UPLOAD_BYTES)
    _validate_magic_bytes(ext, content, filename)
    tmp_path = storage.UPLOADS_DIR / (
        f"_tmp_{Path(filename).stem}_{uuid.uuid4().hex[:8]}{ext}"
    )
    tmp_path.write_bytes(content)

    try:
        df, meta = _load_by_tipo(tipo, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail=f"No se pudo parsear el archivo '{filename}': {exc}",
        ) from exc

    fecha_archivo = None
    fecha_produccion = None
    dias_saltados: int | None = None
    motivos_saltados: list[str] | None = None
    if tipo == "pre_corte":
        try:
            fecha_archivo = extract_file_date(filename)
            fecha_produccion, dias_saltados, motivos_saltados = (
                extract_production_date_verbose(filename)
            )
        except FilenameDateError as exc:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail=f"El nombre '{filename}' no contiene fecha valida: {exc}",
            ) from exc

    meta_for_db = dict(meta)
    meta_for_db["filename"] = filename

    carga_id, es_nueva = get_or_insert_carga(
        conn,
        meta_for_db,
        fecha_archivo=fecha_archivo,
        fecha_produccion=fecha_produccion,
        uploaded_by_user_id=uploaded_by_user_id,
    )
    conn.commit()

    if es_nueva or not storage.has_parsed_df(carga_id):
        final_source = storage.upload_source_path(carga_id, ext)
        if final_source.exists():
            final_source.unlink()
        tmp_path.rename(final_source)
        storage.save_parsed_df(carga_id, df)
    else:
        tmp_path.unlink(missing_ok=True)

    filas_original = int(meta["num_filas_original"])
    filas_procesadas = int(meta["num_filas_procesadas"])
    filas_sin_sap: int | None = None
    coverage_pct: float | None = None
    notif_presente: bool | None = None
    pair_learn: dict[str, int] | None = None
    if tipo == "pre_corte":
        filas_sin_sap = int(meta.get("num_filas_sin_sap", 0))
        coverage_pct = (
            round(filas_procesadas / filas_original * 100.0, 2)
            if filas_original > 0
            else 0.0
        )
        notif_presente = bool(meta.get("notificacion_presente", False))
        pair_learn = meta.get("pair_learn_stats")

    return FileUploadResponse(
        carga_id=carga_id,
        tipo=tipo,
        filename=filename,
        hash_sha256=meta["hash_sha256"],
        num_filas_original=filas_original,
        num_filas_procesadas=filas_procesadas,
        fecha_archivo=fecha_archivo,
        fecha_produccion=fecha_produccion,
        ya_existia=not es_nueva,
        notificacion_presente=notif_presente,
        num_filas_sin_sap=filas_sin_sap,
        catalog_coverage_pct=coverage_pct,
        pair_learn_stats=pair_learn,
        dias_saltados=dias_saltados,
        motivos_saltados=motivos_saltados,
    )


@router.post("/upload-pre-corte", response_model=FileUploadResponse)
def upload_pre_corte(
    file: UploadFile = File(...),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> FileUploadResponse:
    """Sube UN archivo PRE CORTE, parsea, cachea y crea carga."""
    return _process_upload("pre_corte", file, conn, uploaded_by_user_id=user.user_id)


@router.post("/upload-pre-corte-batch", response_model=BatchUploadResponse)
def upload_pre_corte_batch(
    files: list[UploadFile] = File(...),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> BatchUploadResponse:
    """Sube N archivos PRE CORTE del mes en una sola llamada."""
    if len(files) > MAX_UPLOAD_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"Maximo {MAX_UPLOAD_FILES_PER_REQUEST} archivos por solicitud",
        )
    pre_cortes = []
    errores = []
    for f in files:
        try:
            pre_cortes.append(
                _process_upload(
                    "pre_corte",
                    f,
                    conn,
                    uploaded_by_user_id=user.user_id,
                )
            )
        except HTTPException as exc:
            errores.append({"filename": f.filename or "?", "error": str(exc.detail)})
    return BatchUploadResponse(pre_cortes=pre_cortes, errores=errores)


@router.post("/upload-flash", response_model=FileUploadResponse)
def upload_flash(
    file: UploadFile = File(...),
    user: AuthUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(db_connection),
) -> FileUploadResponse:
    """Sube el archivo FLASH mensual (una sola vez por batch)."""
    return _process_upload("flash", file, conn, uploaded_by_user_id=user.user_id)
