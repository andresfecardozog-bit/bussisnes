"""Tests para app.core.resumen_parser."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from app.core.resumen_parser import (
    load_resumen,
    parse_resumen_worksheet,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_XLSX = FIXTURES / "PRE_CORTE_muestra.xlsx"


def test_load_resumen_produce_18_filas_no_cero():
    df, meta = load_resumen(FIXTURE_XLSX)
    assert len(df) == 18, (
        f"Se esperaban 18 filas no-cero en el RESUMEN del fixture; "
        f"recibidas {len(df)}"
    )
    assert meta["num_filas_emitidas"] == 18


def test_load_resumen_total_unidades_igual_al_totales_del_excel():
    df, meta = load_resumen(FIXTURE_XLSX)
    total_esperado = 299_416.0
    assert meta["total_unidades"] == pytest.approx(total_esperado)
    assert float(df["unidades_totales"].sum()) == pytest.approx(total_esperado)


def test_load_resumen_marca_oro_a_amarrado_30_es_8214_bandejas():
    df, _ = load_resumen(FIXTURE_XLSX)
    fila = df[
        (df["referencia"] == "MARCA ORO")
        & (df["tipo"] == "A")
        & (df["formato"] == "AMARRADO")
        & (df["unidades_por_empaque"] == 30)
    ]
    assert len(fila) == 1
    assert float(fila.iloc[0]["bandejas"]) == pytest.approx(8214.0)
    assert float(fila.iloc[0]["unidades_totales"]) == pytest.approx(246_420.0)


def test_load_resumen_ffill_referencia_propaga_correctamente():
    """A9:A14 = MARCA ORO en el xlsx. Las 6 filas (AAAA/AAA/AA/A/B/C) que existan
    deben salir todas con REFERENCIA=MARCA ORO."""
    df, _ = load_resumen(FIXTURE_XLSX)
    marca_oro = df[df["referencia"] == "MARCA ORO"]
    tipos = set(marca_oro["tipo"].unique())
    assert {"AAAA", "AAA", "AA", "A", "B"}.issubset(tipos), (
        f"MARCA ORO deberia tener multiples tipos (AAAA/AAA/AA/A/B); "
        f"encontrados: {tipos}"
    )


def test_load_resumen_no_incluye_totales():
    df, _ = load_resumen(FIXTURE_XLSX)
    assert not (df["referencia"] == "TOTALES").any()
    assert not (df["referencia"] == "TOTAL").any()


def test_load_resumen_formatos_esperados():
    df, meta = load_resumen(FIXTURE_XLSX)
    assert set(meta["formatos_distintos"]).issubset(
        {"AMARRADO", "ESTUCHE", "VITAFILM", "SUELTO"}
    )
    assert not df.empty


def test_load_resumen_rechaza_csv(tmp_path):
    csv_path = tmp_path / "no_es_xlsx.csv"
    csv_path.write_text("no,importa\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="requiere .xlsx"):
        load_resumen(csv_path)


def test_parse_resumen_worksheet_directamente():
    wb = openpyxl.load_workbook(FIXTURE_XLSX, data_only=True)
    ws = wb["RESUMEN"]
    df = parse_resumen_worksheet(ws)
    assert len(df) == 18
    assert set(df.columns) == {
        "referencia", "tipo", "formato", "unidades_por_empaque",
        "bandejas", "unidades_totales",
    }
    wb.close()
