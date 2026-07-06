"""Genera un batch completo (N dailies + consolidado + zip) desde el archivo
real de PRE CORTE + FLASH, con la nueva regla del calendario laboral y la
agrupacion de fechas de la Fase 6.5.

Como solo tenemos UN PRE CORTE real (13/02/2026), simulamos N dias
sinteticos duplicando el cruce con cumplimientos variados. Sirve para
validar visualmente el consolidado con hoja Por_Semana + agrupacion.

Uso:
    venv\\Scripts\\python.exe _demo_export.py
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from app.core.aggregator import aggregate_flash
from app.core.date_extractor import extract_file_date, extract_production_date
from app.core.db import get_conn, init_db, persist_run
from app.core.exporters import export_batch_completo, suggested_zip_filename
from app.core.loaders import load_flash, load_pre_corte
from app.core.matcher import match_by_material
from app.core.sku_catalog import import_from_homologacion

PRE_CORTE = "PRE CORTE 13.02.2026 (1).xlsx"
FLASH = "FLASH.xlsx"
HOMOLOG = "homologacion materiales Nuevo.xlsx"

FECHAS_SIMULADAS = [
    (date(2026, 2, 9),  1.02),   # verde
    (date(2026, 2, 10), 0.92),   # amarillo bajo
    (date(2026, 2, 11), 0.78),   # rojo
    (date(2026, 2, 12), 1.12),   # amarillo alto
    (date(2026, 2, 13), 1.00),   # verde
]


def _titulo(t: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {t}")
    print("=" * 78)


_titulo("[1] Reset DB + import homologacion")
init_db()
with get_conn() as conn:
    conn.execute("PRAGMA foreign_keys = OFF;")
    for tbl in ("cruce", "no_cruzados", "pre_corte", "flash_agregado",
                "runs", "cargas", "sku_catalog"):
        conn.execute(f"DELETE FROM {tbl}")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON;")
    stats = import_from_homologacion(HOMOLOG, conn)
    print(f"  homologacion: {stats}")

_titulo("[2] Cargar PRE CORTE + FLASH")
pre_df, pre_meta = load_pre_corte(PRE_CORTE)
flash_df, flash_meta = load_flash(FLASH)
fecha_arch = extract_file_date(PRE_CORTE)
fecha_prod = extract_production_date(PRE_CORTE)
print(f"  fecha_archivo:    {fecha_arch}")
print(f"  fecha_produccion: {fecha_prod}")

_titulo("[3] Aggregate FLASH + match por MATERIAL + persist")
agg = aggregate_flash(flash_df, fecha_prod)
result = match_by_material(pre_df, agg, fecha_prod)
with get_conn() as conn:
    persist_run(
        conn,
        pre_corte_meta=pre_meta,
        pre_corte_df=pre_df,
        flash_meta=flash_meta,
        flash_agregado_df=agg,
        match_result=result,
        fecha_archivo=fecha_arch,
    )
print(f"  cruce base persistido para {fecha_prod}: {len(result.matched)} matched")

_titulo("[4] Inyectar cruce sintetico para 5 fechas adicionales")
with get_conn() as conn:
    conn.execute("PRAGMA foreign_keys = OFF;")
    base = conn.execute(
        "SELECT material, referencia, nomb_material_flash, "
        "notificado_unidades, producir_unidades FROM cruce WHERE fecha_produccion = ?",
        (fecha_prod.isoformat(),),
    ).fetchall()
    for i, (f, factor) in enumerate(FECHAS_SIMULADAS):
        fake_pre = 9000 + i
        fake_flash = 9500 + i
        for r in base:
            notif = float(r["notificado_unidades"])
            real = notif * factor
            conn.execute(
                """INSERT INTO cruce (pre_corte_carga_id, flash_carga_id,
                   fecha_produccion, material, referencia, nomb_material_flash,
                   notificado_unidades, producir_unidades, real_unidades_flash,
                   delta_unidades, cumplimiento_pct, match_bool)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (fake_pre, fake_flash, f.isoformat(), r["material"],
                 r["referencia"], r["nomb_material_flash"], notif,
                 r["producir_unidades"], real, real - notif, factor * 100.0),
            )
        conn.execute(
            """INSERT INTO no_cruzados (pre_corte_carga_id, flash_carga_id,
               fecha_produccion, origen, material, referencia_o_nombre, valor, motivo)
               VALUES (?, ?, ?, 'flash', 99999, 'FUGA DEMO', 500, 'material sin plan')""",
            (fake_pre, fake_flash, f.isoformat()),
        )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON;")
print(f"  fechas sinteticas: {[f.isoformat() for f, _ in FECHAS_SIMULADAS]}")

_titulo("[5] Generar batch completo (dailies + consolidado + zip)")
desde = date(2026, 2, 9)
hasta = date(2026, 2, 14)
output_dir = Path("data/onedrive_export") / f"batch_{datetime.now():%H%M%S}"
out = export_batch_completo(desde, hasta, output_dir)

print(f"  Directorio: {output_dir.resolve()}")
print(f"  Consolidado: {out['consolidado'].name} ({out['consolidado'].stat().st_size / 1024:.1f} KB)")
print(f"  Dailies ({len(out['dailies'])}):")
for d in out["dailies"]:
    print(f"    - {d.name} ({d.stat().st_size / 1024:.1f} KB)")
print(f"  ZIP: {out['zip'].name} ({out['zip'].stat().st_size / 1024:.1f} KB)")
print(f"  Fechas sin datos en rango: {out['fechas_sin_datos_en_rango']}")

_titulo("[6] Abriendo consolidado en Excel")
try:
    os.startfile(out["consolidado"])
    print("  Excel abierto con el consolidado. Revisa hojas: Portada, Resumen,")
    print("  Por_Semana (nueva!), Por_Categoria, Detalle_Material, No_Cruzados.")
    print("  En las 3 ultimas: las fechas ya NO se repiten fila tras fila.")
except Exception as e:
    print(f"  No se pudo abrir automaticamente: {e}")
