"""Endpoints de la plataforma generica: propuesta asistida por agentes,
chat de entrevista, aprobacion y ejecucion de MatchProfiles.

Flujo tipico del frontend:

1. POST /profiles/draft            (archivos + brief -> agentes proponen)
2. GET  /profiles/{id}/proposal    (profile propuesto + preguntas abiertas)
3. GET  /profiles/{id}/chat        (hilo de entrevista)
4. POST /profiles/{id}/chat        (respuesta del usuario; question_id opcional)
5. POST /profiles/{id}/refine      (re-propuesta con la memoria actualizada)
6. POST /profiles/{id}/approve     (rechaza 409 si hay bloqueantes abiertas)
7. POST /profiles/{id}/run         (ejecuta el profile aprobado: motor deterministico)
8. GET  /profiles/{id}/runs        (historial de ejecuciones + KPIs)
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.agents.orchestrator import (
    _auto_fix_grano,
    _auto_fix_join_keys,
    answer_question,
    approve_proposed_profile,
    assume_question,
    add_knowledge,
    list_questions,
    propose_profile,
    proposal_status,
)
from app.agents.telemetry import telemetry_summary
from app.api.jobs import create_job, run_job
from app.api.security import (
    AuthUser,
    current_user,
    ensure_owner_access,
    ensure_run_permission,
    require_permission,
)
from app.config import DATA_DIR, MAX_UPLOAD_BYTES, UPLOADS_DIR
from app.core.db import get_conn
from app.core.sku_catalog import import_from_homologacion
from app.platform.engine import GranoNoResueltoError, run_profile
from app.platform.profile import MatchProfile
from app.platform.store import (
    init_platform_schema,
    list_profiles,
    list_runs,
    load_profile,
    persist_run,
    save_profile,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])

_PROFILE_UPLOADS = UPLOADS_DIR / "profiles"
_PROFILE_OUTPUTS = DATA_DIR / "outputs" / "profiles"


def _safe_filename(raw: str | None, fallback: str = "archivo.bin") -> str:
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
                detail=f"Archivo excede el limite permitido ({max_bytes} bytes)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _get_crew():
    """Crew real (Gemini). Importa perezoso para que los endpoints que no
    usan LLM funcionen sin API key."""
    from app.agents.crew import Crew

    try:
        return Crew()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _save_upload(profile_id: str, upload: UploadFile, rol: str) -> Path:
    _PROFILE_UPLOADS.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(upload.filename, "archivo.bin")
    suffix = Path(filename).suffix or ".bin"
    dest = _PROFILE_UPLOADS / f"{profile_id}_{rol}_{uuid.uuid4().hex[:8]}{suffix}"
    content = _read_upload_limited(upload, max_bytes=MAX_UPLOAD_BYTES)
    if suffix.lower() in {".xlsx", ".xlsm"} and not content.startswith(b"PK"):
        raise HTTPException(status_code=415, detail=f"El archivo '{filename}' no parece un Excel valido")
    dest.write_bytes(content)
    return dest


def _registrar_archivos(
    conn,
    profile_id: str,
    left: Path,
    right: Path,
    homologacion: Path | None = None,
) -> None:
    payload: dict[str, Any] = {"left_path": str(left), "right_path": str(right)}
    if homologacion is not None:
        payload["homologacion_paths"] = [str(homologacion)]
    add_knowledge(
        conn, profile_id, "nota", "sistema",
        json.dumps(payload),
    )


def _ultimos_archivos(conn, profile_id: str) -> tuple[Path, Path]:
    rows = conn.execute(
        """
        SELECT contenido FROM profile_knowledge
        WHERE profile_id = ? AND autor = 'sistema' AND kind = 'nota'
        ORDER BY id DESC
        """,
        (profile_id,),
    ).fetchall()
    for r in rows:
        try:
            data = json.loads(r["contenido"])
        except (json.JSONDecodeError, TypeError):
            continue
        if "left_path" in data and "right_path" in data:
            left, right = Path(data["left_path"]), Path(data["right_path"])
            if left.exists() and right.exists():
                return left, right
    raise HTTPException(
        status_code=404,
        detail=f"No hay archivos registrados para '{profile_id}'. Subir con POST /profiles/draft.",
    )


def _ultimas_homologaciones(conn, profile_id: str) -> list[Path]:
    rows = conn.execute(
        """
        SELECT contenido FROM profile_knowledge
        WHERE profile_id = ? AND autor = 'sistema' AND kind = 'nota'
        ORDER BY id DESC
        """,
        (profile_id,),
    ).fetchall()
    out: list[Path] = []
    seen: set[str] = set()
    for r in rows:
        try:
            data = json.loads(r["contenido"])
        except (json.JSONDecodeError, TypeError):
            continue
        paths = data.get("homologacion_paths") or []
        if isinstance(paths, str):
            paths = [paths]
        for raw in paths:
            p = Path(raw)
            if not p.exists():
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


def _profile_owner_user_id(conn, profile_id: str) -> int | None:
    row = conn.execute(
        """
        SELECT owner_user_id
        FROM profiles
        WHERE profile_id = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (profile_id,),
    ).fetchone()
    if not row:
        return None
    owner = row["owner_user_id"]
    return int(owner) if owner is not None else None


def _assert_profile_read_access(conn, profile_id: str, user: AuthUser) -> None:
    owner = _profile_owner_user_id(conn, profile_id)
    ensure_owner_access(
        user,
        owner_user_id=owner,
        read_all_permission="profiles:read:all",
        read_own_permission="profiles:read:own",
    )


def _assert_profile_write_access(conn, profile_id: str, user: AuthUser) -> None:
    if not user.has_permission("profiles:write"):
        raise HTTPException(status_code=403, detail="Permiso insuficiente: profiles:write")
    owner = _profile_owner_user_id(conn, profile_id)
    if owner is not None and not user.has_permission("profiles:read:all") and owner != user.user_id:
        raise HTTPException(status_code=403, detail="Acceso denegado")


def _assert_profile_run_access(conn, profile_id: str, user: AuthUser) -> None:
    owner = _profile_owner_user_id(conn, profile_id)
    ensure_run_permission(user, owner_user_id=owner)


def _assert_profile_download_access(conn, profile_id: str, user: AuthUser) -> None:
    owner = _profile_owner_user_id(conn, profile_id)
    ensure_owner_access(
        user,
        owner_user_id=owner,
        read_all_permission="download:all",
        read_own_permission="download:own",
    )


def _importar_homologacion_si_aplica(conn, profile_id: str, path: Path) -> dict[str, Any]:
    try:
        stats = import_from_homologacion(path, conn)
    except Exception as exc:
        err = str(exc)[:500]
        add_knowledge(
            conn,
            profile_id,
            "nota",
            "sistema",
            json.dumps(
                {
                    "homologacion_import_error": err,
                    "homologacion_paths": [str(path)],
                }
            ),
        )
        return {"ok": False, "error": err}
    add_knowledge(
        conn,
        profile_id,
        "nota",
        "sistema",
        json.dumps({"homologacion_import": stats, "homologacion_paths": [str(path)]}),
    )
    return {"ok": True, "stats": stats}


def _norm_material_code(v: Any) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s or s.lower() in {"nan", "none", "<na>"}:
        return None
    try:
        return str(int(float(s)))
    except (TypeError, ValueError):
        digits = re.sub(r"\D", "", s)
        return digits or None


def _material_name_map(conn) -> dict[str, str]:
    try:
        rows = conn.execute(
            """
            SELECT material_sap, nombre_notificacion, fuente, veces_visto, id
            FROM sku_catalog
            WHERE nombre_notificacion IS NOT NULL AND TRIM(nombre_notificacion) <> ''
            ORDER BY
                CASE fuente
                    WHEN 'manual' THEN 3
                    WHEN 'aprendido_pair' THEN 2
                    WHEN 'homologacion' THEN 1
                    ELSE 0
                END DESC,
                veces_visto DESC,
                id DESC
            """
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, str] = {}
    for r in rows:
        key = _norm_material_code(r["material_sap"])
        if not key or key in out:
            continue
        out[key] = str(r["nombre_notificacion"]).strip()
    return out


def _enrich_df_with_homologacion(df: pd.DataFrame, code_to_name: dict[str, str]) -> list[str]:
    if df.empty or not code_to_name:
        return []
    enriched_cols: list[str] = []
    for col in df.columns:
        c = col.lower()
        if "homolog" in c or "nombre" in c or "descripcion" in c:
            continue
        if not any(tok in c for tok in ("material", "item", "sku", "codigo")):
            continue
        target = f"{col}_homologado"
        if target in df.columns:
            continue
        raw = df[col]
        codes = raw.map(_norm_material_code)
        mapped = codes.map(code_to_name)
        notna = int(raw.notna().sum())
        hits = int(mapped.notna().sum())
        if hits == 0:
            continue
        if notna > 0 and (hits / notna) < 0.15:
            continue
        df[target] = mapped.fillna(raw.astype("string"))
        enriched_cols.append(target)
    return enriched_cols


def _aplicar_homologacion_materiales(conn, profile_id: str, result) -> None:
    homolog_files = _ultimas_homologaciones(conn, profile_id)
    if not homolog_files:
        return
    code_to_name = _material_name_map(conn)
    if not code_to_name:
        return
    enriched: list[str] = []
    for frame in (result.matched, result.solo_left, result.solo_right):
        enriched.extend(_enrich_df_with_homologacion(frame, code_to_name))
    for bd in result.breakdowns.values():
        enriched.extend(_enrich_df_with_homologacion(bd, code_to_name))
    if enriched:
        result.kpis["homologacion_materiales"] = {
            "archivos": [p.name for p in homolog_files],
            "columnas_enriquecidas": sorted(set(enriched)),
        }


class ChatIn(BaseModel):
    mensaje: str
    question_id: int | None = None


class ApproveIn(BaseModel):
    aprobado_por: str = "usuario"
    version: int | None = None


class RunIn(BaseModel):
    version: int | None = None
    parameters: dict[str, Any] = {}


@router.post("/{profile_id}/homologacion")
async def post_homologacion(
    profile_id: str,
    homologacion_file: UploadFile = File(...),
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    if not homologacion_file.filename:
        raise HTTPException(status_code=422, detail="Archivo de homologacion vacio.")
    with get_conn() as conn:
        init_platform_schema(conn)
        _assert_profile_write_access(conn, profile_id, user)
        path = _save_upload(profile_id, homologacion_file, "homologacion")
        add_knowledge(
            conn,
            profile_id,
            "nota",
            "usuario",
            f"Adjunte archivo de homologacion: {homologacion_file.filename}",
        )
        add_knowledge(
            conn,
            profile_id,
            "nota",
            "sistema",
            json.dumps({"homologacion_paths": [str(path)]}),
        )
        homologacion_import = _importar_homologacion_si_aplica(conn, profile_id, path)
        return {
            "ok": True,
            "filename": homologacion_file.filename,
            "status": proposal_status(conn, profile_id).model_dump(),
            "homologacion_import": homologacion_import,
        }


@router.get("")
def get_profiles(user: AuthUser = Depends(current_user)) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if user.has_permission("profiles:read:all"):
            return list_profiles(conn)
        if user.has_permission("profiles:read:own"):
            return list_profiles(conn, owner_user_id=user.user_id)
        raise HTTPException(status_code=403, detail="Permiso insuficiente para listar perfiles")


@router.post("/draft")
async def create_draft(
    profile_id: str = Form(...),
    brief: str = Form(...),
    left_file: UploadFile = File(...),
    right_file: UploadFile = File(...),
    homologacion_file: UploadFile | None = File(default=None),
    user: AuthUser = Depends(require_permission("profiles:write")),
) -> dict[str, Any]:
    """Sube los dos archivos + brief y corre el equipo de agentes."""
    crew = _get_crew()
    with get_conn() as conn:
        init_platform_schema(conn)
        _assert_profile_write_access(conn, profile_id, user)
        left = _save_upload(profile_id, left_file, "left")
        right = _save_upload(profile_id, right_file, "right")
        homologacion: Path | None = None
        if homologacion_file and homologacion_file.filename:
            homologacion = _save_upload(profile_id, homologacion_file, "homologacion")
        _registrar_archivos(conn, profile_id, left, right, homologacion)
        homologacion_import: dict[str, Any] | None = None
        if homologacion:
            add_knowledge(
                conn,
                profile_id,
                "nota",
                "usuario",
                f"Adjunte archivo de homologacion: {homologacion_file.filename}",
            )
            homologacion_import = _importar_homologacion_si_aplica(
                conn, profile_id, homologacion
            )
    def _trabajo() -> dict[str, Any]:
        with get_conn() as conn:
            try:
                resultado = propose_profile(
                    crew, conn, profile_id, left, right, brief=brief
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Propuesta fallo: {exc}") from exc
            conn.execute(
                """
                UPDATE profiles
                SET owner_user_id = COALESCE(owner_user_id, ?)
                WHERE profile_id = ?
                """,
                (user.user_id, profile_id),
            )
            conn.commit()
            return {
                "profile_id": profile_id,
                "status": resultado["status"].model_dump(),
                "preguntas_nuevas": resultado["preguntas_nuevas"],
                "profile": json.loads(resultado["profile"].to_json()),
                "justificaciones": {
                    "mapping": resultado["mapping"].justificacion,
                    "kpis": resultado["kpis"].justificacion,
                    "report": resultado["report"].justificacion,
                },
                "resumen_fuentes": {
                    "left": resultado["scout_left"].resumen,
                    "right": resultado["scout_right"].resumen,
                },
                "homologacion_import": homologacion_import,
            }

    job_id = create_job(f"draft:{profile_id}", user.user_id)
    run_job(job_id, _trabajo)
    return {"ok": True, "job_id": job_id, "profile_id": profile_id}


@router.get("/{profile_id}/proposal")
def get_proposal(
    profile_id: str,
    version: int | None = None,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    with get_conn() as conn:
        _assert_profile_read_access(conn, profile_id, user)
        try:
            profile = load_profile(conn, profile_id, version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        status = proposal_status(conn, profile_id)
        return {
            "profile": json.loads(profile.to_json()),
            "status": status.model_dump(),
            "preguntas_abiertas": list_questions(conn, profile_id, estado="abierta"),
        }


@router.get("/{profile_id}/chat")
def get_chat(
    profile_id: str,
    user: AuthUser = Depends(current_user),
) -> list[dict[str, Any]]:
    """Hilo de entrevista: brief y notas del usuario + preguntas de agentes
    (con estado) + respuestas, ordenado cronologicamente."""
    with get_conn() as conn:
        init_platform_schema(conn)
        _assert_profile_read_access(conn, profile_id, user)
        mensajes: list[dict[str, Any]] = []
        for r in conn.execute(
            """
            SELECT id, kind, autor, contenido, creado_en FROM profile_knowledge
            WHERE profile_id = ? AND NOT (autor = 'sistema' AND kind = 'nota')
            ORDER BY id
            """,
            (profile_id,),
        ).fetchall():
            mensajes.append(
                {
                    "tipo": r["kind"],
                    "role": "usuario" if r["autor"] == "usuario" else "sistema",
                    "autor": r["autor"],
                    "contenido": r["contenido"],
                    "timestamp": r["creado_en"],
                }
            )
        for q in list_questions(conn, profile_id):
            mensajes.append(
                {
                    "tipo": "pregunta",
                    "role": "agente",
                    "autor": q["agente"],
                    "contenido": q["pregunta"],
                    "hipotesis": q["hipotesis"],
                    "impacto": q["impacto"],
                    "bloqueante": bool(q["bloqueante"]),
                    "estado": q["estado"],
                    "respuesta": q["respuesta"],
                    "question_id": q["id"],
                    "timestamp": q["creado_en"],
                }
            )
        mensajes.sort(key=lambda m: (m["timestamp"] or "", m.get("question_id") or 0))
        return mensajes


@router.post("/{profile_id}/chat")
def post_chat(
    profile_id: str,
    body: ChatIn,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Mensaje del usuario. Si question_id viene, responde esa pregunta;
    si no, se agrega como contexto adicional del proceso."""
    with get_conn() as conn:
        init_platform_schema(conn)
        _assert_profile_write_access(conn, profile_id, user)
        if body.question_id is not None:
            try:
                answer_question(conn, body.question_id, body.mensaje)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        else:
            add_knowledge(conn, profile_id, "nota", "usuario", body.mensaje)
        return {"ok": True, "status": proposal_status(conn, profile_id).model_dump()}


@router.post("/{profile_id}/questions/{question_id}/assume")
def post_assume(
    profile_id: str,
    question_id: int,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    with get_conn() as conn:
        _assert_profile_write_access(conn, profile_id, user)
        try:
            assume_question(conn, question_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "status": proposal_status(conn, profile_id).model_dump()}


@router.post("/{profile_id}/refine")
def post_refine(
    profile_id: str,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Re-corre la propuesta con la memoria actualizada (nueva version).

    Asincrono: responde con `job_id`; la re-propuesta de los agentes corre en
    segundo plano (evita el limite de tiempo del tunel)."""
    crew = _get_crew()
    with get_conn() as conn:
        _assert_profile_write_access(conn, profile_id, user)

    def _trabajo() -> dict[str, Any]:
        with get_conn() as conn:
            left, right = _ultimos_archivos(conn, profile_id)
            try:
                actual = load_profile(conn, profile_id)
                nueva_version = actual.version + 1
            except KeyError:
                nueva_version = 1
            try:
                resultado = propose_profile(
                    crew, conn, profile_id, left, right, brief="", version=nueva_version
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Los agentes no lograron producir una propuesta valida en "
                        f"este intento: {exc}. Reintentar el refine o ajustar las "
                        "respuestas en el chat."
                    ),
                ) from exc
            return {
                "profile_id": profile_id,
                "version": nueva_version,
                "status": resultado["status"].model_dump(),
                "preguntas_nuevas": resultado["preguntas_nuevas"],
                "profile": json.loads(resultado["profile"].to_json()),
            }

    job_id = create_job(f"refine:{profile_id}", user.user_id)
    run_job(job_id, _trabajo)
    return {"ok": True, "job_id": job_id}


@router.post("/{profile_id}/approve")
def post_approve(
    profile_id: str,
    body: ApproveIn,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    if not user.has_permission("profiles:approve"):
        raise HTTPException(status_code=403, detail="Permiso insuficiente: profiles:approve")
    with get_conn() as conn:
        _assert_profile_write_access(conn, profile_id, user)
        try:
            profile = load_profile(conn, profile_id, body.version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            approve_proposed_profile(
                conn, profile_id, profile.version, body.aprobado_por
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "profile_id": profile_id, "version": profile.version}


@router.put("/{profile_id}")
def put_profile(
    profile_id: str,
    body: dict[str, Any],
    user: AuthUser = Depends(require_permission("profiles:write")),
) -> dict[str, Any]:
    """Guarda un profile editado a mano por el humano (checkpoint de
    correccion). El body es el JSON completo del MatchProfile."""
    body["profile_id"] = profile_id
    try:
        profile = MatchProfile.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Profile invalido: {exc}") from exc
    with get_conn() as conn:
        _assert_profile_write_access(conn, profile_id, user)
        save_profile(conn, profile, status="proposed")
        conn.execute(
            """
            UPDATE profiles
            SET owner_user_id = COALESCE(owner_user_id, ?)
            WHERE profile_id = ? AND version = ?
            """,
            (user.user_id, profile_id, profile.version),
        )
        conn.commit()
        add_knowledge(
            conn, profile_id, "correccion", "usuario",
            f"Profile v{profile.version} editado manualmente por el usuario.",
        )
        return {"ok": True, "version": profile.version}


@router.post("/{profile_id}/run")
def post_run(
    profile_id: str,
    body: RunIn,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Ejecuta el profile APROBADO con el motor deterministico (sin LLM).
    Solo persiste; para generar los archivos descargables usar /generate."""
    with get_conn() as conn:
        _assert_profile_run_access(conn, profile_id, user)
        _, result = _ejecutar_aprobado(conn, profile_id, body.version, body.parameters)
        info = persist_run(conn, result)
        return {
            "ok": True,
            "run": info,
            "summary": result.summary(),
            "kpis": result.kpis,
        }


@router.get("/{profile_id}/runs")
def get_runs(
    profile_id: str,
    user: AuthUser = Depends(current_user),
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        _assert_profile_read_access(conn, profile_id, user)
        return list_runs(conn, profile_id)


def _ejecutar_aprobado(conn, profile_id: str, version: int | None, parameters: dict):
    try:
        profile = load_profile(conn, profile_id, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    row = conn.execute(
        "SELECT status FROM profiles WHERE profile_id = ? AND version = ?",
        (profile_id, profile.version),
    ).fetchone()
    if row is None or row["status"] != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"El profile '{profile_id}' v{profile.version} no esta aprobado.",
        )
    left, right = _ultimos_archivos(conn, profile_id)
    profile, _ = _auto_fix_join_keys(profile)
    profile, _ = _auto_fix_grano(profile, left, right, parameters)
    try:
        result = run_profile(
            profile, left_path=left, right_path=right, parameters=parameters
        )
    except GranoNoResueltoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ValueError, AssertionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _aplicar_homologacion_materiales(conn, profile_id, result)
    return profile, result


def _render_entregables(profile, result, profile_id: str) -> list[str]:
    """Renderiza Excel + PBIP (zip) del resultado y devuelve los nombres de
    archivo. Aisla el render para que un spec de reporte problematico devuelva
    un 422 accionable en vez de un 500 opaco."""
    import zipfile

    from app.platform.render_excel import render_excel
    from app.platform.render_pbip import render_pbip

    out_dir = _PROFILE_OUTPUTS / profile_id
    out_dir.mkdir(parents=True, exist_ok=True)
    generados: list[str] = []
    try:
        if profile.report and profile.report.excel:
            prefix = profile.report.excel.filename_prefix
        else:
            prefix = profile_id
        excel_path = out_dir / f"{prefix}_v{profile.version}.xlsx"
        if profile.report and profile.report.excel:
            render_excel(profile, result, excel_path)
            generados.append(excel_path.name)

        pbip_dir = out_dir / f"pbip_{profile_id}_v{profile.version}"
        if pbip_dir.exists():
            shutil.rmtree(pbip_dir)
        render_pbip(profile, result, pbip_dir)
        pbip_zip = out_dir / f"pbip_{profile_id}_v{profile.version}.zip"
        with zipfile.ZipFile(pbip_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(pbip_dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(pbip_dir.parent))
        generados.append(pbip_zip.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=(
                "No se pudieron generar los entregables por un problema en "
                f"el diseno del reporte: {type(exc).__name__}: {exc}. "
                "Usa 'refine' para que los agentes ajusten la propuesta."
            ),
        ) from exc
    return generados


@router.post("/{profile_id}/generate")
def post_generate(
    profile_id: str,
    body: RunIn,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Ejecuta el profile aprobado Y genera los entregables descargables:
    Excel formateado (con la base de datos incluida como hojas-tabla) y
    proyecto Power BI (PBIP) comprimido en zip.

    Asincrono: responde al instante con `job_id`; el cruce + render pesado corre
    en segundo plano y el frontend consulta GET /jobs/{id}."""
    with get_conn() as conn:
        _assert_profile_run_access(conn, profile_id, user)
        # Verificacion rapida de aprobacion para feedback inmediato (409),
        # antes de lanzar el trabajo pesado en segundo plano.
        try:
            _prof = load_profile(conn, profile_id, body.version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _row = conn.execute(
            "SELECT status FROM profiles WHERE profile_id = ? AND version = ?",
            (profile_id, _prof.version),
        ).fetchone()
        if _row is None or _row["status"] != "approved":
            raise HTTPException(
                status_code=409,
                detail=f"El profile '{profile_id}' v{_prof.version} no esta aprobado.",
            )
    version = body.version
    parameters = body.parameters

    def _trabajo() -> dict[str, Any]:
        with get_conn() as conn:
            profile, result = _ejecutar_aprobado(conn, profile_id, version, parameters)
            info = persist_run(conn, result)
            generados = _render_entregables(profile, result, profile_id)
            return {
                "ok": True,
                "run": info,
                "summary": result.summary(),
                "kpis": result.kpis,
                "archivos": generados,
            }

    job_id = create_job(f"generate:{profile_id}", user.user_id)
    run_job(job_id, _trabajo)
    return {"ok": True, "job_id": job_id}


@router.post("/{profile_id}/reejecutar")
async def post_reejecutar(
    profile_id: str,
    left_file: UploadFile = File(...),
    right_file: UploadFile = File(...),
    homologacion_file: UploadFile | None = File(default=None),
    version: int | None = Form(default=None),
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Reejecuta un profile APROBADO (plantilla) con archivos NUEVOS, SIN pasar
    por los agentes ni el chat.

    Es la reutilizacion del conocimiento: la persona hizo la entrevista una vez,
    aprobo, y ahora repite exactamente el mismo cruce con archivos de otro
    periodo. Sube los dos archivos (y homologacion opcional), el motor
    deterministico corre el profile aprobado tal cual, se persiste el run en el
    historico y se generan los entregables. Cero costo LLM, reproducible.

    Si la estructura del archivo nuevo no cuadra con el loader guardado (faltan
    columnas/hojas), responde 422 con el detalle para que el usuario suba el
    archivo correcto o reabra la entrevista.
    """
    with get_conn() as conn:
        _assert_profile_run_access(conn, profile_id, user)
        try:
            profile = load_profile(conn, profile_id, version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        row = conn.execute(
            "SELECT status FROM profiles WHERE profile_id = ? AND version = ?",
            (profile_id, profile.version),
        ).fetchone()
        if row is None or row["status"] != "approved":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El profile '{profile_id}' v{profile.version} no esta "
                    "aprobado; solo un proceso aprobado se puede reutilizar como "
                    "plantilla."
                ),
            )

        left = _save_upload(profile_id, left_file, "left")
        right = _save_upload(profile_id, right_file, "right")
        homologacion: Path | None = None
        if homologacion_file and homologacion_file.filename:
            homologacion = _save_upload(profile_id, homologacion_file, "homologacion")
        _registrar_archivos(conn, profile_id, left, right, homologacion)
        if homologacion:
            _importar_homologacion_si_aplica(conn, profile_id, homologacion)

    def _trabajo() -> dict[str, Any]:
        with get_conn() as conn2:
            profile2, result = _ejecutar_aprobado(conn2, profile_id, version, {})
            info = persist_run(conn2, result)
            generados = _render_entregables(profile2, result, profile_id)
            return {
                "ok": True,
                "run": info,
                "summary": result.summary(),
                "kpis": result.kpis,
                "archivos": generados,
                "reejecucion": True,
            }

    job_id = create_job(f"reejecutar:{profile_id}", user.user_id)
    run_job(job_id, _trabajo)
    return {"ok": True, "job_id": job_id, "reejecucion": True}


@router.get("/{profile_id}/downloads")
def get_downloads(
    profile_id: str,
    user: AuthUser = Depends(current_user),
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        _assert_profile_download_access(conn, profile_id, user)
    out_dir = _PROFILE_OUTPUTS / profile_id
    if not out_dir.exists():
        return []
    archivos = []
    for f in sorted(out_dir.iterdir()):
        if f.is_file():
            kind = "excel" if f.suffix == ".xlsx" else "pbip" if f.suffix == ".zip" else "otro"
            archivos.append(
                {"filename": f.name, "size_bytes": f.stat().st_size, "kind": kind}
            )
    return archivos


@router.get("/{profile_id}/downloads/{filename}")
def get_download(
    profile_id: str,
    filename: str,
    user: AuthUser = Depends(current_user),
):
    from fastapi.responses import FileResponse

    with get_conn() as conn:
        _assert_profile_download_access(conn, profile_id, user)
    out_dir = (_PROFILE_OUTPUTS / profile_id).resolve()
    target = (out_dir / filename).resolve()
    if not str(target).startswith(str(out_dir)) or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(target, filename=target.name)


@router.get("/{profile_id}/telemetry")
def get_telemetry(
    profile_id: str,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    with get_conn() as conn:
        _assert_profile_read_access(conn, profile_id, user)
        return telemetry_summary(conn, profile_id)
