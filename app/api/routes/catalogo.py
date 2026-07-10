"""Catalogo de procesos predefinidos ('habilidades' reutilizables).

Son MatchProfiles curados que YA codifican el conocimiento acordado (mapeo,
llaves, transformaciones, KPIs, diseno del reporte). Se ejecutan de forma
deterministica SIN pasar por los agentes ni el chat: el usuario solo sube los
archivos (uno o varios) y elige si quiere un reporte consolidado (une todo en
una sola base) o uno por archivo/periodo. Cero costo LLM, reproducible e
identico a la validacion manual.
"""
from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.jobs import create_job, run_job
from app.api.security import AuthUser, current_user
from app.config import DATA_DIR, MAX_UPLOAD_BYTES, MAX_UPLOAD_FILES_PER_REQUEST
from app.platform.engine import GenericMatchResult, run_profile, run_profile_multi
from app.platform.profile import MatchProfile
from app.platform.render_excel import render_excel
from app.platform.render_pbip import render_pbip

router = APIRouter(prefix="/catalogo", tags=["catalogo"])

_PROFILES_DIR = Path(__file__).resolve().parents[3] / "profiles"
_CATALOG_OUTPUTS = DATA_DIR / "outputs" / "catalogo"
_CATALOG_UPLOADS = DATA_DIR / "uploads" / "catalogo"

# Registro de habilidades predefinidas: skill_id -> metadata + profile.
_SKILLS: dict[str, dict[str, Any]] = {
    "cen_vs_sap": {
        "profile_file": "cen_vs_sap_v1_borrador.json",
        "nombre": "Nivel de servicio CEN vs SAP",
        "descripcion": (
            "Cruza las ordenes de la plataforma CEN contra las entregas de SAP: "
            "nivel de servicio (completo/parcial/no entregado en % y unidades), "
            "devoluciones por motivo, rechazos en ventas y desgloses por "
            "material, distrito, cliente y canal."
        ),
        "left_label": "Ordenes CEN (uno o varios periodos)",
        "right_label": "Ventas SAP (uno o varios meses)",
    },
    "pre_corte": {
        "profile_file": "pre_corte_v1.json",
        "nombre": "Cumplimiento PRE CORTE vs FLASH",
        "descripcion": (
            "Mide el cumplimiento de produccion de huevo cruzando el PRE CORTE "
            "(necesidad) contra el FLASH (facturado real) por material."
        ),
        "left_label": "PRE CORTE (uno o varios)",
        "right_label": "FLASH (uno o varios)",
    },
}


def _skill_disponible(sid: str) -> bool:
    meta = _SKILLS.get(sid)
    return bool(meta) and (_PROFILES_DIR / meta["profile_file"]).exists()


def _load_skill_profile(skill_id: str) -> tuple[dict[str, Any], MatchProfile]:
    meta = _SKILLS.get(skill_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Proceso predefinido '{skill_id}' no existe")
    path = _PROFILES_DIR / meta["profile_file"]
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Falta el profile {meta['profile_file']}")
    return meta, MatchProfile.from_json(path.read_text(encoding="utf-8"))


def _assert_run_permission(user: AuthUser) -> None:
    if not (user.has_permission("run:execute") or user.has_permission("run:execute:own")):
        raise HTTPException(status_code=403, detail="Permiso insuficiente para ejecutar procesos")


def _safe_name(raw: str | None, fallback: str) -> str:
    base = Path(raw or fallback).name
    return base or fallback


def _save_uploads(dest_dir: Path, uploads: list[UploadFile], rol: str) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for up in uploads:
        name = _safe_name(up.filename, f"{rol}.bin")
        suffix = Path(name).suffix or ".bin"
        content = up.file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"'{name}' excede el tamano maximo permitido")
        if suffix.lower() in {".xlsx", ".xlsm"} and not content.startswith(b"PK"):
            raise HTTPException(status_code=415, detail=f"'{name}' no parece un Excel valido")
        target = dest_dir / f"{rol}_{uuid.uuid4().hex[:8]}_{name}"
        target.write_bytes(content)
        saved.append(target)
    return saved


def _summary_legacy(result: Any) -> dict[str, int]:
    s = result.summary()
    return {
        "matched": s.get("matched", 0),
        "solo_left": s.get("solo_pre_corte", 0),
        "solo_right": s.get("solo_flash", 0),
        "no_cruzados": s.get("no_cruzados", 0),
    }


def _kpis_legacy(result: Any) -> dict[str, Any]:
    import pandas as pd

    m = result.matched
    plan = float(pd.to_numeric(m.get("notificado_unidades"), errors="coerce").sum()) if len(m) else 0.0
    real = float(pd.to_numeric(m.get("real_unidades_flash"), errors="coerce").sum()) if len(m) else 0.0
    cumpl = round(real / plan * 100, 2) if plan > 0 else None
    return {
        "plan_total_unidades": plan,
        "real_total_unidades": real,
        "cumplimiento_global_pct": cumpl,
        "fecha_produccion": result.fecha_produccion.isoformat(),
    }


def _ejecutar_pre_corte_legacy(
    pre_cortes: list[Path], flashes: list[Path], modo: str, out_dir: Path
) -> list[dict[str, Any]]:
    """Reutiliza el pipeline y el exportador LEGADO (verificado) para PRE CORTE
    vs FLASH: carga -> cruce por material via catalogo SAP -> persiste en el
    historico -> exporta el Excel corporativo de 5-6 hojas (Portada, Resumen,
    Por_Semana, Por_Categoria, Detalle_Material, No_Cruzados).

    La fecha de produccion se deriva del nombre del PRE CORTE (como el legado).
    """
    from app.core.aggregator import aggregate_flash
    from app.core.date_extractor import extract_file_date, extract_production_date
    from app.core.db import get_conn as _legacy_conn
    from app.core.db import persist_run
    from app.core.exporters import export_cumplimiento_diario, export_cumplimiento_xlsx
    from app.core.loaders import load_flash, load_pre_corte
    from app.core.matcher import match_by_material

    if len(flashes) != 1:
        raise HTTPException(
            status_code=422,
            detail="PRE CORTE vs FLASH requiere exactamente 1 archivo FLASH (derecha).",
        )
    flash_path = flashes[0]
    try:
        flash_df, flash_meta = load_flash(flash_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"FLASH invalido: {exc}") from exc

    fechas: list[Any] = []
    resultados: list[dict[str, Any]] = []
    for pc in pre_cortes:
        try:
            fecha = extract_production_date(pc.name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No pude leer la fecha del nombre '{pc.name}'. El PRE CORTE "
                    "debe llamarse como 'PRE CORTE DD.MM.AAAA.xlsx'."
                ),
            ) from exc
        try:
            pre_df, pre_meta = load_pre_corte(pc)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"PRE CORTE invalido: {exc}") from exc
        flash_agg = aggregate_flash(flash_df, fecha)
        result = match_by_material(pre_df, flash_agg, fecha)
        with _legacy_conn() as conn:
            persist_run(
                conn,
                pre_corte_meta=pre_meta,
                pre_corte_df=pre_df,
                flash_meta=flash_meta,
                flash_agregado_df=flash_agg,
                match_result=result,
                fecha_archivo=extract_file_date(pc.name),
            )
        fechas.append(fecha)
        if modo == "individual":
            slug = fecha.strftime("%Y%m%d")
            sub = out_dir / slug
            sub.mkdir(parents=True, exist_ok=True)
            dest = sub / f"cumplimiento_{slug}.xlsx"
            export_cumplimiento_diario(fecha, dest)
            resultados.append(
                {
                    "etiqueta": slug,
                    "summary": _summary_legacy(result),
                    "kpis": _kpis_legacy(result),
                    "archivos": [dest.name],
                }
            )

    if modo == "consolidado":
        sub = out_dir / "consolidado"
        sub.mkdir(parents=True, exist_ok=True)
        desde, hasta = min(fechas), max(fechas)
        nombre = (
            f"cumplimiento_consolidado_{desde:%Y%m%d}_{hasta:%Y%m%d}.xlsx"
            if desde != hasta
            else f"cumplimiento_{desde:%Y%m%d}.xlsx"
        )
        dest = sub / nombre
        export_cumplimiento_xlsx(desde, hasta, dest)
        import pandas as pd

        resultados.append(
            {
                "etiqueta": "consolidado",
                "summary": {"matched": 0, "solo_left": 0, "solo_right": 0, "no_cruzados": 0},
                "kpis": {"fechas": f"{desde.isoformat()} a {hasta.isoformat()}", "archivos_pre_corte": len(pre_cortes)},
                "archivos": [dest.name],
            }
        )
    return resultados


def _render_and_zip(
    profile: MatchProfile, result: GenericMatchResult, out_dir: Path, slug: str
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    generados: list[str] = []
    try:
        if profile.report and profile.report.excel:
            excel_path = out_dir / f"{slug}.xlsx"
            render_excel(profile, result, excel_path)
            generados.append(excel_path.name)
        pbip_dir = out_dir / f"pbip_{slug}"
        if pbip_dir.exists():
            shutil.rmtree(pbip_dir)
        render_pbip(profile, result, pbip_dir)
        pbip_zip = out_dir / f"pbip_{slug}.zip"
        with zipfile.ZipFile(pbip_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(pbip_dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(pbip_dir.parent))
        shutil.rmtree(pbip_dir, ignore_errors=True)
        generados.append(pbip_zip.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=(
                f"No se pudieron generar los entregables: {type(exc).__name__}: {exc}"
            ),
        ) from exc
    return {
        "etiqueta": slug,
        "summary": result.summary(),
        "kpis": {k: v for k, v in result.kpis.items() if not isinstance(v, (dict, list))},
        "archivos": generados,
    }


@router.get("")
def list_catalogo(user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    items = [
        {
            "skill_id": sid,
            "nombre": meta["nombre"],
            "descripcion": meta["descripcion"],
            "left_label": meta["left_label"],
            "right_label": meta["right_label"],
        }
        for sid, meta in _SKILLS.items()
        if _skill_disponible(sid)
    ]
    return {"items": items}


@router.post("/{skill_id}/ejecutar")
async def ejecutar_catalogo(
    skill_id: str,
    left_files: list[UploadFile] = File(...),
    right_files: list[UploadFile] = File(...),
    modo: str = Form("consolidado"),
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Ejecuta un proceso predefinido con archivos NUEVOS, sin agentes.

    modo='consolidado': une todos los archivos izquierda y todos los derecha en
    una sola base y produce UN reporte.
    modo='individual': empareja por orden de subida (izq[i] con der[i]) y
    produce un reporte por pareja (mes por mes).
    """
    _assert_run_permission(user)
    meta, profile = _load_skill_profile(skill_id)

    if modo not in {"consolidado", "individual"}:
        raise HTTPException(status_code=422, detail="modo debe ser 'consolidado' o 'individual'")
    if not left_files or not right_files:
        raise HTTPException(status_code=422, detail="Debes subir al menos un archivo por lado")
    if len(left_files) + len(right_files) > MAX_UPLOAD_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail="Demasiados archivos en una sola peticion")
    if modo == "individual" and len(left_files) != len(right_files):
        raise HTTPException(
            status_code=422,
            detail=(
                "En modo individual sube la misma cantidad de archivos por lado "
                "(se emparejan por orden de subida)."
            ),
        )

    run_token = f"{skill_id}_{user.user_id}_{uuid.uuid4().hex[:10]}"
    up_dir = _CATALOG_UPLOADS / run_token
    out_dir = _CATALOG_OUTPUTS / run_token
    # Los archivos se guardan sincronicamente (los UploadFile no viven despues
    # de que la peticion responde); el cruce+render pesado va al job.
    lefts = _save_uploads(up_dir, left_files, "left")
    rights = _save_uploads(up_dir, right_files, "right")

    def _trabajo() -> dict[str, Any]:
        resultados: list[dict[str, Any]] = []
        try:
            if skill_id == "pre_corte":
                # PRE CORTE reutiliza el pipeline + exportador legado (paridad
                # exacta con el MVP verificado), no el motor generico.
                resultados = _ejecutar_pre_corte_legacy(lefts, rights, modo, out_dir)
            elif modo == "consolidado":
                result = run_profile_multi(profile, lefts, rights)
                resultados.append(
                    _render_and_zip(profile, result, out_dir / "consolidado", "consolidado")
                )
            else:
                for i, (lp, rp) in enumerate(zip(lefts, rights), start=1):
                    result = run_profile(profile, left_path=lp, right_path=rp)
                    slug = f"parte_{i:02d}"
                    resultados.append(
                        _render_and_zip(profile, result, out_dir / slug, slug)
                    )
        except HTTPException:
            raise
        except (ValueError, AssertionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "ok": True,
            "skill_id": skill_id,
            "modo": modo,
            "run_token": run_token,
            "resultados": resultados,
        }

    job_id = create_job(f"catalogo:{skill_id}", user.user_id)
    run_job(job_id, _trabajo)
    return {"ok": True, "job_id": job_id, "run_token": run_token, "modo": modo}


@router.get("/descargas/{run_token}")
def list_descargas(run_token: str, user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    _assert_run_permission(user)
    root = (_CATALOG_OUTPUTS / run_token).resolve()
    if not str(root).startswith(str(_CATALOG_OUTPUTS.resolve())) or not root.exists():
        raise HTTPException(status_code=404, detail="Ejecucion no encontrada")
    archivos = []
    for f in sorted(root.rglob("*")):
        if f.is_file():
            rel = f.relative_to(root).as_posix()
            kind = "excel" if f.suffix == ".xlsx" else "pbip" if f.suffix == ".zip" else "otro"
            archivos.append({"path": rel, "size_bytes": f.stat().st_size, "kind": kind})
    return {"run_token": run_token, "archivos": archivos}


@router.get("/descargas/{run_token}/{file_path:path}")
def descargar(run_token: str, file_path: str, user: AuthUser = Depends(current_user)):
    _assert_run_permission(user)
    root = (_CATALOG_OUTPUTS / run_token).resolve()
    target = (root / file_path).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(target, filename=target.name)
