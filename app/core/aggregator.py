"""Agregacion del FLASH filtrado por fecha de produccion."""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.config import (
    FLASH_DATE_COLUMN,
    FLASH_KEY_MATERIAL,
)


AGG_COLUMNS = {
    "cantidad_neta": "cantidad_neta_total",
    "facturado_real": "facturado_real_total",
    "factura": "num_facturas",
}


def aggregate_flash(flash_df: pd.DataFrame, fecha_produccion: date) -> pd.DataFrame:
    """Filtra por fecha y agrupa por material.

    Retorna DataFrame con columnas:
    - material (int)
    - nomb_material (nombre representativo, el primero visto)
    - cantidad_neta_total (float)
    - facturado_real_total (float)
    - num_facturas (int)
    """
    if flash_df.empty:
        return pd.DataFrame(
            columns=[
                FLASH_KEY_MATERIAL,
                "nomb_material",
                "cantidad_neta_total",
                "facturado_real_total",
                "num_facturas",
            ]
        )

    filtered = flash_df[flash_df[FLASH_DATE_COLUMN] == fecha_produccion].copy()
    if filtered.empty:
        return pd.DataFrame(
            columns=[
                FLASH_KEY_MATERIAL,
                "nomb_material",
                "cantidad_neta_total",
                "facturado_real_total",
                "num_facturas",
            ]
        )

    grouped = (
        filtered.groupby(FLASH_KEY_MATERIAL, as_index=False)
        .agg(
            cantidad_neta_total=("cantidad_neta", "sum"),
            facturado_real_total=("facturado_real", "sum"),
            num_facturas=("factura", "count"),
            nomb_material=("nomb_material", "first"),
        )
        .sort_values(FLASH_KEY_MATERIAL)
        .reset_index(drop=True)
    )

    return grouped[
        [
            FLASH_KEY_MATERIAL,
            "nomb_material",
            "cantidad_neta_total",
            "facturado_real_total",
            "num_facturas",
        ]
    ]
