"""Tests para app.core.aggregator."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.aggregator import aggregate_flash
from app.core.loaders import load_flash

FIXTURES = Path(__file__).parent / "fixtures"
FLASH_MINI = FIXTURES / "FLASH_muestra.csv"
FECHA_PROD = date(2026, 2, 14)


def test_aggregate_filtra_por_fecha_produccion():
    df, _ = load_flash(FLASH_MINI)
    agg = aggregate_flash(df, FECHA_PROD)
    assert 30040 in agg["material"].values
    assert len(agg) == 5


def test_aggregate_suma_correcta_por_material():
    df, _ = load_flash(FLASH_MINI)
    agg = aggregate_flash(df, FECHA_PROD)
    fila = agg[agg["material"] == 30040].iloc[0]
    assert fila["cantidad_neta_total"] == pytest.approx(246_000.0)
    assert fila["num_facturas"] == 1


def test_aggregate_ignora_fecha_distinta():
    """La fila del 15/02/2026 para material 30040 no debe sumarse a la del 14."""
    df, _ = load_flash(FLASH_MINI)
    agg = aggregate_flash(df, FECHA_PROD)
    fila = agg[agg["material"] == 30040].iloc[0]
    assert fila["cantidad_neta_total"] == pytest.approx(246_000.0)


def test_aggregate_fecha_sin_datos_devuelve_vacio():
    df, _ = load_flash(FLASH_MINI)
    agg = aggregate_flash(df, date(1990, 1, 1))
    assert agg.empty


def test_aggregate_columnas_esperadas():
    df, _ = load_flash(FLASH_MINI)
    agg = aggregate_flash(df, FECHA_PROD)
    assert set(agg.columns) == {
        "material",
        "nomb_material",
        "cantidad_neta_total",
        "facturado_real_total",
        "num_facturas",
    }
