# Fase 0 — Exploracion y fix de fecha

**Estado:** completada  
**Fecha de referencia:** 2026-02-13

## Objetivo

Validar manualmente el cruce PRE CORTE vs FLASH en un notebook antes de
productizar la logica. Corregir el bug donde un archivo `PRE CORTE 13.02.2026`
debía cruzar contra produccion del **14/02/2026** (fecha del nombre + 1 dia).

## Entregables

| Artefacto | Descripcion |
|-----------|-------------|
| `test.ipynb` | Sandbox de exploracion; no es codigo productivo |
| `_demo_matching.py` | Script auxiliar para probar matching fuera del notebook |

## Regla de negocio clave

`fecha_produccion = fecha_del_nombre_archivo + 1 dia` (luego extendida en Fase 6.6
con salto a dia habil colombiano).

## Fuera de alcance

- Persistencia, API, UI y export Excel (fases posteriores).
