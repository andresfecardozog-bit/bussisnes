"""Endpoints atomicos del pipeline. Cada uno hace UN paso e implementa el
patron que Power Automate va a orquestar como si fuera n8n.

Cada endpoint:
- Recibe `run_id` (sub-run).
- Lee estado y cargas desde SQLite + cache pickle de DataFrames.
- Actualiza `runs.current_step` y opcionalmente `runs.summary_json`.
- Retorna un JSON pequeno con contadores/resumen.

El detalle completo (DataFrames) queda en el cache local; solo se muestra al
usuario cuando llama al endpoint de kpis-preview.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.api import storage
from app.api.dependencies import db_connection, get_run_or_404
from app.api.schemas import (
    AggregateResponse,
    ExtractDateResponse,
    KpiCard,
    KpisPreviewResponse,
    LoadStepResponse,
    MatchStepResponse,
    PersistResponse,
    ValidateResponse,
    ValidationItem,
)
from app.core.aggregator import aggregate_flash
from app.core.date_extractor import extract_file_date, extract_production_date
from app.core.db import persist_run as db_persist_run
from app.core.db import update_run
from app.core.matcher import match_by_material
from app.core.validators import (
    all_ok as validators_all_ok,
    as_dict_list,
    run_all_validations,
)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def _get_carga(conn: sqlite3.Connection, carga_id: int | None, tipo_esperado: str):
    if not carga_id:
        raise HTTPException(400, f"El run no tiene carga de tipo {tipo_esperado}")
    row = conn.execute("SELECT * FROM cargas WHERE id = ?", (carga_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Carga {carga_id} no encontrada")
    d = dict(row)
    if d["tipo"] != tipo_esperado:
        raise HTTPException(
            400, f"Carga {carga_id} es tipo '{d['tipo']}', se esperaba '{tipo_esperado}'"
        )
    return d


def _fecha_from(row: dict, field: str) -> date | None:
    v = row.get(field)
    if not v:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


@router.post("/{run_id}/extract-date", response_model=ExtractDateResponse)
def extract_date(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> ExtractDateResponse:
    pre_carga = _get_carga(conn, run.get("pre_corte_carga_id"), "pre_corte")
    fecha_arch = extract_file_date(pre_carga["filename"])
    fecha_prod = extract_production_date(pre_carga["filename"])
    update_run(conn, run_id, current_step="extract_date")
    return ExtractDateResponse(
        fecha_archivo=fecha_arch,
        fecha_produccion=fecha_prod,
        filename=pre_carga["filename"],
    )


@router.post("/{run_id}/load-pre-corte", response_model=LoadStepResponse)
def load_pre_corte(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> LoadStepResponse:
    pre_carga = _get_carga(conn, run.get("pre_corte_carga_id"), "pre_corte")
    if not storage.has_parsed_df(pre_carga["id"]):
        raise HTTPException(409, "El pre_corte no fue parseado en upload. Reubir archivo.")
    df = storage.load_parsed_df(pre_carga["id"])
    update_run(conn, run_id, current_step="load_pre_corte")
    return LoadStepResponse(
        tipo="pre_corte",
        filename=pre_carga["filename"],
        hash_sha256=pre_carga["hash_sha256"],
        num_filas_original=pre_carga["num_filas_original"],
        num_filas_procesadas=pre_carga["num_filas_procesadas"],
        columnas=list(df.columns),
    )


@router.post("/{run_id}/load-flash", response_model=LoadStepResponse)
def load_flash_step(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> LoadStepResponse:
    flash_carga = _get_carga(conn, run.get("flash_carga_id"), "flash")
    if not storage.has_parsed_df(flash_carga["id"]):
        raise HTTPException(409, "El flash no fue parseado en upload. Reubir archivo.")
    df = storage.load_parsed_df(flash_carga["id"])
    update_run(conn, run_id, current_step="load_flash")
    return LoadStepResponse(
        tipo="flash",
        filename=flash_carga["filename"],
        hash_sha256=flash_carga["hash_sha256"],
        num_filas_original=flash_carga["num_filas_original"],
        num_filas_procesadas=flash_carga["num_filas_procesadas"],
        columnas=list(df.columns),
    )


def _pipeline_data(conn: sqlite3.Connection, run: dict):
    """Helper que carga pre_corte + flash + fecha + agg desde caches."""
    pre_carga = _get_carga(conn, run.get("pre_corte_carga_id"), "pre_corte")
    flash_carga = _get_carga(conn, run.get("flash_carga_id"), "flash")
    fecha_prod = _fecha_from(pre_carga, "fecha_produccion") or extract_production_date(
        pre_carga["filename"]
    )
    pre_df = storage.load_parsed_df(pre_carga["id"])
    flash_df = storage.load_parsed_df(flash_carga["id"])
    return pre_carga, flash_carga, fecha_prod, pre_df, flash_df


@router.post("/{run_id}/aggregate", response_model=AggregateResponse)
def aggregate_step(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> AggregateResponse:
    _, _, fecha_prod, _, flash_df = _pipeline_data(conn, run)
    agg = aggregate_flash(flash_df, fecha_prod)
    update_run(conn, run_id, current_step="aggregate")
    return AggregateResponse(
        fecha_produccion=fecha_prod,
        materiales_flash=len(agg),
        cantidad_total=float(agg["cantidad_neta_total"].sum() if not agg.empty else 0),
        facturado_total=float(agg["facturado_real_total"].sum() if not agg.empty else 0),
        num_facturas_total=int(agg["num_facturas"].sum() if not agg.empty else 0),
    )


@router.post("/{run_id}/match", response_model=MatchStepResponse)
def match_step(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> MatchStepResponse:
    pre_carga, flash_carga, fecha_prod, pre_df, flash_df = _pipeline_data(conn, run)
    agg = aggregate_flash(flash_df, fecha_prod)
    result = match_by_material(pre_df, agg, fecha_prod)

    top5 = []
    if not result.matched.empty:
        m = result.matched.copy()
        m["abs_delta"] = m["delta_unidades"].abs()
        top5 = (
            m.sort_values("abs_delta", ascending=False)
            .head(5)[
                ["material", "referencia", "notificado_unidades", "real_unidades_flash",
                 "delta_unidades", "cumplimiento_pct"]
            ]
            .to_dict(orient="records")
        )

    summary = {
        "matched": len(result.matched),
        "solo_pre_corte": len(result.solo_pre_corte),
        "solo_flash": len(result.solo_flash),
    }
    update_run(conn, run_id, current_step="match", summary=summary)
    return MatchStepResponse(
        fecha_produccion=fecha_prod,
        matched=len(result.matched),
        solo_pre_corte=len(result.solo_pre_corte),
        solo_flash=len(result.solo_flash),
        no_cruzados=len(result.no_cruzados),
        top_5_desviaciones=top5,
    )


@router.post("/{run_id}/validate", response_model=ValidateResponse)
def validate_step(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> ValidateResponse:
    pre_carga, flash_carga, fecha_prod, pre_df, flash_df = _pipeline_data(conn, run)
    agg = aggregate_flash(flash_df, fecha_prod)
    result = match_by_material(pre_df, agg, fecha_prod)

    pre_meta = {
        "num_filas_original": pre_carga["num_filas_original"],
        "num_filas_procesadas": pre_carga["num_filas_procesadas"],
    }
    flash_meta = {
        "num_filas_original": flash_carga["num_filas_original"],
        "num_filas_procesadas": flash_carga["num_filas_procesadas"],
    }
    pre_src = storage.UPLOADS_DIR.glob(f"{pre_carga['id']}.*")
    flash_src = storage.UPLOADS_DIR.glob(f"{flash_carga['id']}.*")
    pre_path = next((p for p in pre_src if p.suffix != ".pkl"), None)
    flash_path = next((p for p in flash_src if p.suffix != ".pkl"), None)
    if not pre_path or not flash_path:
        raise HTTPException(500, "No se encontraron los archivos originales para validar")

    validaciones = run_all_validations(
        pre_corte_path=pre_path,
        pre_corte_df=pre_df,
        pre_corte_meta=pre_meta,
        flash_path=flash_path,
        flash_df=flash_df,
        flash_meta=flash_meta,
        match_result=result,
    )
    ok = validators_all_ok(validaciones)
    next_status = "awaiting_approval" if ok else "failed"
    update_run(conn, run_id, current_step="validate", status=next_status)

    return ValidateResponse(
        validaciones=[ValidationItem(**d) for d in as_dict_list(validaciones)],
        all_ok=ok,
    )


@router.post("/{run_id}/kpis-preview", response_model=KpisPreviewResponse)
def kpis_preview_step(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> KpisPreviewResponse:
    pre_carga, flash_carga, fecha_prod, pre_df, flash_df = _pipeline_data(conn, run)
    agg = aggregate_flash(flash_df, fecha_prod)
    result = match_by_material(pre_df, agg, fecha_prod)

    total_notif = float(result.matched["notificado_unidades"].sum() if not result.matched.empty else 0)
    total_real = float(result.matched["real_unidades_flash"].sum() if not result.matched.empty else 0)
    cumpl = (total_real / total_notif * 100.0) if total_notif else 0.0

    cards = [
        KpiCard(nombre="Cumplimiento %", valor=round(cumpl, 2), formato="pct",
                descripcion="Real / Notificado total del dia"),
        KpiCard(nombre="Delta unidades", valor=round(total_real - total_notif, 0),
                formato="int", descripcion="Real - Notificado en unidades"),
        KpiCard(nombre="Materiales cruzados", valor=len(result.matched),
                formato="int", descripcion="Codigos SAP presentes en ambos"),
        KpiCard(nombre="Materiales sin venta", valor=len(result.solo_pre_corte),
                formato="int", descripcion="Notificados pero no facturados"),
        KpiCard(nombre="Fugas (vendidos sin notificar)",
                valor=len(result.solo_flash), formato="int",
                descripcion="Facturados sin notificacion previa"),
    ]

    tabla_ejecutiva = []
    if not result.matched.empty:
        tabla_ejecutiva = result.matched[
            ["material", "referencia", "notificado_unidades",
             "real_unidades_flash", "delta_unidades", "cumplimiento_pct"]
        ].to_dict(orient="records")

    update_run(conn, run_id, current_step="kpis_preview")
    return KpisPreviewResponse(
        fecha_produccion=fecha_prod,
        cards=cards,
        tabla_ejecutiva=tabla_ejecutiva,
    )


@router.post("/{run_id}/persist", response_model=PersistResponse)
def persist_step(
    run_id: str,
    run: dict = Depends(get_run_or_404),
    conn: sqlite3.Connection = Depends(db_connection),
) -> PersistResponse:
    if run["status"] != "approved":
        raise HTTPException(
            409,
            f"Run debe estar en status 'approved' para persistir, actual: '{run['status']}'",
        )
    pre_carga, flash_carga, fecha_prod, pre_df, flash_df = _pipeline_data(conn, run)
    fecha_archivo = _fecha_from(pre_carga, "fecha_archivo") or extract_file_date(
        pre_carga["filename"]
    )
    agg = aggregate_flash(flash_df, fecha_prod)
    result = match_by_material(pre_df, agg, fecha_prod)

    pre_meta = {
        "filename": pre_carga["filename"],
        "tipo": "pre_corte",
        "hash_sha256": pre_carga["hash_sha256"],
        "num_filas_original": pre_carga["num_filas_original"],
        "num_filas_procesadas": pre_carga["num_filas_procesadas"],
    }
    flash_meta = {
        "filename": flash_carga["filename"],
        "tipo": "flash",
        "hash_sha256": flash_carga["hash_sha256"],
        "num_filas_original": flash_carga["num_filas_original"],
        "num_filas_procesadas": flash_carga["num_filas_procesadas"],
    }

    summary = db_persist_run(
        conn,
        pre_corte_meta=pre_meta,
        pre_corte_df=pre_df,
        flash_meta=flash_meta,
        flash_agregado_df=agg,
        match_result=result,
        fecha_archivo=fecha_archivo,
    )
    update_run(
        conn,
        run_id,
        current_step="persisted",
        status="completed",
        summary=summary,
        ended=True,
    )
    return PersistResponse(
        pre_carga_id=summary["pre_carga_id"],
        flash_carga_id=summary["flash_carga_id"],
        cruce_filas_insertadas=summary["cruce_filas_insertadas"],
        no_cruzados_filas_insertadas=summary["no_cruzados_filas_insertadas"],
        ya_existia=not (summary["pre_es_nueva"] and summary["flash_es_nueva"]),
    )
