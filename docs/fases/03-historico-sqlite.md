# Fase 3 — Historico SQLite acumulado

**Estado:** completada  
**Fecha de referencia:** 2026-02-21

## Objetivo

Persistir cada cruce aprobado en SQLite con idempotencia por par
`(pre_corte_hash, flash_hash)`.

## Modulo principal

- `app/core/db.py` — schema, `persist_run`, consultas de historico.

## Escenario batch

- N archivos PRE CORTE (uno por dia habil) + 1 FLASH mensual.
- Un `run_id` por PRE CORTE; el FLASH se reutiliza sin re-parsear N veces.

## Tests

- `tests/test_db.py`

## Notas

La base vive en `data/historico.sqlite` (excluida de Git; se crea en runtime).
