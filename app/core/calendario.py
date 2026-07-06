"""Calendario laboral colombiano: domingos + festivos oficiales (Ley 51/1983).

**Fuente de datos**: CSV estatico `resources/festivos_colombia_2024_2030.csv`
generado offline por `scripts/generar_calendario.py`. No hay dependencia de la
libreria `holidays` en runtime, ni llamadas a LLMs, ni servicios externos.

**Regla de negocio**: los archivos PRE CORTE se emiten al final del dia
habil para planificar la produccion del dia siguiente. Si el dia siguiente
cae en domingo o festivo (sin operacion), la fecha de produccion se
desplaza al proximo dia habil. Puede saltar varios dias si hay festivos
encadenados (ej. sabado que precede a domingo + festivo lunes -> martes).

API publica:
- `is_no_laboral(fecha) -> bool`
- `motivo_no_laboral(fecha) -> str | None`
- `siguiente_dia_habil(fecha) -> tuple[date, int, list[str]]`
    retorna (fecha_habil, dias_saltados, motivos_saltados)
- `dias_habiles(desde, hasta) -> list[date]` (rango inclusivo)
- `festivos_del_ano(year) -> list[dict]` (para el endpoint /calendario)

El CSV se carga UNA vez al importar el modulo (cache en memoria como
`set[date]` + `dict[date, str]`), asi que los lookups son O(1) y no tocan
disco durante los requests.

**Cuando regenerar el CSV**: cada 3-4 anios, o inmediatamente si el Gobierno
Nacional agrega/quita un festivo. Correr `python scripts/generar_calendario.py`
y commitear el diff.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from typing import Any

CALENDAR_CSV = Path(__file__).resolve().parents[2] / "resources" / "festivos_colombia_2024_2030.csv"

# Cargados una sola vez al importar. Cambiar el CSV requiere reiniciar el proceso.
_NO_LABORALES: set[date] = set()
_MOTIVOS: dict[date, str] = {}
_FESTIVOS_POR_ANO: dict[int, list[dict[str, Any]]] = {}


class CalendarioSinCobertura(RuntimeError):
    """La fecha esta fuera del rango cubierto por el CSV."""


def _parse_fecha(s: str) -> date:
    yyyy, mm, dd = s.split("-")
    return date(int(yyyy), int(mm), int(dd))


def _load_csv(path: Path = CALENDAR_CSV) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Genera el CSV con "
            f"'python scripts/generar_calendario.py'."
        )
    with path.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            f = _parse_fecha(row["fecha"])
            _NO_LABORALES.add(f)
            _MOTIVOS[f] = row["motivo"]
            if row["es_festivo"] == "1":
                _FESTIVOS_POR_ANO.setdefault(f.year, []).append({
                    "fecha": row["fecha"],
                    "motivo": row["motivo"],
                    "es_domingo": row["es_domingo"] == "1",
                })


_load_csv()

# Rango cubierto (calculado al final del load).
COBERTURA_DESDE: date | None = min(_NO_LABORALES) if _NO_LABORALES else None
COBERTURA_HASTA: date | None = max(_NO_LABORALES) if _NO_LABORALES else None


def _assert_cubierta(f: date) -> None:
    if COBERTURA_DESDE is None or COBERTURA_HASTA is None:
        raise CalendarioSinCobertura("Calendario vacio: regenera el CSV.")
    if not (COBERTURA_DESDE.year <= f.year <= COBERTURA_HASTA.year):
        raise CalendarioSinCobertura(
            f"La fecha {f} esta fuera del rango cubierto por el calendario "
            f"({COBERTURA_DESDE.year}-{COBERTURA_HASTA.year}). "
            f"Regenera el CSV extendiendo el rango en scripts/generar_calendario.py."
        )


def is_no_laboral(f: date) -> bool:
    """True si la fecha es domingo o festivo oficial colombiano."""
    _assert_cubierta(f)
    return f in _NO_LABORALES


def motivo_no_laboral(f: date) -> str | None:
    """Devuelve el motivo ('Domingo', 'Jueves Santo', ...) o None si es laboral."""
    _assert_cubierta(f)
    return _MOTIVOS.get(f)


def siguiente_dia_habil(f: date) -> tuple[date, int, list[str]]:
    """Retorna (fecha_habil, dias_saltados, motivos_saltados).

    Si `f` ya es dia habil, retorna (f, 0, []).
    Si no, avanza dia a dia hasta encontrar uno habil. Falla con
    CalendarioSinCobertura si necesita mirar mas alla del rango del CSV.
    """
    cur = f
    saltados = 0
    motivos: list[str] = []
    while is_no_laboral(cur):
        motivos.append(f"{cur.isoformat()}: {_MOTIVOS[cur]}")
        cur = cur + timedelta(days=1)
        saltados += 1
        if saltados > 15:
            # Salvaguarda contra bugs: nunca hay 15 dias no laborales seguidos.
            raise RuntimeError(
                f"Bucle sospechoso en siguiente_dia_habil desde {f}: "
                f"{saltados} dias saltados sin encontrar laboral."
            )
    return cur, saltados, motivos


def dias_habiles(desde: date, hasta: date) -> list[date]:
    """Lista de dias habiles en [desde, hasta] (ambos inclusivos)."""
    if hasta < desde:
        return []
    _assert_cubierta(desde)
    _assert_cubierta(hasta)
    out: list[date] = []
    cur = desde
    while cur <= hasta:
        if cur not in _NO_LABORALES:
            out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def festivos_del_ano(year: int) -> list[dict[str, Any]]:
    """Lista de festivos oficiales del anio (excluye domingos puros).

    Para el endpoint /calendario/no-laborales?year=Y del frontend, que
    dibuja el calendario del wizard.
    """
    if year < (COBERTURA_DESDE.year if COBERTURA_DESDE else 0) or year > (
        COBERTURA_HASTA.year if COBERTURA_HASTA else 9999
    ):
        raise CalendarioSinCobertura(
            f"Anio {year} fuera del rango del calendario "
            f"({COBERTURA_DESDE.year}-{COBERTURA_HASTA.year})."
        )
    return list(_FESTIVOS_POR_ANO.get(year, []))
