"""Almacenamiento de archivos subidos + cache de DataFrames parseados.

Cada archivo subido queda en `data/uploads/{carga_id}.{ext}` para reproducibilidad
y en `data/uploads/{carga_id}.pkl` para lecturas rapidas por los endpoints del
pipeline sin re-parsear el xlsx original. Se usa pickle (built-in) en vez de
parquet para evitar la dependencia pesada de pyarrow.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config import UPLOADS_DIR


def upload_source_path(carga_id: int, ext: str) -> Path:
    ext = ext.lstrip(".").lower()
    return UPLOADS_DIR / f"{carga_id}.{ext}"


def upload_cache_path(carga_id: int) -> Path:
    return UPLOADS_DIR / f"{carga_id}.pkl"


def save_source_bytes(carga_id: int, ext: str, content: bytes) -> Path:
    p = upload_source_path(carga_id, ext)
    p.write_bytes(content)
    return p


def save_parsed_df(carga_id: int, df: pd.DataFrame) -> Path:
    """Cachea DataFrame parseado como pickle para lecturas rapidas."""
    p = upload_cache_path(carga_id)
    df.to_pickle(p)
    return p


def load_parsed_df(carga_id: int) -> pd.DataFrame:
    p = upload_cache_path(carga_id)
    if not p.exists():
        raise FileNotFoundError(f"Cache pickle no encontrado para carga_id={carga_id}")
    return pd.read_pickle(p)


def has_parsed_df(carga_id: int) -> bool:
    return upload_cache_path(carga_id).exists()
