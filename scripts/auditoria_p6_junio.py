"""Auditoria matematica independiente del cruce CEN P6 vs SAP junio.

No usa agentes ni profiles: carga los archivos crudos con pandas y
calcula a mano fechas, tasa de cruce y nivel de entrega, para contrastar
contra lo que reporto el pipeline.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CEN_PATH = ROOT / "data_nivel_cumplimiento" / "2026" / "Acumulado CEN P6 2026.xlsx"
SAP_PATH = ROOT / "data_nivel_cumplimiento" / "data meses" / "junio.XLS"

CEN_ORDER_RE = re.compile(r"^\d{3}-\d+$")


def cargar_cen() -> pd.DataFrame:
    xls = pd.ExcelFile(CEN_PATH)
    hoja = xls.sheet_names[0]
    df = pd.read_excel(CEN_PATH, sheet_name=hoja)
    print(f"CEN hoja='{hoja}' filas={len(df):,} cols={len(df.columns)}")
    return df


def cargar_sap() -> pd.DataFrame:
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "junio.xlsx"
    shutil.copy(SAP_PATH, tmp)
    df = pd.read_excel(tmp, sheet_name="Sheet1", header=None)
    df = df.dropna(how="all")
    print(f"SAP filas={len(df):,} cols={len(df.columns)}")
    return df


def main() -> int:
    cen = cargar_cen()
    sap = cargar_sap()

    print("\n=== 1. FECHAS DEL CEN P6 ===")
    col_fecha = next(c for c in cen.columns if "F. Documento" in str(c))
    fechas = pd.to_datetime(cen[col_fecha], errors="coerce", dayfirst=True)
    print(f"columna: {col_fecha}")
    print(f"rango: {fechas.min()} -> {fechas.max()}")
    por_mes = fechas.dt.to_period("M").value_counts().sort_index()
    print("filas por mes:")
    for periodo, n in por_mes.items():
        print(f"  {periodo}: {n:,}")

    print("\n=== 2. KEYS ===")
    col_orden = "Numero de la Orden de compra"
    col_item = "Codigo item proveedor"
    col_cant = next(c for c in cen.columns if str(c).strip() == "Cantidad Total")
    cen_orden = cen[col_orden].astype(str).str.strip()
    cen_item = cen[col_item].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    print(f"CEN ordenes unicas: {cen_orden.nunique():,}")
    print(f"CEN lineas orden+item unicas: {(cen_orden + '|' + cen_item).nunique():,}")

    sap_orden = sap[55].astype(str).str.strip()  # col 56 (0-based 55)
    sap_mat = sap[39].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)  # col 40
    sap_cant = pd.to_numeric(sap[41], errors="coerce").fillna(0)  # col 42
    sap_tipo_op = sap[12].astype(str).str.strip()  # col 13 tipo operacion

    es_cen = sap_orden.str.match(CEN_ORDER_RE)
    print(f"SAP filas con orden formato CEN: {int(es_cen.sum()):,} de {len(sap):,}")
    print(f"SAP ordenes CEN unicas: {sap_orden[es_cen].nunique():,}")
    dev_mask = sap_tipo_op.str.upper().eq("DEVOLUCIONES")
    print(f"SAP filas DEVOLUCIONES: {int(dev_mask.sum()):,}")

    print("\n=== 3. CRUCE MANUAL (orden+material) ===")
    cen_g = (
        pd.DataFrame({
            "orden": cen_orden,
            "mat": cen_item,
            "plan": pd.to_numeric(cen[col_cant], errors="coerce").fillna(0),
        })
        .groupby(["orden", "mat"], as_index=False)["plan"].sum()
    )
    sap_cen = pd.DataFrame({
        "orden": sap_orden[es_cen & ~dev_mask],
        "mat": sap_mat[es_cen & ~dev_mask],
        "real": sap_cant[es_cen & ~dev_mask],
    })
    sap_g = sap_cen.groupby(["orden", "mat"], as_index=False)["real"].sum()

    m = cen_g.merge(sap_g, on=["orden", "mat"], how="outer", indicator=True)
    matched = m[m["_merge"] == "both"]
    solo_cen = m[m["_merge"] == "left_only"]
    solo_sap = m[m["_merge"] == "right_only"]
    print(f"matched lineas: {len(matched):,}")
    print(f"solo CEN: {len(solo_cen):,}  solo SAP: {len(solo_sap):,}")
    tasa = len(matched) / (len(matched) + len(solo_cen)) * 100
    print(f"tasa cruce CEN: {tasa:.1f}%")

    plan_total = float(cen_g["plan"].sum())
    real_matched = float(matched["real"].sum())
    print(f"unidades plan CEN (todas): {plan_total:,.0f}")
    print(f"unidades entregadas (matched): {real_matched:,.0f}")
    print(f"cumplimiento unidades global: {real_matched / plan_total * 100:.1f}%")

    print("\n=== 4. NIVEL DE SERVICIO POR PEDIDO ===")
    lineas = m[m["_merge"] != "right_only"].copy()
    lineas["real"] = lineas["real"].fillna(0)
    lineas["completa"] = lineas["real"] >= lineas["plan"]
    lineas["tiene_algo"] = lineas["real"] > 0
    ped = lineas.groupby("orden").agg(
        todas_completas=("completa", "all"),
        alguna_entrega=("tiene_algo", "any"),
    )
    completos = int(ped["todas_completas"].sum())
    no_entregados = int((~ped["alguna_entrega"]).sum())
    parciales = len(ped) - completos - no_entregados
    total = len(ped)
    print(f"pedidos totales: {total:,}")
    print(f"  completos:     {completos:,} ({completos / total * 100:.1f}%)")
    print(f"  parciales:     {parciales:,} ({parciales / total * 100:.1f}%)")
    print(f"  no entregados: {no_entregados:,} ({no_entregados / total * 100:.1f}%)")

    print("\n=== 5. MISMO CALCULO SOLO PEDIDOS DE JUNIO (fecha doc O/C) ===")
    cen_jun_mask = fechas.dt.month.eq(6) & fechas.dt.year.eq(2026)
    cen_jun = (
        pd.DataFrame({
            "orden": cen_orden[cen_jun_mask],
            "mat": cen_item[cen_jun_mask],
            "plan": pd.to_numeric(cen[col_cant], errors="coerce").fillna(0)[cen_jun_mask],
        })
        .groupby(["orden", "mat"], as_index=False)["plan"].sum()
    )
    mj = cen_jun.merge(sap_g, on=["orden", "mat"], how="left", indicator=False)
    mj["real"] = mj["real"].fillna(0)
    tasa_j = (mj["real"] > 0).mean() * 100
    print(f"lineas CEN junio: {len(mj):,}  con entrega SAP: {(mj['real'] > 0).sum():,} ({tasa_j:.1f}%)")
    pj = mj.assign(completa=mj["real"] >= mj["plan"], algo=mj["real"] > 0).groupby("orden").agg(
        todas=("completa", "all"), alguna=("algo", "any")
    )
    tot_j = len(pj)
    comp_j = int(pj["todas"].sum())
    noent_j = int((~pj["alguna"]).sum())
    parc_j = tot_j - comp_j - noent_j
    print(f"pedidos junio: {tot_j:,} -> completos {comp_j:,} ({comp_j/tot_j*100:.1f}%), "
          f"parciales {parc_j:,} ({parc_j/tot_j*100:.1f}%), no entregados {noent_j:,} ({noent_j/tot_j*100:.1f}%)")
    plan_j = float(mj["plan"].sum())
    real_j = float(mj["real"].sum())
    print(f"unidades: plan {plan_j:,.0f} vs entregadas {real_j:,.0f} = {real_j/plan_j*100:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
