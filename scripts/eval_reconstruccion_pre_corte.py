"""Gate (a) de la Fase 2 (rubrica.md): reconstruccion del profile PRE CORTE.

Corre el equipo de agentes REAL (Gemini 2.5 Flash, requiere GEMINI_API_KEY
en .env) dandole solo los archivos del fixture + el brief "medir
cumplimiento de produccion", y mide que porcentaje de los campos criticos
del profile propuesto coincide con profiles/pre_corte_v1.json hecho a mano.

Uso:
    venv\\Scripts\\python.exe scripts/eval_reconstruccion_pre_corte.py

Imprime el desglose campo por campo y el score final. El gate exige >=80%.
Registra el resultado para pegarlo en rubrica.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.crew import Crew  # noqa: E402
from app.agents.orchestrator import propose_profile  # noqa: E402
from app.core.db import get_conn, init_db  # noqa: E402
from app.platform.profile import MatchProfile  # noqa: E402

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
REFERENCIA = PROJECT_ROOT / "profiles" / "pre_corte_v1.json"


def _source_of(profile: MatchProfile, lado: str, col_name: str) -> str | None:
    """A que columna del ARCHIVO apunta el nombre destino `col_name`.

    Permite comparar semantica en vez de nombres inventados por el agente.
    Sigue el linaje a traves de group_by_aggregate y select_rename.
    """
    side = profile.left if lado == "left" else profile.right
    # seguir renombres hacia atras
    name = col_name
    for t in reversed(side.transforms):
        op = getattr(t, "op", "")
        if op == "group_by_aggregate":
            for agg in t.aggregations:
                if agg.target == name:
                    name = agg.source
                    break
        elif op == "select_rename":
            for orig, dest in t.mapping.items():
                if dest == name:
                    name = orig
                    break
    loader = side.loader
    if getattr(loader, "type", "") != "tabular":
        return None
    for col in loader.columns:
        if col.name == name:
            return str(col.source).strip().upper()
    return None


_MATERIAL_SOURCES = {"MATERIAL"}
_PLAN_SOURCES = {"NOTIFICADO", "PRODUCIR UNIDADES", "NECESIDAD UNIDADES"}
_REAL_SOURCES = {"CANTIDAD NETA"}
_TEXT_SOURCES = {"REFERENCIA", "NOMB MATERIAL", "DESCRIPCION"}
_DATE_SOURCES = {"FECHA DE FACTURA"}


def _score(propuesto: MatchProfile, referencia: MatchProfile) -> tuple[float, list[str]]:
    """Compara SEMANTICA del cruce (a que columnas fuente apunta cada
    pieza), no nombres de columnas que el agente elige libremente."""
    checks: list[tuple[str, bool]] = []

    # Join por codigo de material en ambos lados
    join_material = any(
        _source_of(propuesto, "left", k.left) in _MATERIAL_SOURCES
        and _source_of(propuesto, "right", k.right) in _MATERIAL_SOURCES
        for k in propuesto.join.keys
    )
    checks.append(("join por codigo MATERIAL en ambos lados", join_material))

    # Sin keys de texto libre (nombres/referencias)
    join_texto = any(
        (_source_of(propuesto, "left", k.left) or "") in _TEXT_SOURCES
        or (_source_of(propuesto, "right", k.right) or "") in _TEXT_SOURCES
        for k in propuesto.join.keys
    )
    checks.append(("sin keys de texto libre en el join", not join_texto))
    checks.append(("join outer", propuesto.join.type == "outer"))

    # KPI principal ratio con numerador=cantidad real, denominador=plan
    prop_ratio = [k for k in propuesto.kpis if k.op == "ratio_pct_of_sums"]
    checks.append(("kpi ratio_pct_of_sums presente", len(prop_ratio) >= 1))
    ratio_ok = False
    for k in prop_ratio:
        num_src = _source_of(propuesto, "right", k.numerator) or _source_of(
            propuesto, "left", k.numerator
        )
        den_src = _source_of(propuesto, "left", k.denominator) or _source_of(
            propuesto, "right", k.denominator
        )
        if num_src in _REAL_SOURCES and den_src in _PLAN_SOURCES:
            ratio_ok = True
    checks.append(("kpi ratio: real/plan semanticamente correcto", ratio_ok))

    # Computed ratio por fila
    checks.append(
        ("computed ratio_pct por fila", any(c.op == "ratio_pct" for c in propuesto.computed))
    )

    # El FLASH acumula el mes: filtro por fecha o al menos pregunta abierta
    filtro_fecha = any(
        getattr(t, "op", "") == "filter_equals"
        and (_source_of(propuesto, lado, t.column) or "") in _DATE_SOURCES
        for lado, side in (("left", propuesto.left), ("right", propuesto.right))
        for t in side.transforms
    )
    checks.append(("filtro por fecha del FLASH", filtro_fecha))

    # Grano: el FLASH tiene material repetido -> group_by en la derecha
    groupby_right = any(
        getattr(t, "op", "") == "group_by_aggregate"
        for t in propuesto.right.transforms
    )
    checks.append(("group_by para ajustar grano del FLASH", groupby_right))

    # Reporte: hoja portada + tabla de no cruzados
    if propuesto.report and propuesto.report.excel:
        kinds = {s.kind for s in propuesto.report.excel.sheets}
        sources = {s.source for s in propuesto.report.excel.sheets}
        checks.append(("report portada", "portada" in kinds))
        checks.append(("report no_cruzados", "no_cruzados" in sources))
    else:
        checks.append(("report portada", False))
        checks.append(("report no_cruzados", False))

    _ = referencia
    total = len(checks)
    ok = sum(1 for _, v in checks if v)
    detalle = [f"[{'OK ' if v else 'FAIL'}] {name}" for name, v in checks]
    return ok / total * 100.0, detalle


def main() -> int:
    init_db()
    referencia = MatchProfile.from_json(REFERENCIA.read_text(encoding="utf-8"))
    crew = Crew()  # falla explicito si no hay GEMINI_API_KEY

    with get_conn() as conn:
        resultado = propose_profile(
            crew,
            conn,
            "eval_pre_corte",
            left_path=FIXTURES / "PRE_CORTE_muestra.xlsx",
            right_path=FIXTURES / "FLASH_muestra.csv",
            brief="medir cumplimiento de produccion",
        )

    propuesto = resultado["profile"]
    score, detalle = _score(propuesto, referencia)

    print("=" * 60)
    print("EVAL: reconstruccion del profile PRE CORTE por agentes")
    print("=" * 60)
    for line in detalle:
        print(" ", line)
    print("-" * 60)
    print(f"SCORE: {score:.1f}% (gate: >=80%)")
    print(f"Preguntas emitidas: {resultado['preguntas_nuevas']}")
    print(f"Confianza global: {resultado['status'].confianza_global}")
    print()
    print("Preguntas de los agentes:")
    from app.agents.orchestrator import list_questions

    with get_conn() as conn:
        for q in list_questions(conn, "eval_pre_corte"):
            marca = "BLOQ" if q["bloqueante"] else "info"
            print(f"  [{marca}] ({q['agente']}) {q['pregunta'][:200]}")
    print()
    print("Profile propuesto:")
    print(propuesto.to_json())
    return 0 if score >= 80.0 else 1


if __name__ == "__main__":
    sys.exit(main())
