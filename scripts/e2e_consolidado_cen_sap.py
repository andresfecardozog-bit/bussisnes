"""Ejercicio completo CEN vs SAP: consolidado + por mes.

Reproduce el caso de validacion de la plataforma sobre TODA la base real:
- CEN: Acumulado CEN P1..P6 2026.xlsx (7 periodos; P7 aun sin mes SAP par).
- SAP: data meses/{enero..junio} (xlsx renombrados a .XLS).

Usa el perfil validado `profiles/cen_vs_sap_v1_borrador.json`, que codifica
exactamente el mapeo, las llaves de cruce, las normalizaciones y el contexto
de negocio acordado con los agentes en la entrevista (grano linea-item,
join CEN 'Numero de la Orden de compra' vs SAP col56 + item vs col40,
devoluciones = tipo_operacion DEVOLUCIONES con motivo col62, canales TAT/
puntos propios venden directo sin orden CEN, etc.).

Salidas (Excel corporativo + proyecto Power BI PBIP) por cada mes y una
consolidada que junta toda la data en una sola base, reutilizando el motor
deterministico y los renderers del repo. No reimplementa logica de cruce:
concatena las fuentes ya cargadas y llama a las mismas funciones internas
del engine para garantizar identidad numerica con el flujo normal.

Uso:
    venv\\Scripts\\python.exe scripts/e2e_consolidado_cen_sap.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.platform.engine import (  # noqa: E402
    GenericMatchResult,
    _apply_computed,
    _apply_transform,
    _build_no_cruzados,
    _classify_service_level,
    _compute_kpis,
    _join,
    _prenormalize_join_keys,
    _run_breakdown,
)
from app.platform.loader import load_source  # noqa: E402
from app.platform.profile import MatchProfile  # noqa: E402
from app.platform.render_excel import render_excel  # noqa: E402
from app.platform.render_pbip import render_pbip  # noqa: E402

PROFILE_PATH = ROOT / "profiles" / "cen_vs_sap_v1_borrador.json"
DATA = ROOT / "data_nivel_cumplimiento"
OUT_ROOT = ROOT / "data" / "outputs" / "profiles" / "cen_sap_ejercicio_completo"

# Pares periodo CEN <-> mes SAP (solo los que tienen ambos lados).
PARES: list[tuple[str, str, str]] = [
    ("P1", "enero.XLS", "01_enero"),
    ("P2", "Febrero.XLS", "02_febrero"),
    ("P3", "Marzo.XLS", "03_marzo"),
    ("P4", "abril.XLS", "04_abril"),
    ("P5", "Mayo.XLS", "05_mayo"),
    ("P6", "junio.XLS", "06_junio"),
]


def _cen_path(periodo: str) -> Path:
    return DATA / "2026" / f"Acumulado CEN {periodo} 2026.xlsx"


def _sap_path(mes_file: str) -> Path:
    return DATA / "data meses" / mes_file


def _run_profile_multi(
    profile: MatchProfile,
    left_paths: list[Path],
    right_paths: list[Path],
    parameters: dict[str, Any] | None = None,
) -> GenericMatchResult:
    """Como engine.run_profile pero concatenando varias fuentes por lado.

    Carga cada archivo con el loader del perfil, concatena las fuentes ya
    normalizadas por columnas y ejecuta el resto del pipeline con las MISMAS
    funciones internas del motor (prenormalizacion de llaves, transforms,
    join outer, computed, KPIs, service level, breakdowns). Asi la version
    consolidada es numericamente identica a sumar los cruces mensuales.
    """
    parameters = parameters or {}
    left_keys = [(k.left, list(k.normalizers)) for k in profile.join.keys]
    right_keys = [(k.right, list(k.normalizers)) for k in profile.join.keys]

    left_raws: list[pd.DataFrame] = []
    for p in left_paths:
        df, _ = load_source(p, profile.left.loader)
        left_raws.append(df)
    left_raw = pd.concat(left_raws, ignore_index=True, sort=False)

    right_raws: list[pd.DataFrame] = []
    for p in right_paths:
        df, _ = load_source(p, profile.right.loader)
        right_raws.append(df)
    right_raw = pd.concat(right_raws, ignore_index=True, sort=False)

    left_df = _prenormalize_join_keys(left_raw, left_keys)
    for transform in profile.left.transforms:
        left_df = _apply_transform(left_df, transform, parameters)
    left_df = left_df.reset_index(drop=True)

    right_df = _prenormalize_join_keys(right_raw, right_keys)
    for transform in profile.right.transforms:
        right_df = _apply_transform(right_df, transform, parameters)
    right_df = right_df.reset_index(drop=True)

    merged, key_cols = _join(left_df, right_df, profile.join)
    matched = merged[merged["_merge"] == "both"].drop(columns=["_merge"]).copy()
    solo_left = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
    solo_right = merged[merged["_merge"] == "right_only"].drop(columns=["_merge"]).copy()

    matched = _apply_computed(matched, profile.computed)
    kpis = _compute_kpis(matched, profile.kpis)
    no_cruzados = _build_no_cruzados(solo_left, solo_right, key_cols, profile)

    service_level_block: dict[str, Any] | None = None
    if profile.service_level:
        matched, service_level_block = _classify_service_level(
            matched, solo_left, solo_right, profile.service_level
        )
        kpis["service_level"] = service_level_block

    breakdowns: dict[str, pd.DataFrame] = {}
    for bd in profile.breakdowns:
        breakdowns[bd.id] = _run_breakdown(bd, matched, solo_left, right_raw)

    result = GenericMatchResult(
        profile_id=profile.profile_id,
        profile_version=profile.version,
        parameters=parameters,
        matched=matched.reset_index(drop=True),
        solo_left=solo_left.reset_index(drop=True),
        solo_right=solo_right.reset_index(drop=True),
        no_cruzados=no_cruzados,
        kpis=kpis,
        left_meta={"archivos": [p.name for p in left_paths]},
        right_meta={"archivos": [p.name for p in right_paths]},
        service_level=service_level_block,
        breakdowns=breakdowns,
    )
    result.verify_accounting(len(left_df), len(right_df))
    return result


def _print_result(etiqueta: str, result: GenericMatchResult) -> dict[str, Any]:
    s = result.summary()
    total_left = s["matched"] + s["solo_left"]
    tasa = s["matched"] / total_left * 100 if total_left else 0.0
    cumpl = result.kpis.get("cumplimiento_entregas_pct")
    print(f"  cruce: matched={s['matched']:,} solo_cen={s['solo_left']:,} "
          f"solo_sap={s['solo_right']:,} tasa_cruce_cen={tasa:.1f}% "
          f"cumplimiento={cumpl}")
    ped = (result.service_level or {}).get("pedidos")
    if ped:
        c = ped["clases"]
        print(f"  pedidos={ped['total']:,}: "
              f"completos={c['completo']['pedidos']:,} ({c['completo']['pct']}%), "
              f"parciales={c['parcial']['pedidos']:,} ({c['parcial']['pct']}%), "
              f"no_entregados={c['no_entregado']['pedidos']:,} ({c['no_entregado']['pct']}%)")
    dev = result.breakdowns.get("devoluciones_por_motivo")
    if dev is not None and not dev.empty:
        tot = pd.to_numeric(dev["unidades_devueltas"], errors="coerce").sum()
        print(f"  devoluciones: {tot:,.0f} unidades en {len(dev)} motivos")
    return {"etiqueta": etiqueta, "summary": s, "cumplimiento": cumpl}


def _render(profile: MatchProfile, result: GenericMatchResult, out_dir: Path,
            slug: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    excel_path = out_dir / f"NivelServicio_{slug}.xlsx"
    render_excel(profile, result, excel_path)
    print(f"  Excel: {excel_path.name} ({excel_path.stat().st_size:,} bytes)")
    pbip_dir = out_dir / f"pbip_{slug}"
    render_pbip(profile, result, pbip_dir)
    print(f"  PBIP:  {pbip_dir.name}/{profile.profile_id}.pbip")


def main() -> int:
    profile = MatchProfile.from_json(PROFILE_PATH.read_text(encoding="utf-8"))
    print("=" * 78)
    print(f"EJERCICIO COMPLETO CEN vs SAP  (perfil {profile.profile_id} v{profile.version})")
    print("=" * 78)

    disponibles = [
        (p, mes, slug) for (p, mes, slug) in PARES
        if _cen_path(p).exists() and _sap_path(mes).exists()
    ]
    faltantes = [
        (p, mes) for (p, mes, _) in PARES
        if not (_cen_path(p).exists() and _sap_path(mes).exists())
    ]
    if faltantes:
        print(f"Pares sin ambos lados (omitidos): {faltantes}")

    resumen: list[dict[str, Any]] = []

    print("\n--- REPORTES POR MES ---")
    for periodo, mes, slug in disponibles:
        print(f"\n[{periodo} vs {mes}]")
        t0 = time.monotonic()
        result = _run_profile_multi(profile, [_cen_path(periodo)], [_sap_path(mes)])
        info = _print_result(slug, result)
        _render(profile, result, OUT_ROOT / "por_mes" / slug, slug)
        info["segundos"] = round(time.monotonic() - t0, 1)
        resumen.append(info)

    print("\n--- REPORTE CONSOLIDADO (toda la base en una sola) ---")
    left_all = [_cen_path(p) for (p, _, _) in disponibles]
    right_all = [_sap_path(mes) for (_, mes, _) in disponibles]
    t0 = time.monotonic()
    consolidado = _run_profile_multi(profile, left_all, right_all)
    info = _print_result("consolidado", consolidado)
    _render(profile, consolidado, OUT_ROOT / "consolidado", "CEN_SAP_consolidado_2026")
    info["segundos"] = round(time.monotonic() - t0, 1)
    resumen.append(info)

    print("\n" + "=" * 78)
    print("RESUMEN GENERAL")
    print("=" * 78)
    for r in resumen:
        s = r["summary"]
        print(f"  {r['etiqueta']:28s} matched={s['matched']:>7,} "
              f"cumplimiento={r['cumplimiento']} ({r['segundos']}s)")
    print(f"\nEntregables en: {OUT_ROOT}")
    print("EJERCICIO COMPLETO OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
