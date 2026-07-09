"""Almacenamiento de archivos subidos + cache tabular seguro.

Cada archivo subido queda en `data/uploads/{carga_id}.{ext}` para reproducibilidad
y en `data/uploads/{carga_id}.parquet` (o fallback `.csv`) para lecturas rapidas.
NO se usa pickle para evitar riesgos de deserializacion insegura.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config import UPLOADS_DIR


def upload_source_path(carga_id: int, ext: str) -> Path:
    ext = ext.lstrip(".").lower()
    return UPLOADS_DIR / f"{carga_id}.{ext}"


def upload_cache_path(carga_id: int) -> Path:
    return UPLOADS_DIR / f"{carga_id}.parquet"


def _upload_cache_csv_path(carga_id: int) -> Path:
    return UPLOADS_DIR / f"{carga_id}.csv.cache"


def _legacy_pickle_path(carga_id: int) -> Path:
    return UPLOADS_DIR / f"{carga_id}.pkl"


def save_source_bytes(carga_id: int, ext: str, content: bytes) -> Path:
    p = upload_source_path(carga_id, ext)
    p.write_bytes(content)
    return p


def save_parsed_df(carga_id: int, df: pd.DataFrame) -> Path:
    """Cachea DataFrame parseado sin deserializacion ejecutable."""
    parquet = upload_cache_path(carga_id)
    csv_fallback = _upload_cache_csv_path(carga_id)
    parquet.unlink(missing_ok=True)
    csv_fallback.unlink(missing_ok=True)
    try:
        df.to_parquet(parquet, index=False)
        return parquet
    except Exception:
        df.to_csv(csv_fallback, index=False)
        return csv_fallback


def load_parsed_df(carga_id: int) -> pd.DataFrame:
    parquet = upload_cache_path(carga_id)
    if parquet.exists():
        return pd.read_parquet(parquet)
    csv_fallback = _upload_cache_csv_path(carga_id)
    if csv_fallback.exists():
        return pd.read_csv(csv_fallback)
    legacy = _legacy_pickle_path(carga_id)
    if legacy.exists():
        raise FileNotFoundError(
            "Cache legado .pkl detectado; recargar archivo para regenerar cache seguro"
        )
    raise FileNotFoundError(f"Cache tabular no encontrado para carga_id={carga_id}")


def has_parsed_df(carga_id: int) -> bool:
    return (
        upload_cache_path(carga_id).exists()
        or _upload_cache_csv_path(carga_id).exists()
    )
