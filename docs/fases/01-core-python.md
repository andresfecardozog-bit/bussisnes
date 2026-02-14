# Fase 1 — Core Python deterministico

**Estado:** completada  
**Fecha de referencia:** 2026-02-14

## Objetivo

Extraer la logica pura de matching en modulos testeables bajo `app/core/`,
sin I/O en el nucleo.

## Modulos

| Modulo | Responsabilidad |
|--------|-----------------|
| `date_extractor.py` | Fecha desde nombre de archivo PRE CORTE |
| `loaders.py` | Carga PRE CORTE y FLASH a DataFrames |
| `aggregator.py` | Agregacion por material SAP |
| `matcher.py` | Cruce deterministico por codigo MATERIAL |

## Tests

- `tests/test_date_extractor.py`
- `tests/test_loaders.py`
- `tests/test_aggregator.py`
- `tests/test_matcher.py`
- Fixture: `tests/fixtures/FLASH_muestra.csv`

## Compromisos

- Matching 100% deterministico por SAP; sin LLM.
- Funciones puras; efectos secundarios solo en capas superiores.
