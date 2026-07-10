"""Validacion de agentes: reconstruccion del profile CEN vs SAP.

Corre el equipo de agentes REAL (requiere GEMINI_API_KEY) dandole solo los
fixtures CEN/SAP + el brief de negocio, y mide que tanto la propuesta coincide
SEMANTICAMENTE con el profile verificado `profiles/cen_vs_sap_v1_borrador.json`
(la "verdad de referencia" deterministica).

No compara nombres de columnas (el agente los elige libremente): compara a que
columnas del ARCHIVO apunta cada pieza del cruce, y la presencia de las piezas
de negocio clave (doble llave orden+item, exclusion de devoluciones, nivel de
servicio, desgloses).

Uso:
    venv\\Scripts\\python.exe scripts/eval_reconstruccion_cen.py

Gate sugerido: >=80% (ver rubrica.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.crew import Crew  # noqa: E402
from app.agents.orchestrator import (  # noqa: E402
    answer_question,
    list_questions,
    propose_profile,
)
from app.core.db import get_conn, init_db  # noqa: E402
from app.platform.profile import MatchProfile  # noqa: E402

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "cen"
LEFT = FIXTURES / "cen_junio_muestra.xlsx"
RIGHT = FIXTURES / "sap_junio_muestra.xlsx"
REFERENCIA = PROJECT_ROOT / "profiles" / "cen_vs_sap_v1_borrador.json"

BRIEF = (
    "Necesito medir el nivel de servicio de las ordenes de compra que llegan "
    "por la plataforma CEN contra lo que realmente entregamos segun SAP. El "
    "primer archivo es el acumulado de ordenes CEN del mes; el segundo es el "
    "reporte de ventas de SAP (solo lo facturado; trae TODAS las ventas y "
    "tambien devoluciones). Quiero cuantos pedidos se entregaron completos, "
    "parciales y no entregados, en unidades y porcentaje, por material, "
    "distrito y cliente, y aparte los motivos de devolucion."
)

RESPUESTA = (
    "Confirmado: el grano en CEN es linea de producto por orden. Cruzar por "
    "DOBLE llave: numero de orden (CEN 'Numero de la Orden de compra' vs SAP "
    "columna 56) Y codigo de item/material (CEN 'Codigo item proveedor' vs SAP "
    "columna 40), normalizando espacios y ceros a la izquierda; agrupar por "
    "(orden, item) en ambos lados. La cantidad pedida es 'Cantidad Total' del "
    "CEN; la entregada es la columna 42 del SAP. Las devoluciones son las filas "
    "con tipo de operacion DEVOLUCIONES (columna 13), con motivo en la columna "
    "62: EXCLUIRLAS de la entrega (filter_not_equals) y reportarlas aparte por "
    "motivo. Las ventas SAP sin orden CEN son canales directos (TAT, PUNTOS "
    "PROPIOS, EMPLEADOS), no errores: quedan como no cruzadas. Distrito = "
    "columna 11 SAP. Desglosar por material, distrito y cliente."
)

_ORDER_SRC = {"NUMERO DE LA ORDEN DE COMPRA", "56"}
_ITEM_SRC = {"CODIGO ITEM PROVEEDOR", "40"}
_REAL_SRC = {"42"}  # cantidad entregada SAP (col 42)
_PLAN_SRC = {"CANTIDAD TOTAL"}  # cantidad pedida CEN


def _source_of(profile: MatchProfile, lado: str, col_name: str) -> str | None:
    side = profile.left if lado == "left" else profile.right
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


def _score(p: MatchProfile) -> tuple[float, list[str]]:
    checks: list[tuple[str, bool]] = []

    # Doble llave: orden (CEN) <-> pedido (SAP col56) y item (CEN) <-> material (SAP col40)
    tiene_orden = any(
        _source_of(p, "left", k.left) in _ORDER_SRC
        and _source_of(p, "right", k.right) in _ORDER_SRC
        for k in p.join.keys
    )
    tiene_item = any(
        _source_of(p, "left", k.left) in _ITEM_SRC
        and _source_of(p, "right", k.right) in _ITEM_SRC
        for k in p.join.keys
    )
    checks.append(("join por numero de orden (CEN <-> SAP col56)", tiene_orden))
    checks.append(("join por codigo de item/material (CEN <-> SAP col40)", tiene_item))
    checks.append(("join outer", p.join.type == "outer"))

    # Grano orden-item: group_by en ambos lados
    checks.append(
        ("group_by grano en CEN", any(getattr(t, "op", "") == "group_by_aggregate" for t in p.left.transforms))
    )
    checks.append(
        ("group_by grano en SAP", any(getattr(t, "op", "") == "group_by_aggregate" for t in p.right.transforms))
    )

    # Devoluciones excluidas de la entrega (filter_not_equals DEVOLUCIONES)
    excl_dev = any(
        getattr(t, "op", "") == "filter_not_equals"
        and str(getattr(t, "value", "")).upper() == "DEVOLUCIONES"
        for t in p.right.transforms
    )
    checks.append(("devoluciones excluidas de la entrega", excl_dev))

    # Nivel de servicio declarado
    checks.append(("service_level presente", p.service_level is not None))

    # Desgloses de negocio
    bd_ids = {b.id for b in p.breakdowns}
    dims = {tuple(b.dimensions) for b in p.breakdowns}
    checks.append(("breakdown por material", any("material" in " ".join(b.dimensions).lower() or "item" in " ".join(b.dimensions).lower() for b in p.breakdowns)))
    checks.append(("breakdown por distrito", any("distrito" in " ".join(b.dimensions).lower() for b in p.breakdowns)))
    checks.append(("breakdown de devoluciones por motivo", any("motivo" in " ".join(b.dimensions).lower() for b in p.breakdowns)))
    _ = (bd_ids, dims)

    # KPI de cumplimiento (ratio)
    checks.append(("kpi ratio_pct_of_sums presente", any(k.op == "ratio_pct_of_sums" for k in p.kpis)))

    # Reporte: portada + no_cruzados
    if p.report and p.report.excel:
        kinds = {s.kind for s in p.report.excel.sheets}
        sources = {s.source for s in p.report.excel.sheets}
        checks.append(("report portada", "portada" in kinds))
        checks.append(("report no_cruzados", "no_cruzados" in sources))
    else:
        checks.append(("report portada", False))
        checks.append(("report no_cruzados", False))

    total = len(checks)
    ok = sum(1 for _, v in checks if v)
    detalle = [f"[{'OK ' if v else 'FAIL'}] {name}" for name, v in checks]
    return ok / total * 100.0, detalle


def _answer_and_refine(crew, conn, profile_id, resultado, rondas=2):
    """Responde las preguntas abiertas con el contexto estandar y re-propone
    (refine), como haria el analista. Mide el flujo real, no el borrador frio."""
    version = resultado["profile"].version
    for _ in range(rondas):
        abiertas = list_questions(conn, profile_id, "abierta")
        if not abiertas:
            break
        for q in abiertas:
            answer_question(conn, q["id"], RESPUESTA)
        version += 1
        resultado = propose_profile(
            crew, conn, profile_id, left_path=LEFT, right_path=RIGHT,
            brief="", version=version,
        )
    return resultado


def main() -> int:
    init_db()
    crew = Crew()  # falla explicito si no hay GEMINI_API_KEY
    with get_conn() as conn:
        resultado = propose_profile(
            crew, conn, "eval_cen", left_path=LEFT, right_path=RIGHT, brief=BRIEF
        )
        score_frio, _ = _score(resultado["profile"])
        resultado = _answer_and_refine(crew, conn, "eval_cen", resultado)
    propuesto = resultado["profile"]
    score, detalle = _score(propuesto)

    print("=" * 60)
    print("EVAL: reconstruccion del profile CEN vs SAP por agentes")
    print("=" * 60)
    for line in detalle:
        print(" ", line)
    print("-" * 60)
    print(f"SCORE en frio (sin responder):        {score_frio:.1f}%")
    print(f"SCORE guiado (con respuestas+refine): {score:.1f}% (gate: >=80%)")
    print(f"Confianza global: {resultado['status'].confianza_global}")
    return 0 if score >= 80.0 else 1


if __name__ == "__main__":
    sys.exit(main())
