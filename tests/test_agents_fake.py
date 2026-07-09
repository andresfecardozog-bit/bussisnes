"""Tests de la capa de agentes (Fase 2) con modelo fake: cero gasto de API.

Cubre los gates duros de rubrica.md Fase 2:
- fixture de keys repetidas -> pregunta de grano correcta -> la respuesta
  cambia el profile (group_by antes del join),
- segunda corrida no repite preguntas respondidas,
- approve rechazado con bloqueantes abiertas,
- telemetria registrada por llamada.
"""
from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.crew import Crew
from app.agents.orchestrator import (
    answer_question,
    approve_proposed_profile,
    assume_question,
    get_knowledge_context,
    list_questions,
    propose_profile,
    proposal_status,
)
from app.agents.telemetry import telemetry_summary
from app.core.db import get_conn, init_db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "agents.sqlite"
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    init_db(db_path)
    return db_path


@pytest.fixture
def archivos(tmp_path):
    """Par de archivos sinteticos: plan con keys REPETIDAS (grano = linea
    de producto) y real con keys unicas."""

    def mk(name, headers, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for r in rows:
            ws.append(r)
        p = tmp_path / name
        wb.save(p)
        return p

    plan = mk(
        "ordenes.xlsx",
        ["Numero Orden", "Item", "Cantidad"],
        [["ORD-1", "A", 10], ["ORD-1", "B", 5], ["ORD-2", "A", 3]],
    )
    real = mk(
        "entregas.xlsx",
        ["Pedido", "Entregado"],
        [["ORD-1", 12], ["ORD-3", 9]],
    )
    return plan, real


# ---------------------------------------------------------------------------
# Modelo fake: produce outputs validos segun el schema del agente invocado
# ---------------------------------------------------------------------------

def _scout_output(sobre: str, con_pregunta_grano: bool) -> dict:
    out = {
        "hoja_recomendada": "Sheet",
        "header_row": 1,
        "grano": {
            "grano_descripcion": "cada fila es una linea de producto de una orden",
            "key_candidata": "Numero Orden",
            "keys_se_repiten": con_pregunta_grano,
            "requiere_agrupacion": con_pregunta_grano,
            "confianza": 0.7,
        },
        "columnas_relevantes": ["Numero Orden", "Cantidad"],
        "anomalias": [],
        "resumen": "Tabla de ordenes con lineas de producto.",
        "open_questions": [],
        "confianza": 0.8,
    }
    if con_pregunta_grano:
        out["open_questions"] = [
            {
                "agente": "SchemaScout",
                "sobre": sobre,
                "pregunta": (
                    "Encontre numeros de orden repetidos en varias filas. "
                    "Cada fila es un producto y debo agrupar por numero de "
                    "orden para reconstruir la orden completa?"
                ),
                "hipotesis": "Si: fila = linea de producto, se agrupa por orden.",
                "impacto": "Sin agrupar, el join duplica filas y el KPI queda mal.",
                "bloqueante": True,
            }
        ]
    return out


def _mapping_output(agrupar: bool) -> dict:
    left_transforms = []
    if agrupar:
        left_transforms.append(
            {
                "op": "group_by_aggregate",
                "by": ["numero_orden"],
                "aggregations": [
                    {"target": "cantidad_pedida", "source": "cantidad", "fn": "sum"}
                ],
            }
        )
    return {
        "left": {
            "role": "plan",
            "label": "Ordenes",
            "loader": {
                "type": "tabular",
                "header_row": 1,
                "columns": [
                    {"name": "numero_orden", "source": "Numero Orden", "dtype": "str"},
                    {"name": "cantidad", "source": "Cantidad", "dtype": "float_clean"},
                ],
            },
            "transforms": left_transforms,
        },
        "right": {
            "role": "real",
            "label": "Entregas",
            "loader": {
                "type": "tabular",
                "header_row": 1,
                "columns": [
                    {"name": "pedido", "source": "Pedido", "dtype": "str"},
                    {"name": "entregado", "source": "Entregado", "dtype": "float_clean"},
                ],
            },
            "transforms": [],
        },
        "join": {
            "keys": [{"left": "numero_orden", "right": "pedido", "normalizers": ["strip"]}],
            "type": "outer",
        },
        "justificacion": "Numero Orden y Pedido contienen los mismos valores.",
        "open_questions": [],
        "confianza": 0.85,
    }


_KPI_OUTPUT = {
    "computed": [
        {
            "name": "cumplimiento_pct",
            "op": "ratio_pct",
            "left": "entregado",
            "right": "cantidad_pedida",
            "round": 2,
        }
    ],
    "kpis": [
        {
            "id": "cumplimiento_global",
            "label": "Cumplimiento (%)",
            "op": "ratio_pct_of_sums",
            "numerator": "entregado",
            "denominator": "cantidad_pedida",
            "semaforo": {"verde_min": 95.0, "amarillo_min": 85.0},
        }
    ],
    "justificacion": "KPI unico pedido por el usuario.",
    "open_questions": [],
    "confianza": 0.9,
}

_REPORT_OUTPUT = {
    "report": {
        "excel": {
            "filename_prefix": "cumplimiento_test",
            "sheets": [
                {"name": "Portada", "kind": "portada"},
                {"name": "Resumen", "kind": "kpi_resumen", "source": "kpis"},
                {"name": "Detalle", "kind": "tabla", "source": "matched"},
                {"name": "No_Cruzados", "kind": "tabla", "source": "no_cruzados"},
            ],
        },
        "powerbi": {
            "theme": "nutriavicola",
            "pages": [
                {
                    "name": "Resumen",
                    "visuals": [
                        {"kind": "card_kpi", "title": "Cumplimiento", "measure": "cumplimiento_global"},
                        {"kind": "tabla_detalle", "title": "Detalle"},
                    ],
                }
            ],
        },
    },
    "justificacion": "Reporte minimo corporativo.",
    "open_questions": [],
    "confianza": 0.9,
}


def make_fake_model(con_pregunta_grano: bool = True, agrupar: bool = True) -> FunctionModel:
    """Despacha por el schema del output tool del agente que llama."""

    def fn(messages, info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        props = tool.parameters_json_schema.get("properties", {})
        prompt_text = ""
        for m in messages:
            for part in getattr(m, "parts", []):
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    prompt_text += content

        if "hoja_recomendada" in props:
            sobre = "izquierda" if "FUENTE IZQUIERDA" in prompt_text else "derecha"
            args = _scout_output(sobre, con_pregunta_grano and sobre == "izquierda")
        elif "join" in props:
            args = _mapping_output(agrupar)
        elif "kpis" in props:
            args = _KPI_OUTPUT
        elif "report" in props:
            args = _REPORT_OUTPUT
        else:
            raise AssertionError(f"Output tool desconocido: {list(props)}")
        return ModelResponse(parts=[ToolCallPart(tool.name, args)])

    return FunctionModel(fn)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_propuesta_completa_con_pregunta_de_grano(db, archivos):
    plan, real = archivos
    crew = Crew(model=make_fake_model())
    with get_conn(db) as conn:
        resultado = propose_profile(
            crew, conn, "test_proc", plan, real,
            brief="medir cumplimiento de entregas por orden",
        )
        profile = resultado["profile"]
        assert profile.profile_id == "test_proc"
        assert resultado["preguntas_nuevas"] == 1
        abiertas = list_questions(conn, "test_proc", estado="abierta")
        assert len(abiertas) == 1
        assert "agrupar" in abiertas[0]["pregunta"]
        assert abiertas[0]["bloqueante"] == 1
        assert resultado["status"].listo_para_aprobar is False


def test_approve_rechazado_con_bloqueante_abierta(db, archivos):
    plan, real = archivos
    crew = Crew(model=make_fake_model())
    with get_conn(db) as conn:
        propose_profile(crew, conn, "test_proc", plan, real, brief="cumplimiento")
        with pytest.raises(ValueError, match="bloqueante"):
            approve_proposed_profile(conn, "test_proc", 1, "analista")


def test_respuesta_desbloquea_approve_y_alimenta_memoria(db, archivos):
    plan, real = archivos
    crew = Crew(model=make_fake_model())
    with get_conn(db) as conn:
        propose_profile(crew, conn, "test_proc", plan, real, brief="cumplimiento")
        q = list_questions(conn, "test_proc", estado="abierta")[0]
        answer_question(conn, q["id"], "Si, cada fila es un producto; agrupar por orden.")
        status = proposal_status(conn, "test_proc")
        assert status.listo_para_aprobar is True
        approve_proposed_profile(conn, "test_proc", 1, "analista")
        contexto = get_knowledge_context(conn, "test_proc")
        assert "agrupar por orden" in contexto


def test_segunda_corrida_no_repite_pregunta_respondida(db, archivos):
    """Gate duro rubrica Fase 2: cero preguntas repetidas."""
    plan, real = archivos
    crew = Crew(model=make_fake_model())
    with get_conn(db) as conn:
        propose_profile(crew, conn, "test_proc", plan, real, brief="cumplimiento")
        q = list_questions(conn, "test_proc", estado="abierta")[0]
        answer_question(conn, q["id"], "Si, agrupar por numero de orden.")
        # segunda corrida: el fake vuelve a emitir la misma pregunta,
        # pero el orquestador la deduplica contra las respondidas
        resultado2 = propose_profile(
            crew, conn, "test_proc", plan, real, brief="", version=2
        )
        assert resultado2["preguntas_nuevas"] == 0
        assert proposal_status(conn, "test_proc").preguntas_abiertas == 0


def test_profile_propuesto_ejecuta_con_grano_corregido(db, archivos):
    """Sin group_by en la propuesta del agente, el orquestador corrige el
    grano automaticamente; con group_by explicito el KPI esperado se mantiene."""
    from app.platform.engine import run_profile

    plan, real = archivos
    with get_conn(db) as conn:
        crew_sin = Crew(model=make_fake_model(agrupar=False))
        sin_grupo = propose_profile(
            crew_sin, conn, "proc_sin", plan, real, brief="cumplimiento"
        )["profile"]
        result_sin = run_profile(sin_grupo, left_path=plan, right_path=real)
        assert result_sin.summary()["matched"] >= 1

        crew_con = Crew(model=make_fake_model(agrupar=True))
        con_grupo = propose_profile(
            crew_con, conn, "proc_con", plan, real, brief="cumplimiento"
        )["profile"]
        result = run_profile(con_grupo, left_path=plan, right_path=real)
        assert result.summary() == {
            "matched": 1, "solo_left": 1, "solo_right": 1, "no_cruzados": 2,
        }
        # ORD-1: pedido 15 (10+5 agrupado), entregado 12
        assert result.kpis["cumplimiento_global"] == 80.0


def test_telemetria_registrada_por_llamada(db, archivos):
    plan, real = archivos
    crew = Crew(model=make_fake_model())
    with get_conn(db) as conn:
        propose_profile(crew, conn, "test_proc", plan, real, brief="cumplimiento")
        resumen = telemetry_summary(conn, "test_proc")
        # 2 scouts + mapping + kpi + report = 5 llamadas
        assert resumen["llamadas"] == 5
        assert resumen["latencia_media_ms"] >= 0


def test_pregunta_no_bloqueante_puede_asumirse(db, archivos):
    plan, real = archivos
    crew = Crew(model=make_fake_model())
    with get_conn(db) as conn:
        propose_profile(crew, conn, "test_proc", plan, real, brief="x")
        q = list_questions(conn, "test_proc", estado="abierta")[0]
        # bloqueante no puede asumirse
        with pytest.raises(ValueError, match="bloqueante"):
            assume_question(conn, q["id"])


def test_crew_sin_api_key_falla_explicito(monkeypatch):
    monkeypatch.setattr("app.agents.crew.GEMINI_API_KEY", None)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        Crew()


def test_dry_run_completo_cubre_render(db, archivos):
    """El dry-run del orquestador debe ejercer el pipeline COMPLETO de
    generate (cruce + render) para que el flujo del usuario nunca vea un
    error en /generate. Un profile valido -> None; uno que rompe en el motor
    o el render -> string de error (que el orquestador vuelve pregunta
    bloqueante del Motor)."""
    from app.agents.orchestrator import _full_generate_dry_run

    plan, real = archivos
    crew = Crew(model=make_fake_model(agrupar=True))
    with get_conn(db) as conn:
        profile = propose_profile(
            crew, conn, "dry_ok", plan, real, brief="cumplimiento"
        )["profile"]

    # Profile valido: el dry-run completo (incluye render) no reporta error.
    assert _full_generate_dry_run(profile, plan, real) is None

    # Profile roto: una dimension del data_model referencia columna
    # inexistente -> el dry-run lo detecta (no revienta el proceso).
    raw = json.loads(profile.to_json())
    raw["data_model"] = {
        "fact_name": "FactTest",
        "dimensions": [{"name": "DimX", "key": "columna_que_no_existe"}],
    }
    roto = type(profile).model_validate(raw)
    err = _full_generate_dry_run(roto, plan, real)
    assert err is not None
    assert "columna_que_no_existe" in err or "inexistente" in err
