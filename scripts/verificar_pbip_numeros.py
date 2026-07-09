"""Verifica coherencia numerica de un PBIP generado (gate 4B-F).

Cruza 3 capas:
1) Motor deterministico (`run_profile`) sobre las fuentes originales.
2) Fact exportado en el PBIP (`data/<fact>.csv`).
3) Expresiones DAX esperadas en TMDL para las medidas de service_level.

Uso recomendado (fixture CEN/SAP):
    python scripts/verificar_pbip_numeros.py \
      --profile profiles/cen_vs_sap_v1_borrador.json \
      --left tests/fixtures/cen/cen_junio_muestra.xlsx \
      --right tests/fixtures/cen/sap_junio_muestra.xlsx \
      --pbip-dir data/outputs/profiles/<profile_id>/pbip_<profile_id>_v2

Si no se pasa `--pbip-dir`, el script genera un PBIP temporal y valida ese.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.platform.engine import GenericMatchResult, run_profile  # noqa: E402
from app.platform.profile import MatchProfile  # noqa: E402
from app.platform.render_pbip import render_pbip  # noqa: E402


def _resolve_fact_col(df: pd.DataFrame, base_name: str) -> str | None:
    for cand in (base_name, f"{base_name}_left", f"{base_name}_right", f"key_{base_name}"):
        if cand in df.columns:
            return cand
    return None


def _metricas_motor(profile: MatchProfile, result: GenericMatchResult) -> dict[str, float | None]:
    sl = result.service_level or {}
    clases = sl.get("clases", {})
    pedidos = sl.get("pedidos", {}).get("clases", {})
    total_plan = float(sl.get("total_unidades_plan", 0.0))
    entregadas = float(result.kpis.get("unidades_entregadas", 0.0))
    sin_pedido = float(clases.get("sin_pedido", {}).get("unidades_real", 0.0))
    pedidos_completos_pct = pedidos.get("completo", {}).get("pct")
    if pedidos_completos_pct is not None:
        pedidos_completos_pct = float(pedidos_completos_pct)
    ns_unidades_pct = None
    if total_plan > 0:
        ns_unidades_pct = entregadas / total_plan * 100.0
    _ = profile
    return {
        "unidades_pedidas": total_plan,
        "unidades_entregadas": entregadas,
        "unidades_sin_pedido": sin_pedido,
        "ns_unidades_pct": ns_unidades_pct,
        "pedidos_completos_pct": pedidos_completos_pct,
    }


def _metricas_fact(profile: MatchProfile, fact: pd.DataFrame) -> dict[str, float | None]:
    if profile.service_level is None:
        raise ValueError("El profile no define service_level; no hay metrica comparable.")
    plan_col = _resolve_fact_col(fact, profile.service_level.plan_column)
    real_col = _resolve_fact_col(fact, profile.service_level.real_column)
    pedido_col = (
        _resolve_fact_col(fact, profile.service_level.pedido_key)
        if profile.service_level.pedido_key
        else None
    )
    if not plan_col or not real_col:
        raise ValueError(
            "No se encontraron en el fact las columnas plan/real del service_level."
        )

    left_states = {"cruzado", f"solo_{profile.left.role}"}
    right_only = f"solo_{profile.right.role}"

    fact_left = fact[fact["estado_cruce"].isin(left_states)]
    fact_cruzado = fact[fact["estado_cruce"] == "cruzado"]
    fact_sin_pedido = fact[fact["estado_cruce"] == right_only]

    unidades_pedidas = float(pd.to_numeric(fact_left[plan_col], errors="coerce").fillna(0).sum())
    unidades_entregadas = float(
        pd.to_numeric(fact_cruzado[real_col], errors="coerce").fillna(0).sum()
    )
    unidades_sin_pedido = float(
        pd.to_numeric(fact_sin_pedido[real_col], errors="coerce").fillna(0).sum()
    )
    ns_unidades_pct = None
    if unidades_pedidas > 0:
        ns_unidades_pct = unidades_entregadas / unidades_pedidas * 100.0

    pedidos_completos_pct = None
    if pedido_col and "nivel_servicio" in fact.columns:
        pedidos_df = fact_left[fact_left[pedido_col].notna()].copy()
        if not pedidos_df.empty:
            agg = pedidos_df.groupby(pedido_col, dropna=False)["nivel_servicio"].apply(
                lambda s: bool((s.astype("string") == "completo").all())
            )
            pedidos_completos_pct = float(agg.mean() * 100.0)

    return {
        "unidades_pedidas": unidades_pedidas,
        "unidades_entregadas": unidades_entregadas,
        "unidades_sin_pedido": unidades_sin_pedido,
        "ns_unidades_pct": ns_unidades_pct,
        "pedidos_completos_pct": pedidos_completos_pct,
    }


def _expected_dax_fragments(profile: MatchProfile, fact_name: str, fact: pd.DataFrame) -> list[str]:
    if profile.service_level is None:
        return []
    plan_col = _resolve_fact_col(fact, profile.service_level.plan_column)
    real_col = _resolve_fact_col(fact, profile.service_level.real_column)
    if not plan_col or not real_col:
        return []
    left = f"solo_{profile.left.role}"
    right = f"solo_{profile.right.role}"
    return [
        (
            f'CALCULATE(SUM({fact_name}[{real_col}]), '
            f'{fact_name}[estado_cruce] IN {{"cruzado"}})'
        ),
        (
            f'CALCULATE(SUM({fact_name}[{plan_col}]), '
            f'{fact_name}[estado_cruce] IN {{"cruzado", "{left}"}})'
        ),
        (
            f'CALCULATE(SUM({fact_name}[{real_col}]), '
            f'{fact_name}[estado_cruce] IN {{"{right}"}})'
        ),
    ]


@dataclass
class CheckResult:
    name: str
    motor: float | None
    fact: float | None
    diff: float | None
    ok: bool


def _comparar(metricas_motor: dict[str, float | None], metricas_fact: dict[str, float | None], tol: float) -> list[CheckResult]:
    checks: list[CheckResult] = []
    keys = [
        "unidades_pedidas",
        "unidades_entregadas",
        "unidades_sin_pedido",
        "ns_unidades_pct",
        "pedidos_completos_pct",
    ]
    for k in keys:
        m = metricas_motor.get(k)
        f = metricas_fact.get(k)
        if m is None or f is None:
            checks.append(CheckResult(k, m, f, None, False))
            continue
        d = abs(float(m) - float(f))
        checks.append(CheckResult(k, float(m), float(f), d, d <= tol))
    return checks


def _cargar_fact(pbip_dir: Path, profile: MatchProfile) -> tuple[pd.DataFrame, Path]:
    fact_name = profile.data_model.fact_name if profile.data_model else "FactCruce"
    fact_path = pbip_dir / "data" / f"{fact_name}.csv"
    if not fact_path.exists():
        raise FileNotFoundError(f"No existe el fact CSV esperado: {fact_path}")
    return pd.read_csv(fact_path), fact_path


def _cargar_tmdl(pbip_dir: Path, profile: MatchProfile) -> str:
    fact_name = profile.data_model.fact_name if profile.data_model else "FactCruce"
    tmdl_path = (
        pbip_dir
        / f"{profile.profile_id}.SemanticModel"
        / "definition"
        / "tables"
        / f"{fact_name}.tmdl"
    )
    if not tmdl_path.exists():
        raise FileNotFoundError(f"No existe TMDL del fact esperado: {tmdl_path}")
    return tmdl_path.read_text(encoding="utf-8")


def run_verificacion(
    profile_path: Path,
    left_path: Path,
    right_path: Path,
    pbip_dir: Path | None = None,
    tolerance: float = 0.01,
) -> dict[str, Any]:
    profile = MatchProfile.from_json(profile_path.read_text(encoding="utf-8"))
    result = run_profile(profile, left_path=left_path, right_path=right_path)

    cleanup_tmp = False
    if pbip_dir is None:
        pbip_dir = PROJECT_ROOT / "data" / "outputs" / "_verify_pbip_tmp" / profile.profile_id
        if pbip_dir.exists():
            shutil.rmtree(pbip_dir)
        render_pbip(profile, result, pbip_dir)
        cleanup_tmp = True
    else:
        pbip_dir = pbip_dir.resolve()

    fact, fact_path = _cargar_fact(pbip_dir, profile)
    metricas_m = _metricas_motor(profile, result)
    metricas_f = _metricas_fact(profile, fact)
    checks = _comparar(metricas_m, metricas_f, tolerance)

    fact_name = profile.data_model.fact_name if profile.data_model else "FactCruce"
    tmdl = _cargar_tmdl(pbip_dir, profile)
    expected_fragments = _expected_dax_fragments(profile, fact_name, fact)
    dax_checks = [
        {"fragment": frag, "ok": frag in tmdl}
        for frag in expected_fragments
    ]

    ok = all(c.ok for c in checks) and all(d["ok"] for d in dax_checks)
    payload = {
        "ok": ok,
        "profile_id": profile.profile_id,
        "fact_csv": str(fact_path),
        "pbip_dir": str(pbip_dir),
        "tolerance": tolerance,
        "checks": [
            {
                "name": c.name,
                "motor": c.motor,
                "fact": c.fact,
                "diff": c.diff,
                "ok": c.ok,
            }
            for c in checks
        ],
        "dax_checks": dax_checks,
    }

    if cleanup_tmp:
        payload["nota"] = (
            "PBIP generado temporalmente en data/outputs/_verify_pbip_tmp "
            "para esta validacion."
        )
    return payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verifica numeros del PBIP contra el motor.")
    p.add_argument("--profile", required=True, help="Ruta al MatchProfile (.json)")
    p.add_argument("--left", required=True, help="Fuente izquierda")
    p.add_argument("--right", required=True, help="Fuente derecha")
    p.add_argument(
        "--pbip-dir",
        default="",
        help="Carpeta PBIP ya generada. Si se omite, se genera temporalmente.",
    )
    p.add_argument(
        "--tolerance",
        type=float,
        default=0.01,
        help="Tolerancia absoluta para comparar metricas (default: 0.01).",
    )
    p.add_argument(
        "--json-out",
        default="",
        help="Si se define, escribe el resultado en JSON en esa ruta.",
    )
    return p.parse_args(argv)


def _print_summary(payload: dict[str, Any]) -> None:
    print("=" * 72)
    print("Verificacion PBIP (motor vs fact CSV vs DAX esperado)")
    print("=" * 72)
    print(f"Profile: {payload['profile_id']}")
    print(f"PBIP:    {payload['pbip_dir']}")
    print(f"Fact:    {payload['fact_csv']}")
    print(f"Tol:     {payload['tolerance']}")
    print("-" * 72)
    for c in payload["checks"]:
        if c["diff"] is None:
            diff_txt = "n/a"
        else:
            diff_txt = f"{c['diff']:.6f}"
        estado = "OK" if c["ok"] else "FAIL"
        print(
            f"[{estado:4s}] {c['name']:24s} "
            f"motor={c['motor']!s:>12s} fact={c['fact']!s:>12s} diff={diff_txt}"
        )
    print("-" * 72)
    for i, d in enumerate(payload["dax_checks"], start=1):
        estado = "OK" if d["ok"] else "FAIL"
        print(f"[{estado:4s}] DAX fragmento #{i}")
    print("-" * 72)
    print("RESULTADO:", "OK" if payload["ok"] else "FAIL")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = run_verificacion(
        profile_path=Path(args.profile).resolve(),
        left_path=Path(args.left).resolve(),
        right_path=Path(args.right).resolve(),
        pbip_dir=Path(args.pbip_dir).resolve() if args.pbip_dir else None,
        tolerance=float(args.tolerance),
    )
    if args.json_out:
        out = Path(args.json_out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _print_summary(payload)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
