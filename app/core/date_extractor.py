"""Extraccion de la fecha de produccion a partir del nombre del archivo PRE CORTE.

Regla de negocio:
1. El archivo `PRE CORTE dd.mm.yyyy.xlsx` se crea al final del dia
   `dd.mm.yyyy` para visualizar la produccion del DIA SIGUIENTE.
2. Si `dd.mm.yyyy + 1` cae en domingo o festivo colombiano oficial (no hay
   FLASH esperado), la fecha de produccion **salta al proximo dia habil**.
   Puede saltar varios dias si hay festivos encadenados
   (ej. sabado -> domingo + festivo lunes -> martes).

Ver `app/core/calendario.py` para la fuente de verdad de dias no laborales.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from app.config import FILENAME_PRE_CORTE_REGEX
from app.core.calendario import siguiente_dia_habil


class FilenameDateError(ValueError):
    """El nombre del archivo no contiene una fecha en el formato esperado."""


def _extract_regex_groups(filename: str) -> tuple[int, int, int]:
    basename = Path(filename).name
    match = re.search(FILENAME_PRE_CORTE_REGEX, basename, flags=re.IGNORECASE)
    if not match:
        raise FilenameDateError(
            f"No se pudo extraer fecha del nombre '{basename}'. "
            f"Formato esperado: 'PRE CORTE dd.mm.yyyy...'."
        )
    dd, mm, yyyy = map(int, match.groups())
    return dd, mm, yyyy


def extract_file_date(filename: str) -> date:
    """Fecha literal del nombre del archivo (dia en que se hizo la visualizacion)."""
    dd, mm, yyyy = _extract_regex_groups(filename)
    try:
        return date(yyyy, mm, dd)
    except ValueError as exc:
        raise FilenameDateError(
            f"Fecha invalida '{yyyy}-{mm:02d}-{dd:02d}' extraida de '{filename}'."
        ) from exc


def extract_production_date(filename: str) -> date:
    """Fecha de produccion a comparar contra el FLASH.

    Regla: `fecha_archivo + 1`, saltando al proximo dia habil si cae en
    domingo o festivo colombiano. Ver `extract_production_date_verbose` si
    ademas necesitas la informacion del salto.
    """
    fecha, _saltados, _motivos = extract_production_date_verbose(filename)
    return fecha


def extract_production_date_verbose(
    filename: str,
) -> tuple[date, int, list[str]]:
    """Retorna `(fecha_produccion, dias_saltados, motivos_saltados)`.

    - `dias_saltados == 0` significa que `fecha_archivo + 1` ya era laboral.
    - `dias_saltados >= 1` indica que la fecha original apuntaba a domingo/
      festivo y se desplazo. `motivos_saltados` describe cada dia saltado.

    Usado por el preview del batch para mostrar transparencia al usuario:
    "Este PRE CORTE apunta originalmente a 2026-02-08 (Domingo). Se
    procesara contra el FLASH del 2026-02-09."
    """
    base = extract_file_date(filename) + timedelta(days=1)
    return siguiente_dia_habil(base)
