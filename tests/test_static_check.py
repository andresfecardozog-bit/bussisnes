"""Tests del chequeo estatico de referencias del MatchProfile."""
from __future__ import annotations

import json
from pathlib import Path

from app.platform.profile import MatchProfile
from app.platform.static_check import check_profile_references

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


def _borrador() -> dict:
    return json.loads(
        (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    )


def test_borrador_cen_sin_errores():
    profile = MatchProfile.model_validate(_borrador())
    assert check_profile_references(profile) == []


def test_pre_corte_registered_no_valida_columnas():
    raw = (PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    # loaders registered no declaran columnas: no puede validar, no falla
    assert check_profile_references(profile) == []


def test_detecta_kpi_con_columna_fantasma():
    raw = _borrador()
    raw["kpis"][0]["numerator"] = "columna_inventada"
    profile = MatchProfile.model_validate(raw)
    errores = check_profile_references(profile)
    assert any("columna_inventada" in e for e in errores)


def test_detecta_service_level_roto():
    raw = _borrador()
    raw["service_level"]["plan_column"] = "no_existe"
    profile = MatchProfile.model_validate(raw)
    errores = check_profile_references(profile)
    assert any("service_level" in e for e in errores)


def test_detecta_breakdown_dimension_fantasma():
    raw = _borrador()
    raw["breakdowns"][0]["dimensions"] = ["dimension_fantasma"]
    profile = MatchProfile.model_validate(raw)
    errores = check_profile_references(profile)
    assert any("dimension_fantasma" in e for e in errores)


def test_detecta_join_key_fantasma():
    raw = _borrador()
    raw["join"]["keys"][0]["left"] = "key_fantasma"
    profile = MatchProfile.model_validate(raw)
    errores = check_profile_references(profile)
    assert any("key_fantasma" in e for e in errores)


def test_detecta_data_model_dimension_rota():
    raw = _borrador()
    raw["data_model"]["dimensions"][0]["key"] = "id_material_agregado"
    profile = MatchProfile.model_validate(raw)
    errores = check_profile_references(profile)
    assert any("id_material_agregado" in e for e in errores)


def test_computed_habilita_referencias_en_kpis():
    raw = _borrador()
    # cumplimiento_pct es computed y los breakdowns lo pueden referenciar
    profile = MatchProfile.model_validate(raw)
    assert check_profile_references(profile) == []
