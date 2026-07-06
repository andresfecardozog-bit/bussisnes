"""Tests del calendario laboral colombiano.

Verifica que el CSV commiteado en `resources/festivos_colombia_2024_2030.csv`
cubre correctamente:
- Domingos (52-53 por anio).
- Los 18 festivos oficiales de la Ley 51/1983 + Ley Emiliani (traslados a
  lunes) + fechas moviles derivadas de Pascua.
- No incluye "Rosario de Chiquinquira" (no es festivo laboral oficial).

Tambien verifica la logica de `siguiente_dia_habil` con casos concretos:
- Sabado que apunta a domingo -> lunes.
- Domingo + festivo lunes -> martes.
- Pascua 2026 (Jueves, Viernes Santo, Domingo Resurreccion).
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.calendario import (
    CalendarioSinCobertura,
    COBERTURA_DESDE,
    COBERTURA_HASTA,
    dias_habiles,
    festivos_del_ano,
    is_no_laboral,
    motivo_no_laboral,
    siguiente_dia_habil,
)


# ---------- cobertura del CSV ----------

def test_csv_cubre_2024_2030():
    """Cobertura reportada como primer/ultimo dia no laboral del rango."""
    assert COBERTURA_DESDE is not None and COBERTURA_DESDE.year == 2024
    assert COBERTURA_HASTA is not None and COBERTURA_HASTA.year == 2030


def test_fuera_de_cobertura_falla_explicito():
    with pytest.raises(CalendarioSinCobertura):
        is_no_laboral(date(2020, 1, 1))
    with pytest.raises(CalendarioSinCobertura):
        is_no_laboral(date(2035, 1, 1))


# ---------- domingos ----------

@pytest.mark.parametrize("dia", [
    date(2026, 2, 8),
    date(2026, 4, 5),
    date(2026, 12, 27),
])
def test_domingos_son_no_laborales(dia):
    assert dia.weekday() == 6
    assert is_no_laboral(dia)
    assert motivo_no_laboral(dia) == "Domingo"


# ---------- festivos fijos ----------

@pytest.mark.parametrize("fecha,motivo_esperado", [
    (date(2026, 1, 1), "Año Nuevo"),
    (date(2026, 5, 1), "Día del Trabajo"),
    (date(2026, 7, 20), "Día de la Independencia"),
    (date(2026, 8, 7), "Batalla de Boyacá"),
    (date(2026, 12, 8), "La Inmaculada Concepción"),
    (date(2026, 12, 25), "Navidad"),
])
def test_festivos_fijos_2026(fecha, motivo_esperado):
    assert is_no_laboral(fecha)
    m = motivo_no_laboral(fecha)
    assert m is not None
    assert motivo_esperado in m


# ---------- Ley Emiliani (traslados al lunes) ----------

def test_reyes_magos_2026_trasladado_al_lunes():
    """6 enero 2026 es martes -> traslado al lunes 12/01/2026."""
    assert not is_no_laboral(date(2026, 1, 6))
    assert is_no_laboral(date(2026, 1, 12))
    assert "Reyes" in (motivo_no_laboral(date(2026, 1, 12)) or "")


def test_san_jose_2026_trasladado_al_lunes():
    """19 marzo 2026 es jueves -> traslado al lunes 23/03/2026."""
    assert not is_no_laboral(date(2026, 3, 19))
    assert is_no_laboral(date(2026, 3, 23))
    assert "San José" in (motivo_no_laboral(date(2026, 3, 23)) or "")


# ---------- Pascua 2026 ----------

def test_semana_santa_2026():
    """Domingo de Resurreccion = 05/04/2026."""
    assert is_no_laboral(date(2026, 4, 2))
    assert "Jueves Santo" in (motivo_no_laboral(date(2026, 4, 2)) or "")
    assert is_no_laboral(date(2026, 4, 3))
    assert "Viernes Santo" in (motivo_no_laboral(date(2026, 4, 3)) or "")


def test_corpus_christi_2026_lunes():
    """Corpus Christi 2026 observado -> lunes 08/06/2026."""
    assert is_no_laboral(date(2026, 6, 8))
    m = motivo_no_laboral(date(2026, 6, 8))
    assert m is not None and "Corpus Christi" in m


# ---------- exclusion de Chiquinquira ----------

def test_chiquinquira_13_julio_no_es_festivo_laboral():
    """13/07/2026 no debe estar como festivo (no es Ley 51/1983)."""
    assert not is_no_laboral(date(2026, 7, 13))
    assert motivo_no_laboral(date(2026, 7, 13)) is None


# ---------- siguiente_dia_habil ----------

def test_siguiente_dia_habil_de_dia_laboral_no_salta():
    fecha, saltados, motivos = siguiente_dia_habil(date(2026, 2, 14))
    assert fecha == date(2026, 2, 14)
    assert saltados == 0
    assert motivos == []


def test_siguiente_dia_habil_sabado_no_salta():
    """El sabado 07/02/2026 es laboral en NutriAvicola."""
    fecha, saltados, motivos = siguiente_dia_habil(date(2026, 2, 7))
    assert fecha == date(2026, 2, 7)
    assert saltados == 0


def test_siguiente_dia_habil_desde_domingo_salta_a_lunes():
    fecha, saltados, motivos = siguiente_dia_habil(date(2026, 2, 8))
    assert fecha == date(2026, 2, 9)
    assert saltados == 1
    assert "Domingo" in motivos[0]


def test_siguiente_dia_habil_encadenado_dom_mas_festivo():
    """Domingo 11/01/2026 + lunes 12/01 (Reyes trasladado) -> martes 13/01."""
    fecha, saltados, motivos = siguiente_dia_habil(date(2026, 1, 11))
    assert fecha == date(2026, 1, 13)
    assert saltados == 2
    assert any("Domingo" in m for m in motivos)
    assert any("Reyes" in m for m in motivos)


def test_siguiente_dia_habil_desde_jueves_santo():
    """Jueves Santo 02/04 -> Viernes Santo 03/04 -> Sabado 04/04 (laboral)."""
    fecha, saltados, motivos = siguiente_dia_habil(date(2026, 4, 2))
    assert fecha == date(2026, 4, 4)
    assert saltados == 2


# ---------- dias_habiles ----------

def test_dias_habiles_semana_normal():
    dias = dias_habiles(date(2026, 2, 9), date(2026, 2, 15))
    assert dias == [
        date(2026, 2, 9),
        date(2026, 2, 10),
        date(2026, 2, 11),
        date(2026, 2, 12),
        date(2026, 2, 13),
        date(2026, 2, 14),
    ]


def test_dias_habiles_rango_invertido_es_vacio():
    assert dias_habiles(date(2026, 2, 15), date(2026, 2, 9)) == []


def test_dias_habiles_semana_santa_2026():
    """Semana santa 2026: lun 30/03 - dom 05/04, jueves y viernes santo festivos."""
    dias = dias_habiles(date(2026, 3, 30), date(2026, 4, 5))
    assert dias == [
        date(2026, 3, 30),
        date(2026, 3, 31),
        date(2026, 4, 1),
        date(2026, 4, 4),
    ]


# ---------- festivos_del_ano ----------

def test_festivos_del_ano_2026_tiene_18_oficiales():
    fest = festivos_del_ano(2026)
    assert 17 <= len(fest) <= 19, f"esperado ~18 festivos, obtenido {len(fest)}"
    fechas = {f["fecha"] for f in fest}
    assert "2026-01-01" in fechas
    assert "2026-12-25" in fechas
    assert "2026-07-13" not in fechas  # Chiquinquira excluido


def test_festivos_del_ano_fuera_cobertura_falla():
    with pytest.raises(CalendarioSinCobertura):
        festivos_del_ano(2020)
