"""Tests del modelo de datos exportable (fact + dimensiones con ids)."""
from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from app.platform.data_model import build_data_model
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


def _profile(tmp_path) -> tuple[MatchProfile, Path, Path]:
    plan = _mk_xlsx(
        tmp_path, "plan.xlsx",
        ["PEDIDO", "MAT", "DESC", "CLIENTE", "PLAN"],
        [
            ["P1", "30018", "HUEVO AA", "EXITO", 10],
            ["P2", "30049", "HUEVO AAA", "OLIMPICA", 20],
            ["P3", "30018", "HUEVO AA", "EXITO", 5],
        ],
    )
    real = _mk_xlsx(
        tmp_path, "real.xlsx",
        ["PEDIDO", "MAT", "REAL"],
        [["P1", "30018", 8], ["P9", "40002", 3]],
    )
    profile = MatchProfile.model_validate(
        {
            "profile_id": "dm_test",
            "version": 1,
            "left": {
                "role": "plan",
                "label": "Plan",
                "loader": {
                    "type": "tabular",
                    "header_row": 1,
                    "columns": [
                        {"name": "pedido", "source": "PEDIDO", "dtype": "str"},
                        {"name": "material", "source": "MAT", "dtype": "str"},
                        {"name": "descripcion", "source": "DESC", "dtype": "str"},
                        {"name": "cliente", "source": "CLIENTE", "dtype": "str"},
                        {"name": "plan", "source": "PLAN", "dtype": "float_clean"},
                    ],
                },
            },
            "right": {
                "role": "real",
                "label": "Real",
                "loader": {
                    "type": "tabular",
                    "header_row": 1,
                    "columns": [
                        {"name": "pedido", "source": "PEDIDO", "dtype": "str"},
                        {"name": "material", "source": "MAT", "dtype": "str"},
                        {"name": "real", "source": "REAL", "dtype": "float_clean"},
                    ],
                },
            },
            "join": {
                "keys": [
                    {"left": "pedido", "right": "pedido"},
                    {"left": "material", "right": "material"},
                ]
            },
            "kpis": [
                {"id": "n", "label": "n", "op": "count"}
            ],
            "data_model": {
                "fact_name": "FactTest",
                "dimensions": [
                    {"name": "DimCliente", "key": "cliente"},
                    {"name": "DimMaterial", "key": "material", "attributes": ["descripcion"]},
                ],
            },
        }
    )
    return profile, plan, real


def test_fact_contiene_todas_las_lineas_con_estado(tmp_path):
    profile, plan, real = _profile(tmp_path)
    result = run_profile(profile, left_path=plan, right_path=real)
    tablas = build_data_model(profile, result)

    fact = tablas["FactTest"]
    # 1 matched + 2 solo_plan + 1 solo_real = 4 filas, cero perdida
    assert len(fact) == 4
    assert set(fact["estado_cruce"]) == {"cruzado", "solo_plan", "solo_real"}
    assert fact["fact_id"].tolist() == [1, 2, 3, 4]
    # keys del join con nombre legible
    assert "key_pedido" in fact.columns
    assert "key_material" in fact.columns


def test_dimensiones_con_ids_y_fks(tmp_path):
    profile, plan, real = _profile(tmp_path)
    result = run_profile(profile, left_path=plan, right_path=real)
    tablas = build_data_model(profile, result)

    dim_cliente = tablas["DimCliente"]
    assert list(dim_cliente.columns) == ["dim_cliente_id", "cliente"]
    assert set(dim_cliente["cliente"]) == {"EXITO", "OLIMPICA"}

    dim_material = tablas["DimMaterial"]
    assert "descripcion" in dim_material.columns

    fact = tablas["FactTest"]
    assert "dim_cliente_id" in fact.columns
    assert "dim_material_id" in fact.columns
    # FK consistente: la fila cruzada P1 apunta al cliente EXITO
    fila_p1 = fact[fact["key_pedido"] == "P1"].iloc[0]
    exito_id = dim_cliente[dim_cliente["cliente"] == "EXITO"]["dim_cliente_id"].iloc[0]
    assert fila_p1["dim_cliente_id"] == exito_id
    # la fila solo_real no tiene cliente -> FK nula
    fila_p9 = fact[fact["key_pedido"] == "P9"].iloc[0]
    assert pd.isna(fila_p9["dim_cliente_id"])


def test_fact_con_columnas_duplicadas_no_rompe(tmp_path, monkeypatch):
    """Regresion del 500 en /generate: un profile (tipico del LLM) puede
    producir un fact con columnas de nombre duplicado; build_data_model debe
    colapsarlas (primera aparicion) en vez de reventar en sort_values."""
    import app.platform.data_model as dm

    profile, plan, real = _profile(tmp_path)
    result = run_profile(profile, left_path=plan, right_path=real)

    fact_real = dm._build_fact(profile, result)
    # Duplicar la columna 'cliente' tal como saldria de un mapeo que trae el
    # mismo label desde ambas fuentes (caso observado en produccion).
    fact_dup = pd.concat([fact_real, fact_real[["cliente"]]], axis=1)
    assert fact_dup.columns.duplicated().any()
    monkeypatch.setattr(dm, "_build_fact", lambda p, r: fact_dup)

    tablas = build_data_model(profile, result)  # no debe lanzar
    fact = tablas["FactTest"]
    assert not fact.columns.duplicated().any()
    assert "cliente" in tablas["DimCliente"].columns


def test_dimension_key_igual_a_atributo_no_rompe(tmp_path):
    """Regresion del 500 en /generate: si la key de una dimension y uno de
    sus atributos resuelven a la MISMA columna del fact, la seleccion no debe
    producir un subset con columnas duplicadas (sort_values reventaba)."""
    profile, plan, real = _profile(tmp_path)
    raw = json.loads(profile.to_json())
    # DimCliente con key 'cliente' y atributo 'cliente' (mismo campo)
    raw["data_model"]["dimensions"][0] = {
        "name": "DimCliente", "key": "cliente", "attributes": ["cliente"]
    }
    profile2 = MatchProfile.model_validate(raw)
    result = run_profile(profile2, left_path=plan, right_path=real)
    tablas = build_data_model(profile2, result)  # no debe lanzar
    dim = tablas["DimCliente"]
    assert list(dim.columns) == ["dim_cliente_id", "cliente"]


def test_profile_sin_data_model_produce_fact_minimo(tmp_path):
    profile, plan, real = _profile(tmp_path)
    raw = json.loads(profile.to_json())
    raw.pop("data_model")
    profile2 = MatchProfile.model_validate(raw)
    result = run_profile(profile2, left_path=plan, right_path=real)
    tablas = build_data_model(profile2, result)
    assert list(tablas) == ["FactCruce"]
    assert len(tablas["FactCruce"]) == 4


def test_dimension_key_inexistente_falla(tmp_path):
    profile, plan, real = _profile(tmp_path)
    raw = json.loads(profile.to_json())
    raw["data_model"]["dimensions"][0]["key"] = "no_existe"
    profile2 = MatchProfile.model_validate(raw)
    result = run_profile(profile2, left_path=plan, right_path=real)
    with pytest.raises(ValueError, match="no_existe"):
        build_data_model(profile2, result)


def test_borrador_cen_produce_modelo_estrella():
    raw = (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    result = run_profile(profile, left_path=CEN_JUNIO, right_path=SAP_MUESTRA)
    tablas = build_data_model(profile, result)
    assert set(tablas) == {
        "FactNivelServicio", "DimCliente", "DimMaterial", "DimDistrito",
    }
    fact = tablas["FactNivelServicio"]
    assert "nivel_servicio" in fact.columns
    assert "estado_cruce" in fact.columns
    assert "dim_cliente_id" in fact.columns
    total = result.summary()
    assert len(fact) == total["matched"] + total["solo_left"] + total["solo_right"]