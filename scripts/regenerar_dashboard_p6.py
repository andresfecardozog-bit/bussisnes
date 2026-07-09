"""Regenera entregables CEN P6 vs SAP junio con el perfil borrador validado.

Usa `profiles/cen_vs_sap_v1_borrador.json` (numeros verificados a mano:
5,533 lineas cruzadas, 36.3% completos) y los renderers actuales del repo
(con las mejoras 4B: drill pages, theme corporativo con dropShadow, cards
grandes, variedad de charts). Sirve como camino deterministico cuando la
entrevista LLM propone un join incompatible con el motor.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.platform.engine import run_profile  # noqa: E402
from app.platform.profile import MatchProfile  # noqa: E402
from app.platform.render_excel import render_excel  # noqa: E402
from app.platform.render_pbip import render_pbip  # noqa: E402

PROFILE_PATH = ROOT / "profiles" / "cen_vs_sap_v1_borrador.json"
CEN_PATH = ROOT / "data_nivel_cumplimiento" / "2026" / "Acumulado CEN P6 2026.xlsx"
SAP_PATH = ROOT / "data_nivel_cumplimiento" / "data meses" / "junio.XLS"
OUT_DIR = ROOT / "data" / "outputs" / "profiles" / "regen_cen_p6_junio_v3"


def main() -> int:
    profile = MatchProfile.from_json(PROFILE_PATH.read_text(encoding="utf-8"))
    print(f"Perfil: {profile.profile_id} v{profile.version}")
    print(f"Fuentes: {CEN_PATH.name}  |  {SAP_PATH.name}")

    result = run_profile(profile, left_path=CEN_PATH, right_path=SAP_PATH)
    summary = result.summary()
    print("Resumen del cruce:")
    for k, v in summary.items():
        print(f"  {k}: {v:,}")

    kpis_plano = {
        k: v for k, v in result.kpis.items() if not isinstance(v, (dict, list))
    }
    print("KPIs planos:")
    for k, v in kpis_plano.items():
        print(f"  {k}: {v}")

    sl = result.service_level or {}
    ped = sl.get("pedidos", {})
    if ped:
        print("Pedidos:")
        print(f"  total: {ped.get('total')}")
        for clase, info in (ped.get("clases") or {}).items():
            print(f"  {clase}: {info}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    excel_path = OUT_DIR / f"NivelServicio_CEN_SAP_P6_junio_v3.xlsx"
    render_excel(profile, result, excel_path)
    print(f"Excel: {excel_path}  ({excel_path.stat().st_size:,} bytes)")

    pbip_dir = OUT_DIR / "pbip_cen_p6_junio_v3"
    render_pbip(profile, result, pbip_dir)
    pbip_root = pbip_dir / f"{profile.profile_id}.pbip"
    print(f"PBIP:  {pbip_root}  (dir: {pbip_dir})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
