"""Tests para app.core.date_extractor.

Incluye la regla del skip a dia habil (Fase 6.6). Los casos de sabados que
apuntan a domingos y de festivos encadenados estan en `test_calendario.py`.
Aca solo se prueba la interfaz publica de date_extractor.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.date_extractor import (
    FilenameDateError,
    extract_file_date,
    extract_production_date,
    extract_production_date_verbose,
)


def test_extract_file_date_standard():
    assert extract_file_date("PRE CORTE 13.02.2026.xlsx") == date(2026, 2, 13)


def test_extract_production_date_dia_habil_no_salta():
    """Viernes 13/02 -> sabado 14/02: laboral, sin skip."""
    assert extract_production_date("PRE CORTE 13.02.2026.xlsx") == date(2026, 2, 14)


def test_extract_production_date_verbose_dia_habil():
    fecha, saltados, motivos = extract_production_date_verbose(
        "PRE CORTE 13.02.2026.xlsx"
    )
    assert fecha == date(2026, 2, 14)
    assert saltados == 0
    assert motivos == []


def test_extract_production_date_sabado_salta_a_lunes():
    """Sabado 07/02 apunta a domingo 08/02 -> skip a lunes 09/02.

    Este es el escenario reportado por el usuario que motivo la Fase 6.6.
    """
    fecha, saltados, motivos = extract_production_date_verbose(
        "PRE CORTE 07.02.2026.xlsx"
    )
    assert fecha == date(2026, 2, 9)
    assert saltados == 1
    assert len(motivos) == 1
    assert "Domingo" in motivos[0]


def test_extract_production_date_borde_fin_de_mes_salta():
    """Sabado 28/02/2026 -> domingo 01/03/2026 (no laboral) -> lunes 02/03/2026."""
    assert extract_production_date("PRE CORTE 28.02.2026.xlsx") == date(2026, 3, 2)


def test_extract_production_date_con_ruta_completa():
    assert extract_production_date(
        "C:/algo/PRE CORTE 13.02.2026.xlsx - NOTIFICACION.csv"
    ) == date(2026, 2, 14)


def test_extract_production_date_separador_guion():
    assert extract_production_date("PRE CORTE 13-02-2026.xlsx") == date(2026, 2, 14)


def test_extract_production_date_lanza_error_si_no_matchea():
    with pytest.raises(FilenameDateError):
        extract_production_date("otro_nombre_sin_fecha.xlsx")


def test_extract_file_date_fecha_invalida():
    with pytest.raises(FilenameDateError):
        extract_file_date("PRE CORTE 32.13.2026.xlsx")
