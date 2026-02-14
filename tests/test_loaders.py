"""Tests para app.core.loaders (PRE CORTE via RESUMEN + FLASH)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.db import get_conn, init_db
from app.core.loaders import (
    hash_file,
    load_flash,
    load_notificacion,
    load_pre_corte,
)
from app.core.sku_catalog import import_from_homologacion

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_XLSX = FIXTURES / "PRE_CORTE_muestra.xlsx"
HOMOLOG_XLSX = FIXTURES / "homologacion.xlsx"
FLASH_MINI = FIXTURES / "FLASH_muestra.csv"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    init_db(db_path)
    with get_conn(db_path) as conn:
        import_from_homologacion(HOMOLOG_XLSX, conn)
    yield db_path


def test_hash_file_es_deterministico():
    h1 = hash_file(FIXTURE_XLSX)
    h2 = hash_file(FIXTURE_XLSX)
    assert h1 == h2
    assert len(h1) == 64


def test_load_pre_corte_requiere_xlsx(tmp_path):
    csv_path = tmp_path / "no.csv"
    csv_path.write_text("no,importa\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="requiere .xlsx"):
        load_pre_corte(csv_path)


def test_load_pre_corte_columnas_para_matcher(isolated_db):
    df, _ = load_pre_corte(FIXTURE_XLSX)
    columnas_requeridas = {
        "material", "referencia", "notificado", "producir_unidades",
        "necesidad_bandeja", "necesidad_unidades", "producir_bandeja",
        "formato", "tipo", "unidades_por_empaque",
    }
    assert columnas_requeridas.issubset(set(df.columns))


def test_load_pre_corte_meta_incluye_fuente_resumen(isolated_db):
    df, meta = load_pre_corte(FIXTURE_XLSX)
    assert meta["tipo"] == "pre_corte"
    assert meta["fuente_primaria"] == "RESUMEN"
    assert meta["notificacion_presente"] is True
    assert len(meta["hash_sha256"]) == 64
    assert meta["num_filas_original"] == 18
    assert meta["resumen_total_unidades"] == pytest.approx(299_416.0)


def test_load_pre_corte_cobertura_100pct_en_fixture(isolated_db):
    """Con homologacion importada + pair-learn, todas las 18 filas resuelven a SAP."""
    df, meta = load_pre_corte(FIXTURE_XLSX)
    assert meta["num_filas_sin_sap"] == 0
    assert len(df) == 18
    assert df["material"].notna().all()
    assert (df["material"] > 0).all()


def test_load_pre_corte_totales_del_notificado_coinciden_con_resumen(isolated_db):
    df, _ = load_pre_corte(FIXTURE_XLSX)
    assert float(df["notificado"].sum()) == pytest.approx(299_416.0)


def test_load_notificacion_ignora_value_errors():
    """La NOTIFICACION del fixture tiene celdas con #VALUE!; no deben romper el parseo."""
    df, meta = load_notificacion(FIXTURE_XLSX)
    assert not df.empty
    assert "necesidad_unidades" in df.columns
    for col in ("necesidad_unidades", "necesidad_bandeja", "producir_unidades"):
        assert df[col].dtype.kind == "f"


def test_load_notificacion_total_coincide_con_resumen():
    _, meta = load_notificacion(FIXTURE_XLSX)
    assert meta["total_necesidad_unidades"] == pytest.approx(299_416.0)


def test_load_flash_normaliza_material_padding():
    df, _ = load_flash(FLASH_MINI)
    assert df["material"].dtype.kind == "i"
    assert set(df["material"].unique()) == {30040, 30046, 30018, 40789, 99999}


def test_load_flash_parsea_fecha_dayfirst():
    df, _ = load_flash(FLASH_MINI)
    fechas = set(df["fecha_factura"].unique())
    assert date(2026, 2, 14) in fechas
    assert date(2026, 2, 15) in fechas


def test_load_flash_cleaning_facturado_real_con_dolar():
    df, _ = load_flash(FLASH_MINI)
    fila = df[df["factura"] == "INV001"].iloc[0]
    assert fila["facturado_real"] == pytest.approx(2_460_000.0)
    assert fila["cantidad_neta"] == pytest.approx(246_000.0)


def test_load_flash_metadata():
    _, meta = load_flash(FLASH_MINI)
    assert meta["tipo"] == "flash"
    assert meta["num_filas_original"] == 6
