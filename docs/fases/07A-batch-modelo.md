# Fase 7A — Modelo Batch

**Estado:** completada  
**Fecha de referencia:** 2026-07-06

## Objetivo

Unidad principal de trabajo del frontend y del orquestador: agrupa N PRE CORTE
+ 1 FLASH en staging antes del match.

## Estados del batch

```
draft -> ready_to_match -> matching -> matched
                                  -> failed
                                  -> archived
```

## Endpoints `/batches/*`

- CRUD, upload pre-cortes (multipart o ZIP), flash con `year`/`month`.
- `GET .../preview` — dias, colisiones, saltos calendario, flash_ok.
- `POST .../confirm` — bloquea si hay colisiones de `fecha_produccion`.
- `POST .../generate` — persiste runs + export batch completo.
- `GET .../downloads` — lista y descarga consolidado/dailies/zip.

## Reglas de negocio

| Regla | Comportamiento |
|-------|----------------|
| Colisiones | Dos pre-cortes -> misma fecha_produccion: 409 en confirm |
| Periodo flash | Validacion declarada vs fechas reales del archivo |
| ZIP upload | Filtra por regex; archivos malos en `ignorados`, no falla todo |

## Tests

- `tests/test_batches.py` (20 tests)
- `_smoke_e2e_curl.ps1` — smoke manual contra API local
