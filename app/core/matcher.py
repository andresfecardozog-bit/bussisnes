"""Cruce PRE CORTE vs FLASH agregado por codigo SAP MATERIAL.

Join deterministico sin necesidad de LLM. Retorna tres particiones:
- matched: material presente en ambos, con delta y cumplimiento %.
- solo_pre_corte: notificado pero no facturado en el dia.
- solo_flash: facturado sin notificacion previa (posibles fugas).

CLI de conveniencia:
    python -m app.core.matcher --pre-corte X.xlsx --flash Y.xlsx

Sirve como smoke test rapido equivalente al notebook.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.config import (
    FLASH_KEY_MATERIAL,
    PRE_CORTE_KEY,
)
from app.core.aggregator import aggregate_flash
from app.core.date_extractor import extract_production_date
from app.core.loaders import load_flash, load_pre_corte


@dataclass
class MatchResult:
    fecha_produccion: date
    matched: pd.DataFrame
    solo_pre_corte: pd.DataFrame
    solo_flash: pd.DataFrame
    no_cruzados: pd.DataFrame

    def summary(self) -> dict[str, int]:
        return {
            "matched": len(self.matched),
            "solo_pre_corte": len(self.solo_pre_corte),
            "solo_flash": len(self.solo_flash),
            "no_cruzados": len(self.no_cruzados),
        }


def _compute_cumplimiento(matched: pd.DataFrame) -> pd.DataFrame:
    matched = matched.copy()
    matched["delta_unidades"] = (
        matched["real_unidades_flash"] - matched["notificado_unidades"]
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        matched["cumplimiento_pct"] = np.where(
            matched["notificado_unidades"] > 0,
            matched["real_unidades_flash"] / matched["notificado_unidades"] * 100.0,
            np.nan,
        )
    matched["cumplimiento_pct"] = matched["cumplimiento_pct"].round(2)
    matched["match_bool"] = True
    return matched


def match_by_material(
    pre_corte_df: pd.DataFrame,
    flash_agregado_df: pd.DataFrame,
    fecha_produccion: date,
) -> MatchResult:
    """Outer join por codigo SAP MATERIAL entre PRE CORTE y FLASH agregado."""
    pre = pre_corte_df.rename(
        columns={
            "notificado": "notificado_unidades",
            "producir_unidades": "producir_unidades",
        }
    )[[PRE_CORTE_KEY, "referencia", "notificado_unidades", "producir_unidades"]]

    flash = flash_agregado_df.rename(
        columns={
            "cantidad_neta_total": "real_unidades_flash",
        }
    )[[FLASH_KEY_MATERIAL, "nomb_material", "real_unidades_flash", "num_facturas"]]

    merged = pre.merge(flash, on=PRE_CORTE_KEY, how="outer", indicator=True)

    matched_mask = merged["_merge"] == "both"
    matched = merged[matched_mask].drop(columns=["_merge"]).copy()
    matched = _compute_cumplimiento(matched)
    matched.insert(0, "fecha_produccion", fecha_produccion)

    solo_pre = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
    solo_pre.insert(0, "fecha_produccion", fecha_produccion)

    solo_flash = merged[merged["_merge"] == "right_only"].drop(columns=["_merge"]).copy()
    solo_flash.insert(0, "fecha_produccion", fecha_produccion)

    no_cruzados_rows = []
    for _, row in solo_pre.iterrows():
        no_cruzados_rows.append(
            {
                "fecha_produccion": fecha_produccion,
                "origen": "pre_corte",
                "material": int(row[PRE_CORTE_KEY]),
                "referencia_o_nombre": row["referencia"],
                "valor": float(row["notificado_unidades"] or 0),
                "motivo": "material notificado sin venta en FLASH",
            }
        )
    for _, row in solo_flash.iterrows():
        no_cruzados_rows.append(
            {
                "fecha_produccion": fecha_produccion,
                "origen": "flash",
                "material": int(row[PRE_CORTE_KEY]),
                "referencia_o_nombre": row.get("nomb_material", ""),
                "valor": float(row["real_unidades_flash"] or 0),
                "motivo": "material vendido sin notificacion previa",
            }
        )
    no_cruzados = pd.DataFrame(
        no_cruzados_rows,
        columns=[
            "fecha_produccion",
            "origen",
            "material",
            "referencia_o_nombre",
            "valor",
            "motivo",
        ],
    )

    return MatchResult(
        fecha_produccion=fecha_produccion,
        matched=matched.reset_index(drop=True),
        solo_pre_corte=solo_pre.reset_index(drop=True),
        solo_flash=solo_flash.reset_index(drop=True),
        no_cruzados=no_cruzados.reset_index(drop=True),
    )


def run_full_pipeline(
    pre_corte_path: str | Path,
    flash_path: str | Path,
    fecha_override: date | None = None,
) -> MatchResult:
    """Ejecuta el pipeline completo: load -> aggregate -> match. Util para CLI y tests."""
    pre_df, _pre_meta = load_pre_corte(pre_corte_path)
    flash_df, _flash_meta = load_flash(flash_path)
    fecha_produccion = fecha_override or extract_production_date(str(pre_corte_path))
    flash_agg = aggregate_flash(flash_df, fecha_produccion)
    return match_by_material(pre_df, flash_agg, fecha_produccion)


def run_batch_pipeline(
    pre_corte_paths: list[str | Path],
    flash_path: str | Path,
) -> dict[str, Any]:
    """Cruza N archivos PRE CORTE contra UN unico FLASH cargado una sola vez.

    Escenario tipico: al final del mes se sube el FLASH mensual + todos los
    PRE CORTE diarios acumulados. El FLASH se parsea una vez y se reusa
    para cada fecha, evitando N cargas costosas.
    """
    flash_df, flash_meta = load_flash(flash_path)
    runs: list[dict[str, Any]] = []
    for pre_path in pre_corte_paths:
        pre_df, pre_meta = load_pre_corte(pre_path)
        fecha = extract_production_date(str(pre_path))
        agg = aggregate_flash(flash_df, fecha)
        result = match_by_material(pre_df, agg, fecha)
        runs.append(
            {
                "pre_corte_meta": pre_meta,
                "fecha_produccion": fecha,
                "result": result,
                "summary": result.summary(),
            }
        )
    return {"flash_meta": flash_meta, "runs": runs}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cruce PRE CORTE vs FLASH por codigo SAP MATERIAL."
    )
    parser.add_argument("--pre-corte", required=True, help="Ruta al archivo PRE CORTE")
    parser.add_argument("--flash", required=True, help="Ruta al archivo FLASH")
    parser.add_argument(
        "--fecha",
        default=None,
        help="Override manual de fecha de produccion (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Numero de filas del matched a imprimir",
    )
    args = parser.parse_args(argv)

    fecha_override = (
        date.fromisoformat(args.fecha) if args.fecha else None
    )
    result = run_full_pipeline(args.pre_corte, args.flash, fecha_override)

    print(f"Fecha produccion usada: {result.fecha_produccion}")
    print(f"Resumen: {result.summary()}")
    print(f"\nTop {args.top} filas del cruce:")
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(result.matched.head(args.top).to_string(index=False))
    if not result.no_cruzados.empty:
        print(f"\nNo cruzados ({len(result.no_cruzados)} filas): primeras 5")
        print(result.no_cruzados.head(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
