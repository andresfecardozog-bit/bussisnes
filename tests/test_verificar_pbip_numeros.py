from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.platform.engine import GenericMatchResult
from app.platform.profile import MatchProfile
from scripts.verificar_pbip_numeros import (
    _expected_dax_fragments,
    _metricas_fact,
    _metricas_motor,
    run_verificacion,
)


ROOT = Path(__file__).resolve().parents[1]


def _profile_min() -> MatchProfile:
    return MatchProfile.model_validate(
        {
            "profile_id": "mini_verif",
            "version": 1,
            "descripcion": "mini profile para verificacion numerica",
            "left": {
                "role": "cen",
                "label": "Plan",
                "loader": {"type": "tabular", "columns": [{"name": "numero_orden", "source": "ORDEN"}]},
            },
            "right": {
                "role": "sap",
                "label": "Real",
                "loader": {"type": "tabular", "columns": [{"name": "numero_pedido", "source": "PEDIDO"}]},
            },
            "join": {"keys": [{"left": "numero_orden", "right": "numero_pedido"}]},
            "kpis": [{"id": "lineas", "label": "Lineas", "op": "count"}],
            "service_level": {
                "plan_column": "cantidad_pedida",
                "real_column": "cantidad_entregada",
                "pedido_key": "numero_orden",
                "tolerancia_pct": 0.0,
            },
            "data_model": {"fact_name": "FactMini", "dimensions": []},
        }
    )


def test_metricas_fact_calcula_nivel_servicio_y_pedidos():
    profile = _profile_min()
    fact = pd.DataFrame(
        {
            "estado_cruce": ["cruzado", "cruzado", "solo_cen", "solo_sap"],
            "key_numero_orden": ["P1", "P1", "P2", None],
            "nivel_servicio": ["completo", "completo", "no_entregado", "sin_pedido"],
            "cantidad_pedida": [10.0, 5.0, 8.0, 0.0],
            "cantidad_entregada": [10.0, 5.0, 0.0, 3.0],
        }
    )
    m = _metricas_fact(profile, fact)
    assert m["unidades_pedidas"] == 23.0
    assert m["unidades_entregadas"] == 15.0
    assert m["unidades_sin_pedido"] == 3.0
    assert m["ns_unidades_pct"] == (15.0 / 23.0 * 100.0)
    assert m["pedidos_completos_pct"] == 50.0


def test_metricas_motor_usa_service_level_y_kpis():
    profile = _profile_min()
    result = GenericMatchResult(
        profile_id=profile.profile_id,
        profile_version=profile.version,
        parameters={},
        matched=pd.DataFrame(),
        solo_left=pd.DataFrame(),
        solo_right=pd.DataFrame(),
        no_cruzados=pd.DataFrame(columns=["origen", "key", "motivo"]),
        kpis={"unidades_entregadas": 154.0},
        service_level={
            "total_unidades_plan": 4005.0,
            "clases": {"sin_pedido": {"unidades_real": 120.0}},
            "pedidos": {"clases": {"completo": {"pct": 33.33}}},
        },
    )
    m = _metricas_motor(profile, result)
    assert m["unidades_pedidas"] == 4005.0
    assert m["unidades_entregadas"] == 154.0
    assert m["unidades_sin_pedido"] == 120.0
    assert m["ns_unidades_pct"] == (154.0 / 4005.0 * 100.0)
    assert m["pedidos_completos_pct"] == 33.33


def test_expected_dax_fragments_incluye_filtros_estado():
    profile = _profile_min()
    fact = pd.DataFrame(
        columns=["cantidad_pedida", "cantidad_entregada", "estado_cruce", "nivel_servicio"]
    )
    frags = _expected_dax_fragments(profile, "FactMini", fact)
    assert len(frags) == 3
    assert 'FactMini[estado_cruce] IN {"cruzado"}' in frags[0]
    assert 'FactMini[estado_cruce] IN {"cruzado", "solo_cen"}' in frags[1]
    assert 'FactMini[estado_cruce] IN {"solo_sap"}' in frags[2]


def test_run_verificacion_cen_fixtures_genera_reporte_ok():
    payload = run_verificacion(
        profile_path=ROOT / "profiles" / "cen_vs_sap_v1_borrador.json",
        left_path=ROOT / "tests" / "fixtures" / "cen" / "cen_junio_muestra.xlsx",
        right_path=ROOT / "tests" / "fixtures" / "cen" / "sap_junio_muestra.xlsx",
        pbip_dir=None,
        tolerance=0.01,
    )
    assert payload["profile_id"] == "cen_vs_sap_v1_borrador"
    assert payload["checks"], "debe traer comparaciones numericas"
    assert payload["dax_checks"], "debe validar fragmentos dax"
    assert payload["ok"] is True
