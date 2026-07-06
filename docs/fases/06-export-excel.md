# Fase 6 — Export Excel formateado (KPI cumplimiento)

**Estado:** completada  
**Fecha de referencia:** 2026-07-06

## Objetivo

Entregable final para el equipo de BI: un `.xlsx` corporativo, no una conexion
directa a Power BI.

## KPI unico

**Cumplimiento %** = `unidades_reales_flash / unidades_plan_resumen * 100`

## Hojas del archivo

1. **Portada** — logo + tabla Indicador | Valor (7 filas).
2. **Resumen** — una fila por `fecha_produccion` + TOTAL.
3. **Por_Categoria** — plan/real/cumplimiento por TIPO (A, AA, AAA, ...).
4. **Detalle_Material** — dump legible de `cruce`.
5. **No_Cruzados** — fugas con `origen` y motivo.

## Modulos

| Modulo | Rol |
|--------|-----|
| `excel_style.py` | Paleta NutriAvicola, NamedStyles, tablas Excel |
| `exporters.py` | Ensambla hojas desde SQLite |
| `/kpis/excel` | Descarga HTTP por rango de fechas |

## Demo

```powershell
venv\Scripts\python.exe _demo_export.py
```

## Tests

- `tests/test_excel_style.py`
- `tests/test_exporters.py` (incluye test anti-import directo de estilos)
