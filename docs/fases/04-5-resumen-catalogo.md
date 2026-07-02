# Fase 4.5 — RESUMEN + catalogo SKU

**Estado:** completada  
**Fecha de referencia:** 2026-07-02

## Problema resuelto

La hoja NOTIFICACION es aviso de bodega, no el plan de comercializacion.
La fuente autoritativa pasa a ser la hoja **RESUMEN** del `.xlsx`.

## Cambios clave

| Componente | Cambio |
|------------|--------|
| `resumen_parser.py` | Parseo con openpyxl (merged cells) |
| `sku_catalog.py` | Puente REFERENCIA/TIPO/FORMATO -> SAP MATERIAL |
| `loaders.py` | Orquesta RESUMEN + NOTIFICACION opcional |
| `/catalog/*` | Import homologacion, mapeo manual, listado |

## Fuentes del catalogo (prioridad)

1. `manual` (maxima)
2. `aprendido_pair` (emparejamiento con NOTIFICACION)
3. `homologacion` (Excel externo)

## Fixtures requeridos

- `tests/fixtures/PRE_CORTE_muestra.xlsx`
- `tests/fixtures/homologacion.xlsx`

El script de historial copia estos desde los archivos en la raiz del proyecto
si existen.

## Regla de intake

PRE CORTE debe ser `.xlsx` / `.xlsm`; CSV destruye merged cells del RESUMEN.
