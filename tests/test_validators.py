"""Tests para app.core.validators."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.core.aggregator import aggregate_flash
from app.core.loaders import load_flash, load_pre_corte
from app.core.matcher import match_by_material
from app.core.validators import (
    all_ok,
    as_dict_list,
    run_all_validations,
    validate_all_materials_accounted,
    validate_catalog_coverage,
    validate_no_duplicates,
    validate_resumen_total_preserved,
    validate_resumen_vs_notificacion,
    validate_row_count,
    validate_sum_preserved,
)

FIXTURES = Path(__file__).parent / "fixtures"
PRE_CORTE_XLSX = FIXTURES / "PRE_CORTE_muestra.xlsx"
FLASH_MINI = FIXTURES / "FLASH_muestra.csv"
FECHA_PROD = date(2026, 2, 14)


def test_validate_row_count_ok():
    v = validate_row_count(37, 37)
    assert v.ok is True
    assert v.detalle["diferencia"] == 0


def test_validate_row_count_fail_por_diferencia():
    v = validate_row_count(37, 30)
    assert v.ok is False
    assert v.detalle["diferencia"] == 7


def test_validate_resumen_total_preserved_ok(isolated_db_with_catalog):
    df, _ = load_pre_corte(PRE_CORTE_XLSX)
    v = validate_resumen_total_preserved(PRE_CORTE_XLSX, df)
    assert v.ok is True, v.detalle
    assert v.detalle["resumen_total_unidades"] == pytest.approx(299_416.0)
    assert v.detalle["df_notificado_sum"] == pytest.approx(299_416.0)


def test_validate_resumen_total_preserved_detecta_perdida(isolated_db_with_catalog):
    df, _ = load_pre_corte(PRE_CORTE_XLSX)
    df_corrupto = df.copy()
    df_corrupto.loc[df_corrupto.index[0], "notificado"] = 0
    v = validate_resumen_total_preserved(PRE_CORTE_XLSX, df_corrupto)
    assert v.ok is False
    assert v.detalle["diferencia_absoluta"] > 100


def test_validate_resumen_vs_notificacion_ok_fixture():
    v = validate_resumen_vs_notificacion(PRE_CORTE_XLSX)
    assert v.ok is True, v.detalle
    assert v.detalle["resumen_total_unidades"] == pytest.approx(
        v.detalle["notificacion_total_unidades"]
    )


def test_validate_catalog_coverage_100pct(isolated_db_with_catalog):
    _, meta = load_pre_corte(PRE_CORTE_XLSX)
    v = validate_catalog_coverage(meta)
    assert v.ok is True
    assert v.detalle["cobertura_pct"] == 100.0


def test_validate_catalog_coverage_bajo_umbral():
    meta_bajo = {
        "num_filas_original": 18,
        "num_filas_procesadas": 10,
        "num_filas_sin_sap": 8,
        "sin_sap_detalle": [],
    }
    v = validate_catalog_coverage(meta_bajo, umbral_pct=95.0)
    assert v.ok is False
    assert v.detalle["cobertura_pct"] < 95.0


def test_validate_sum_preserved_flash_cantidad_neta():
    df, _ = load_flash(FLASH_MINI)
    v = validate_sum_preserved(FLASH_MINI, df, "Cantidad Neta", "cantidad_neta")
    assert v.ok is True, v.detalle


def test_validate_no_duplicates_ok(isolated_db_with_catalog):
    df, _ = load_pre_corte(PRE_CORTE_XLSX)
    v = validate_no_duplicates(df, ["material"])
    assert v.ok is True


def test_validate_no_duplicates_detecta_repetidos(isolated_db_with_catalog):
    df, _ = load_pre_corte(PRE_CORTE_XLSX)
    duplicado = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    v = validate_no_duplicates(duplicado, ["material"])
    assert v.ok is False
    assert v.detalle["filas_duplicadas"] >= 2


def test_validate_all_materials_accounted_conservacion(isolated_db_with_catalog):
    pre_df, _ = load_pre_corte(PRE_CORTE_XLSX)
    flash_df, _ = load_flash(FLASH_MINI)
    agg = aggregate_flash(flash_df, FECHA_PROD)
    result = match_by_material(pre_df, agg, FECHA_PROD)

    v = validate_all_materials_accounted(pre_df, result)
    assert v.ok is True
    assert v.detalle["pre_corte_filas"] == v.detalle["matched"] + v.detalle["solo_pre_corte"]


def test_run_all_validations_devuelve_lista_con_nuevos_validadores(isolated_db_with_catalog):
    pre_df, pre_meta = load_pre_corte(PRE_CORTE_XLSX)
    flash_df, flash_meta = load_flash(FLASH_MINI)
    agg = aggregate_flash(flash_df, FECHA_PROD)
    result = match_by_material(pre_df, agg, FECHA_PROD)

    validaciones = run_all_validations(
        pre_corte_path=PRE_CORTE_XLSX,
        pre_corte_df=pre_df,
        pre_corte_meta=pre_meta,
        flash_path=FLASH_MINI,
        flash_df=flash_df,
        flash_meta=flash_meta,
        match_result=result,
    )
    nombres = {v.nombre for v in validaciones}
    assert "pre_corte_resumen_total_preserved" in nombres
    assert "resumen_vs_notificacion" in nombres
    assert "catalog_coverage" in nombres
    assert all_ok(validaciones), [
        (v.nombre, v.detalle) for v in validaciones if not v.ok
    ]


def test_as_dict_list_serializa_correctamente():
    v = validate_row_count(10, 10)
    dicts = as_dict_list([v])
    assert dicts[0]["nombre"] == "row_count"
    assert dicts[0]["ok"] is True
    assert "detalle" in dicts[0]
