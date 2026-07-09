"""Tests de nivel de servicio y breakdowns dimensionales (requisitos de
negocio del caso CEN vs SAP, 2026-07-08)."""
from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pytest

from app.platform.engine import run_profile
from app.platform.profile import MatchProfile

FIXTURES = Path(__file__).parent / "fixtures"
PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"
CEN_JUNIO = FIXTURES / "cen" / "cen_junio_muestra.xlsx"
SAP_MUESTRA = FIXTURES / "cen" / "sap_junio_muestra.xlsx"


def _mk_xlsx(tmp_path: Path, name: str, headers: list[str], rows: list[list]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    p = tmp_path / name
    wb.save(p)
    return p


def _profile_con_service_level() -> MatchProfile:
    return MatchProfile.model_validate(
        {
            "profile_id": "sl_test",
            "version": 1,
            "left": {
                "role": "plan",
                "label": "Pedidos",
                "loader": {
                    "type": "tabular",
                    "header_row": 1,
                    "columns": [
                        {"name": "pedido", "source": "PEDIDO", "dtype": "str"},
                        {"name": "item", "source": "ITEM", "dtype": "str"},
                        {"name": "plan", "source": "PLAN", "dtype": "float_clean"},
                        {"name": "cliente", "source": "CLIENTE", "dtype": "str"},
                    ],
                },
            },
            "right": {
                "role": "real",
                "label": "Entregas",
                "loader": {
                    "type": "tabular",
                    "header_row": 1,
                    "columns": [
                        {"name": "pedido", "source": "PEDIDO", "dtype": "str"},
                        {"name": "item", "source": "ITEM", "dtype": "str"},
                        {"name": "real", "source": "REAL", "dtype": "float_clean"},
                    ],
                },
            },
            "join": {
                "keys": [
                    {"left": "pedido", "right": "pedido"},
                    {"left": "item", "right": "item"},
                ]
            },
            "service_level": {
                "plan_column": "plan",
                "real_column": "real",
                "pedido_key": "pedido",
            },
            "breakdowns": [
                {
                    "id": "por_cliente",
                    "label": "Por cliente",
                    "dimensions": ["cliente"],
                    "metrics": [
                        {"id": "plan_total", "op": "sum", "column": "plan"},
                        {"id": "real_total", "op": "sum", "column": "real"},
                        {
                            "id": "cumpl_pct",
                            "op": "ratio_pct_of_sums",
                            "numerator": "real",
                            "denominator": "plan",
                        },
                    ],
                    "universe": "left_full",
                }
            ],
            "kpis": [
                {
                    "id": "cumpl",
                    "label": "Cumplimiento",
                    "op": "ratio_pct_of_sums",
                    "numerator": "real",
                    "denominator": "plan",
                }
            ],
        }
    )


@pytest.fixture
def par_archivos(tmp_path):
    # PED-1: 2 lineas completas -> pedido completo
    # PED-2: 1 completa + 1 parcial -> pedido parcial
    # PED-3: solo en plan -> pedido no_entregado
    # PED-9: solo en real -> sin_pedido
    plan = _mk_xlsx(
        tmp_path, "plan.xlsx",
        ["PEDIDO", "ITEM", "PLAN", "CLIENTE"],
        [
            ["PED-1", "A", 10, "EXITO"],
            ["PED-1", "B", 20, "EXITO"],
            ["PED-2", "A", 30, "OLIMPICA"],
            ["PED-2", "C", 40, "OLIMPICA"],
            ["PED-3", "A", 50, "CARULLA"],
        ],
    )
    real = _mk_xlsx(
        tmp_path, "real.xlsx",
        ["PEDIDO", "ITEM", "REAL"],
        [
            ["PED-1", "A", 10],
            ["PED-1", "B", 25],
            ["PED-2", "A", 30],
            ["PED-2", "C", 15],
            ["PED-9", "Z", 7],
        ],
    )
    return plan, real


def test_service_level_por_linea(par_archivos):
    plan, real = par_archivos
    result = run_profile(_profile_con_service_level(), left_path=plan, right_path=real)

    assert "nivel_servicio" in result.matched.columns
    sl = result.kpis["service_level"]
    clases = sl["clases"]
    # lineas: PED-1 A y B completas, PED-2 A completa = 3; PED-2 C parcial = 1;
    # PED-3 A solo_left = 1 no_entregado; PED-9 Z = sin_pedido
    assert clases["completo"]["lineas"] == 3
    assert clases["parcial"]["lineas"] == 1
    assert clases["no_entregado"]["lineas"] == 1
    assert clases["sin_pedido"]["lineas"] == 1

    # unidades: plan total pedido = 150; completo=60, parcial=40, no_entregado=50
    assert clases["completo"]["unidades_plan"] == 60.0
    assert clases["parcial"]["unidades_plan"] == 40.0
    assert clases["no_entregado"]["unidades_plan"] == 50.0
    assert clases["sin_pedido"]["unidades_real"] == 7.0

    # porcentajes en unidades y en lineas presentes
    assert clases["completo"]["pct_unidades_plan"] == 40.0
    assert clases["completo"]["pct_lineas"] == 60.0


def test_service_level_por_pedido(par_archivos):
    plan, real = par_archivos
    result = run_profile(_profile_con_service_level(), left_path=plan, right_path=real)
    pedidos = result.kpis["service_level"]["pedidos"]
    assert pedidos["total"] == 3
    assert pedidos["clases"]["completo"]["pedidos"] == 1
    assert pedidos["clases"]["parcial"]["pedidos"] == 1
    assert pedidos["clases"]["no_entregado"]["pedidos"] == 1
    assert pedidos["clases"]["completo"]["pct"] == 33.33


def test_breakdown_por_cliente(par_archivos):
    plan, real = par_archivos
    result = run_profile(_profile_con_service_level(), left_path=plan, right_path=real)
    bd = result.breakdowns["por_cliente"]
    fila_exito = bd[bd["cliente"] == "EXITO"].iloc[0]
    assert fila_exito["plan_total"] == 30.0
    assert fila_exito["real_total"] == 35.0
    fila_carulla = bd[bd["cliente"] == "CARULLA"].iloc[0]
    assert fila_carulla["plan_total"] == 50.0
    # CARULLA no recibio nada: real 0/NaN, cumplimiento nulo o 0
    assert fila_carulla["real_total"] in (0.0,) or fila_carulla["real_total"] != fila_carulla["real_total"]


def test_cen_vs_sap_borrador_v2_completo():
    """El borrador v2 con service_level + 5 breakdowns corre sobre los
    fixtures reales de junio."""
    raw = (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    result = run_profile(profile, left_path=CEN_JUNIO, right_path=SAP_MUESTRA)

    sl = result.kpis["service_level"]
    assert sl["total_lineas_pedido"] > 0
    assert "pedidos" in sl

    assert set(result.breakdowns) == {
        "por_material", "por_distrito", "por_cliente",
        "por_cliente_material", "devoluciones_por_motivo",
        "devoluciones_por_distrito", "rechazos_no_devolucion",
        "ventas_por_canal",
    }
    bd_mat = result.breakdowns["por_material"]
    assert "cumplimiento_pct" in bd_mat.columns
    assert bd_mat["unidades_pedidas"].sum() > 0

    # devoluciones: universo right_source con filtro DEVOLUCIONES; puede
    # estar vacio en la muestra pero la tabla existe con sus columnas
    bd_dev = result.breakdowns["devoluciones_por_motivo"]
    assert list(bd_dev.columns)[:1] == ["motivo_devolucion"]


def test_breakdown_dimension_inexistente_falla_explicito(par_archivos, tmp_path):
    plan, real = par_archivos
    profile = _profile_con_service_level().model_copy(deep=True)
    raw = json.loads(profile.to_json())
    raw["breakdowns"][0]["dimensions"] = ["no_existe"]
    profile = MatchProfile.model_validate(raw)
    with pytest.raises(ValueError, match="dimensiones inexistentes"):
        run_profile(profile, left_path=plan, right_path=real)


def test_sheet_breakdown_referencia_valida():
    raw = json.loads(
        (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    )
    raw["report"]["excel"]["sheets"][2]["breakdown_id"] = "fantasma"
    with pytest.raises(Exception, match="fantasma"):
        MatchProfile.model_validate(raw)


def test_persist_run_guarda_breakdowns(par_archivos, tmp_path, monkeypatch):
    from app.core.db import get_conn, init_db
    from app.platform.store import load_run_breakdown, persist_run

    db_path = tmp_path / "sl.sqlite"
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    init_db(db_path)

    plan, real = par_archivos
    result = run_profile(_profile_con_service_level(), left_path=plan, right_path=real)
    with get_conn(db_path) as conn:
        info = persist_run(conn, result)
        bd = load_run_breakdown(conn, info["run_id"], "por_cliente")
        assert len(bd) == 3
        assert "cumpl_pct" in bd.columns