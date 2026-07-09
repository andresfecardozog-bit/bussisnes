"""Exploracion Fase 3: cruza los meses reales CEN vs SAP con el borrador.

NO persiste nada (el profile borrador no esta aprobado). Produce las
cifras que alimentan la entrevista con la analista: tasa de cruce por mes,
ordenes CEN sin entrega, entregas SAP sin orden CEN del periodo, y la
distribucion de formatos del numero de pedido en la col 56 del SAP.

Uso:
    venv\\Scripts\\python.exe scripts/explorar_cen_vs_sap.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import re  # noqa: E402

from app.platform.engine import prepare_source, run_profile  # noqa: E402
from app.platform.profile import MatchProfile  # noqa: E402

DATA = PROJECT_ROOT / "data_nivel_cumplimiento"
PROFILE = PROJECT_ROOT / "profiles" / "cen_vs_sap_v1_borrador.json"

PARES = [
    ("P1", "enero.XLS"),
    ("P2", "Febrero.XLS"),
    ("P3", "Marzo.XLS"),
    ("P4", "abril.XLS"),
    ("P5", "Mayo.XLS"),
    ("P6", "junio.XLS"),
]

_CEN_ORDER_RE = re.compile(r"^\d{3}-\d+$")


def main() -> int:
    profile = MatchProfile.from_json(PROFILE.read_text(encoding="utf-8"))

    print("=" * 78)
    print("EXPLORACION CEN vs SAP (borrador, sin persistir)")
    print("=" * 78)

    for p, mes in PARES:
        cen_path = DATA / "2026" / f"Acumulado CEN {p} 2026.xlsx"
        sap_path = DATA / "data meses" / mes
        if not cen_path.exists() or not sap_path.exists():
            print(f"{p} vs {mes}: archivos faltantes, skip")
            continue
        try:
            result = run_profile(profile, left_path=cen_path, right_path=sap_path)
        except Exception as exc:
            print(f"{p} vs {mes}: FALLO -> {type(exc).__name__}: {exc}")
            continue
        s = result.summary()
        total_left = s["matched"] + s["solo_left"]
        tasa = s["matched"] / total_left * 100 if total_left else 0
        print(
            f"{p} vs {mes:12s} matched={s['matched']:6,} "
            f"solo_cen={s['solo_left']:6,} solo_sap={s['solo_right']:6,} "
            f"tasa_cruce_cen={tasa:5.1f}% "
            f"cumplimiento={result.kpis.get('cumplimiento_entregas_pct')}"
        )
        sl = result.kpis.get("service_level")
        if sl and "pedidos" in sl:
            ped = sl["pedidos"]
            clases = ped["clases"]
            print(
                f"    pedidos={ped['total']:,}: "
                f"completos={clases['completo']['pedidos']:,} ({clases['completo']['pct']}%), "
                f"parciales={clases['parcial']['pedidos']:,} ({clases['parcial']['pct']}%), "
                f"no_entregados={clases['no_entregado']['pedidos']:,} ({clases['no_entregado']['pct']}%)"
            )
        dev = result.breakdowns.get("devoluciones_por_motivo")
        if dev is not None and not dev.empty:
            top = dev.head(3)
            motivos = "; ".join(
                f"{r['motivo_devolucion']}: {r['unidades_devueltas']:,.0f} un"
                for _, r in top.iterrows()
            )
            print(f"    devoluciones top: {motivos}")

    # distribucion de formatos de pedido en col 56 del SAP (pregunta clave)
    print("-" * 78)
    print("Formatos del numero de pedido (SAP col 56), muestra enero:")
    sap_df, _ = prepare_source(
        DATA / "data meses" / "enero.XLS", profile.right, {}
    )
    ordenes = sap_df["numero_pedido"].dropna().astype(str)
    con_formato_cen = ordenes[ordenes.str.match(_CEN_ORDER_RE)].nunique()
    sin_formato_cen = ordenes[~ordenes.str.match(_CEN_ORDER_RE)].nunique()
    print(f"  pedidos formato CEN (NNN-...): {con_formato_cen:,}")
    print(f"  pedidos otros formatos:        {sin_formato_cen:,}")
    ejemplos = ordenes[~ordenes.str.match(_CEN_ORDER_RE)].unique()[:8]
    print(f"  ejemplos otros formatos: {list(ejemplos)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
