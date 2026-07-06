# Fase 6.6 — Calendario laboral colombiano

**Estado:** completada  
**Fecha de referencia:** 2026-07-06

## Regla de negocio

```
fecha_produccion = siguiente_dia_habil(fecha_archivo + 1)
```

Si el dia siguiente cae en domingo o festivo oficial CO, se salta al proximo
dia habil (puede encadenar varios dias).

## Ejemplos verificados

| Archivo | Sin calendario | Con calendario |
|---------|----------------|----------------|
| Sab 07/02/2026 | Dom 08 | Lun 09 (`dias_saltados=1`) |
| Dom 11/01/2026 | Lun 12 (Reyes) | Mar 13 (`dias_saltados=2`) |

## Modulos y recursos

- `app/core/calendario.py` — lookups O(1) sobre CSV estatico.
- `resources/festivos_colombia_2024_2030.csv` — generado por
  `scripts/generar_calendario.py` (dev dependency `holidays`).
- `GET /calendario/no-laborales?year=Y` — festivos para UI.

## Exclusion explicita

"Dia de Nuestra Senora del Rosario de Chiquinquira" (13 jul) no es festivo
laboral segun Ley 51/1983.

## Tests

- `tests/test_calendario.py`
- Extensiones en `tests/test_date_extractor.py` y `tests/test_api.py`
