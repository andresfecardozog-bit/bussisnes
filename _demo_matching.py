"""Demostracion end-to-end del matching con los archivos reales.

Corre el pipeline completo:
1. Reset del catalogo SKU (para prueba limpia).
2. Import de la homologacion externa.
3. Load PRE CORTE 13.02.2026 (1).xlsx -> RESUMEN + NOTIFICACION + pair-learn.
4. Load FLASH.xlsx (todo el mes).
5. Extract fecha de produccion desde el nombre del PRE CORTE.
6. Aggregate del FLASH por (fecha, material).
7. Match PRE CORTE vs FLASH por MATERIAL para la fecha de produccion.
8. Reporte legible: matched, solo_pre, solo_flash, top desviaciones.
9. Validaciones anti-perdida.
"""
from __future__ import annotations

import textwrap

import pandas as pd

from app.core.aggregator import aggregate_flash
from app.core.date_extractor import extract_file_date, extract_production_date
from app.core.db import get_conn, init_db
from app.core.loaders import load_flash, load_pre_corte
from app.core.matcher import match_by_material
from app.core.sku_catalog import (
    catalog_stats,
    import_from_homologacion,
)
from app.core.validators import run_all_validations

PRE_CORTE_PATH = "PRE CORTE 13.02.2026 (1).xlsx"
FLASH_PATH = "FLASH.xlsx"
HOMOLOG_PATH = "homologacion materiales Nuevo.xlsx"

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 220)
pd.set_option("display.max_colwidth", 40)
pd.set_option("display.float_format", "{:,.2f}".format)


def _titulo(t: str) -> None:
    print("\n" + "=" * 90)
    print(f"  {t}")
    print("=" * 90)


_titulo("PASO 0 - Reset del catalogo SKU (para demo limpia)")
init_db()
with get_conn() as conn:
    conn.execute("DELETE FROM sku_catalog")
    conn.commit()
print("Catalogo reseteado.")

_titulo(f"PASO 1 - Import homologacion: {HOMOLOG_PATH}")
with get_conn() as conn:
    stats_import = import_from_homologacion(HOMOLOG_PATH, conn)
    stats_cat = catalog_stats(conn)
print(f"Import stats:  {stats_import}")
print(f"Catalog stats: {stats_cat}")

_titulo(f"PASO 2 - Cargar PRE CORTE: {PRE_CORTE_PATH}")
pre_df, pre_meta = load_pre_corte(PRE_CORTE_PATH)
print(f"Filename:               {pre_meta['filename']}")
print(f"Hash SHA256:            {pre_meta['hash_sha256'][:16]}...")
print(f"Fuente primaria:        {pre_meta['fuente_primaria']}")
print(f"Notificacion presente:  {pre_meta['notificacion_presente']}")
print(f"Filas RESUMEN original: {pre_meta['num_filas_original']}")
print(f"Filas procesadas:       {pre_meta['num_filas_procesadas']}")
print(f"Filas SIN SAP:          {pre_meta['num_filas_sin_sap']}")
print(f"Total unidades RESUMEN: {pre_meta['resumen_total_unidades']:,.0f}")
if pre_meta.get("notificacion_total_unidades") is not None:
    print(f"Total unid. NOTIF.:     {pre_meta['notificacion_total_unidades']:,.0f}")
print(f"Pair-learn stats:       {pre_meta.get('pair_learn_stats')}")

_titulo("Detalle del PRE CORTE listo para matchear")
mostrar = pre_df[[
    "material", "referencia", "tipo", "formato",
    "unidades_por_empaque", "necesidad_bandeja", "notificado",
]].copy()
mostrar.columns = ["SAP", "REF", "TIPO", "FORMATO", "UND/PACK", "BANDEJAS", "UNIDADES"]
print(mostrar.to_string(index=False))

_titulo("PASO 3 - Fechas extraidas del nombre del PRE CORTE")
fecha_archivo = extract_file_date(PRE_CORTE_PATH)
fecha_produccion = extract_production_date(PRE_CORTE_PATH)
print(f"Fecha archivo:    {fecha_archivo}  (dia en que se emitio el pre corte)")
print(f"Fecha produccion: {fecha_produccion}  (dia que se produce = archivo + 1)")

_titulo(f"PASO 4 - Cargar FLASH mensual: {FLASH_PATH}")
flash_df, flash_meta = load_flash(FLASH_PATH)
print(f"Filas FLASH originales:   {flash_meta['num_filas_original']:,}")
print(f"Filas FLASH procesadas:   {flash_meta['num_filas_procesadas']:,}")
print(f"Hash SHA256:              {flash_meta['hash_sha256'][:16]}...")

fechas_flash = flash_df["fecha_factura"].dropna().unique()
print(f"Fechas distintas en FLASH: {len(fechas_flash)}")
if len(fechas_flash) > 0:
    print(f"  min: {min(fechas_flash)}  max: {max(fechas_flash)}")

if fecha_produccion in fechas_flash:
    n_flash_dia = int((flash_df["fecha_factura"] == fecha_produccion).sum())
    print(f"Filas FLASH del {fecha_produccion}: {n_flash_dia}")
else:
    print(f"AVISO: la fecha {fecha_produccion} NO aparece en FLASH.")

_titulo(f"PASO 5 - Aggregate FLASH por (fecha={fecha_produccion}, material)")
agg_df = aggregate_flash(flash_df, fecha_produccion)
print(f"Materiales distintos facturados ese dia: {len(agg_df)}")
print(f"Total unidades facturadas ese dia:       {agg_df['cantidad_neta_total'].sum():,.0f}")
print("\nTop 15 materiales facturados el dia de produccion:")
top15 = agg_df.nlargest(15, "cantidad_neta_total")[
    ["material", "nomb_material", "cantidad_neta_total", "num_facturas"]
]
top15.columns = ["SAP", "NOMBRE FLASH", "UNIDADES REALES", "N FACTURAS"]
print(top15.to_string(index=False))

_titulo("PASO 6 - Match PRE CORTE vs FLASH por MATERIAL")
result = match_by_material(pre_df, agg_df, fecha_produccion)
summary = result.summary()
print(f"Matched (planeado y vendido):       {summary['matched']}")
print(f"Solo pre corte (plan sin venta):    {summary['solo_pre_corte']}")
print(f"Solo flash (venta sin plan):        {summary['solo_flash']}")
print(f"Total no cruzados:                  {summary['no_cruzados']}")

_titulo("MATCHED - detalle del cruce")
matched = result.matched.copy()
if not matched.empty:
    cols_show = ["material", "referencia", "notificado_unidades",
                 "real_unidades_flash", "delta_unidades", "cumplimiento_pct"]
    tabla = matched[cols_show].copy()
    tabla.columns = ["SAP", "REF", "PLAN UNID", "REAL UNID", "DELTA", "CUMPL %"]
    tabla = tabla.sort_values("PLAN UNID", ascending=False)
    print(tabla.to_string(index=False))

    total_plan = matched["notificado_unidades"].sum()
    total_real = matched["real_unidades_flash"].sum()
    cumpl_ponderado = (total_real / total_plan * 100) if total_plan > 0 else 0.0
    print(f"\nTotal planeado (matched): {total_plan:,.0f}")
    print(f"Total real (matched):     {total_real:,.0f}")
    print(f"Cumplimiento ponderado:   {cumpl_ponderado:.2f}%")
else:
    print("(sin filas matched)")

_titulo("SOLO PRE CORTE - se planeo pero no facturo ese dia")
solo_pre = result.solo_pre_corte
if not solo_pre.empty:
    tabla = solo_pre[["material", "referencia", "notificado"]].copy()
    tabla.columns = ["SAP", "REF", "PLAN UNID"]
    tabla = tabla.sort_values("PLAN UNID", ascending=False)
    print(tabla.to_string(index=False))
else:
    print("(vacio - todo lo planeado se facturo)")

_titulo("SOLO FLASH - se facturo pero no aparece en el PRE CORTE")
solo_flash = result.solo_flash
if not solo_flash.empty:
    tabla = solo_flash[["material", "nomb_material", "real_unidades_flash", "num_facturas"]].copy()
    tabla.columns = ["SAP", "NOMBRE FLASH", "REAL UNID", "N FACT"]
    tabla = tabla.sort_values("REAL UNID", ascending=False)
    print(tabla.head(20).to_string(index=False))
    if len(solo_flash) > 20:
        print(f"... y {len(solo_flash) - 20} filas mas")
    print(f"\nTotal unidades solo_flash: {solo_flash['real_unidades_flash'].sum():,.0f}")
else:
    print("(vacio - todo lo facturado estaba planeado)")

_titulo("TOP 5 DESVIACIONES (mayor delta absoluto)")
if not matched.empty:
    top_desv = matched.reindex(
        matched["delta_unidades"].abs().sort_values(ascending=False).index
    ).head(5)
    tabla = top_desv[["material", "referencia", "notificado_unidades",
                      "real_unidades_flash", "delta_unidades", "cumplimiento_pct"]].copy()
    tabla.columns = ["SAP", "REF", "PLAN", "REAL", "DELTA", "CUMPL %"]
    print(tabla.to_string(index=False))
else:
    print("(sin filas matched)")

_titulo("PASO 7 - Validaciones anti-perdida")
validaciones = run_all_validations(
    pre_corte_path=PRE_CORTE_PATH,
    pre_corte_df=pre_df,
    pre_corte_meta=pre_meta,
    flash_path=FLASH_PATH,
    flash_df=flash_df,
    flash_meta=flash_meta,
    match_result=result,
)
for v in validaciones:
    tag = "[OK]  " if v.ok else "[FAIL]"
    print(f"{tag} {v.nombre}")
    for k, valor in v.detalle.items():
        if k == "sin_sap_detalle" and not valor:
            continue
        if isinstance(valor, (int, float)):
            print(f"         {k}: {valor:,}" if isinstance(valor, int)
                  else f"         {k}: {valor:,.4f}")
        else:
            texto = str(valor)
            if len(texto) > 80:
                texto = textwrap.shorten(texto, width=80)
            print(f"         {k}: {texto}")

total_ok = sum(1 for v in validaciones if v.ok)
print(f"\nResumen validaciones: {total_ok}/{len(validaciones)} OK")

_titulo("FIN - demo completada")
