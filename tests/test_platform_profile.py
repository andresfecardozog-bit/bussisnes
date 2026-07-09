"""Tests del contrato MatchProfile (app/platform/profile.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.platform.profile import (
    MatchProfile,
    TabularLoaderSpec,
)

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


def test_pre_corte_v1_valida_contra_el_schema():
    raw = (PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    assert profile.profile_id == "pre_corte_v1"
    assert profile.join.type == "outer"
    assert {k.id for k in profile.kpis} >= {"cumplimiento_global_pct"}


def test_cen_vs_sap_borrador_valida_contra_el_schema():
    """Gate Fase 1: el contrato expresa el caso CEN vs SAP."""
    raw = (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    # SAP sin headers -> loader posicional
    assert profile.right.loader.header_row is None
    assert all(isinstance(c.source, int) for c in profile.right.loader.columns)
    # ambas fuentes ajustan el grano antes del join
    ops_left = [t.op for t in profile.left.transforms]
    ops_right = [t.op for t in profile.right.transforms]
    assert "group_by_aggregate" in ops_left
    assert "group_by_aggregate" in ops_right


def test_roundtrip_json():
    raw = (PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    again = MatchProfile.from_json(profile.to_json())
    assert again == profile


def test_roundtrip_preserva_header_row_none():
    """Regresion: exclude_none en to_json borraba header_row=null (loader
    posicional del SAP) y el default 1 corrompia el profile al recargar."""
    raw = (PROFILES_DIR / "cen_vs_sap_v1_borrador.json").read_text(encoding="utf-8")
    profile = MatchProfile.from_json(raw)
    assert profile.right.loader.header_row is None
    again = MatchProfile.from_json(profile.to_json())
    assert again.right.loader.header_row is None
    assert again == profile


def test_rechaza_join_no_outer():
    raw = json.loads((PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8"))
    raw["join"]["type"] = "inner"
    with pytest.raises(ValidationError):
        MatchProfile.model_validate(raw)


def test_rechaza_kpi_op_desconocida():
    """Una 'formula' fuera de la whitelist no es expresable en el contrato."""
    raw = json.loads((PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8"))
    raw["kpis"][0]["op"] = "eval"
    with pytest.raises(ValidationError):
        MatchProfile.model_validate(raw)


def test_rechaza_formula_libre_como_op():
    raw = json.loads((PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8"))
    raw["computed"][0]["op"] = "__import__('os').system('x')"
    with pytest.raises(ValidationError):
        MatchProfile.model_validate(raw)


def test_rechaza_parametro_no_declarado():
    raw = json.loads((PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8"))
    raw["parameters"] = []
    with pytest.raises(ValidationError, match="parametro no declarado"):
        MatchProfile.model_validate(raw)


def test_loader_posicional_exige_sources_int():
    with pytest.raises(ValidationError, match="posicionales"):
        TabularLoaderSpec(
            header_row=None,
            columns=[{"name": "x", "source": "Nombre Col", "dtype": "str"}],
        )


def test_rechaza_kpi_ids_duplicados():
    raw = json.loads((PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8"))
    raw["kpis"].append(dict(raw["kpis"][0]))
    with pytest.raises(ValidationError, match="duplicados"):
        MatchProfile.model_validate(raw)


def test_semaforo_incoherente_rechazado():
    raw = json.loads((PROFILES_DIR / "pre_corte_v1.json").read_text(encoding="utf-8"))
    raw["kpis"][0]["semaforo"] = {"verde_min": 80.0, "amarillo_min": 90.0}
    with pytest.raises(ValidationError):
        MatchProfile.model_validate(raw)
