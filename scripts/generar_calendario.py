"""Genera el CSV de dias no laborales para Colombia.

Se corre UNA VEZ (o cada 3-4 anios) para regenerar
`resources/festivos_colombia_2024_2030.csv`. Este script NO se importa en
runtime: la libreria `holidays` es dev-only. El core lee el CSV directamente
via `app.core.calendario`.

Motivos concretos para no depender de `holidays` en runtime:
- `requirements.txt` mas ligero para Railway.
- Cero fetch/parseo en cada request.
- Datos deterministicos (los festivos oficiales no cambian): commiteados al
  repo se auditan en el diff cuando alguien los actualice.

Uso:
    venv\\Scripts\\python.exe scripts\\generar_calendario.py

Configuracion (edita las constantes abajo si es necesario):
- YEAR_START / YEAR_END: rango a cubrir.
- EXCLUIR_MOTIVOS: substring de motivos que NO son festivo oficial laboral
  segun la Ley 51 de 1983. Se filtran del CSV para que la plataforma no
  bloquee dias en los que la empresa si opera.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import holidays

YEAR_START = 2024
YEAR_END = 2030

# La libreria `holidays` incluye "Dia de Nuestra Senora del Rosario de
# Chiquinquira" (13 julio observado), que es celebracion religiosa NO
# reconocida por la Ley 51 de 1983 como festivo nacional laboral. Se excluye
# por defecto para no bloquear operacion legitima.
EXCLUIR_MOTIVOS: tuple[str, ...] = (
    "Chiquinquira",
    "Chiquinquir",  # con y sin acento por robustez de encoding
)

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "resources" / "festivos_colombia_2024_2030.csv"


def _es_excluido(motivo: str) -> bool:
    return any(substr.lower() in motivo.lower() for substr in EXCLUIR_MOTIVOS)


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    co = holidays.Colombia(years=range(YEAR_START, YEAR_END + 1))

    filas: list[tuple[str, int, int, str]] = []
    d = date(YEAR_START, 1, 1)
    fin = date(YEAR_END, 12, 31)
    total_dom = 0
    total_fest = 0
    total_excluidos = 0

    while d <= fin:
        es_dom = d.weekday() == 6
        motivo_fest = co.get(d)
        if motivo_fest and _es_excluido(motivo_fest):
            total_excluidos += 1
            motivo_fest = None
        es_fest = motivo_fest is not None
        if es_dom or es_fest:
            motivo = motivo_fest or "Domingo"
            filas.append((d.isoformat(), int(es_dom), int(es_fest), motivo))
            if es_dom:
                total_dom += 1
            if es_fest:
                total_fest += 1
        d += timedelta(days=1)

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["fecha", "es_domingo", "es_festivo", "motivo"])
        w.writerows(filas)

    print(f"Escrito: {OUTPUT_PATH}")
    print(f"  Anios:              {YEAR_START} - {YEAR_END}")
    print(f"  Filas totales:      {len(filas):,}")
    print(f"  Domingos:           {total_dom:,}")
    print(f"  Festivos oficiales: {total_fest:,}")
    print(f"  Excluidos (no ley): {total_excluidos}")
    if EXCLUIR_MOTIVOS:
        print(f"  Motivos excluidos:  {EXCLUIR_MOTIVOS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
