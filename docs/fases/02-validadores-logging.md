# Fase 2 — Validadores anti-perdida y logging

**Estado:** completada  
**Fecha de referencia:** 2026-02-18

## Objetivo

Garantizar **cero perdida de datos** en cada transformacion y trazabilidad
operativa con logs rotativos.

## Modulos

| Modulo | Responsabilidad |
|--------|-----------------|
| `validators.py` | Checks de suma preservada, filas contabilizadas |
| `logging_setup.py` | Logging estructurado con rotacion en `logs/` |

## Tests

- `tests/test_validators.py`

## Criterio de exito

Toda fila de entrada termina en `cruce` o en `no_cruzados` con motivo explicito.
