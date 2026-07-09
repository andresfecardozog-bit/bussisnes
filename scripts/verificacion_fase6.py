"""Runner de verificacion intensiva (Fase 6).

Ejecuta checks automatizables y resume los pendientes manuales de rubrica.
No reemplaza la validacion humana final, pero deja evidencia reproducible.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class StepResult:
    name: str
    command: str
    exit_code: int
    ok: bool


def _run(command: str, cwd: Path) -> StepResult:
    print(f"\n[RUN] {command}")
    p = subprocess.run(command, shell=True, cwd=str(cwd))
    return StepResult(
        name=command.split()[0],
        command=command,
        exit_code=p.returncode,
        ok=p.returncode == 0,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verificacion intensiva fase 6.")
    p.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter para comandos python (default: actual).",
    )
    p.add_argument(
        "--skip-frontend",
        action="store_true",
        help="No correr build del frontend.",
    )
    p.add_argument(
        "--skip-pbip-check",
        action="store_true",
        help="No correr scripts/verificar_pbip_numeros.py",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    py = args.python
    results: list[StepResult] = []

    results.append(
        _run(
            f'{py} -m pytest tests/test_platform_engine.py tests/test_render_excel.py '
            "tests/test_render_pbip.py tests/test_api_profiles.py tests/test_verificar_pbip_numeros.py -q",
            ROOT,
        )
    )
    if not args.skip_pbip_check:
        results.append(
            _run(
                f'{py} scripts/verificar_pbip_numeros.py '
                "--profile profiles/cen_vs_sap_v1_borrador.json "
                "--left tests/fixtures/cen/cen_junio_muestra.xlsx "
                "--right tests/fixtures/cen/sap_junio_muestra.xlsx",
                ROOT,
            )
        )
    if not args.skip_frontend:
        results.append(_run("npm run build", ROOT / "frontend"))

    print("\n" + "=" * 72)
    print("Resumen verificacion Fase 6")
    print("=" * 72)
    for r in results:
        estado = "OK" if r.ok else "FAIL"
        print(f"[{estado:4s}] exit={r.exit_code} :: {r.command}")

    ok = all(r.ok for r in results)
    print("-" * 72)
    print("Estado checks automatizados:", "OK" if ok else "FAIL")
    print("\nPendientes manuales (rubrica fase 4/5/6):")
    print("- Abrir PBIP en Power BI Desktop y validar refresh + checklist visual.")
    print("- E2E completo via UI con usuario no tecnico (flujo guiado).")
    print("- Ejecutar bateria adversa manual (archivos corruptos/hojas renombradas/etc).")
    print("- Completar informe de presupuesto con costos medidos por periodo.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
