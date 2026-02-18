"""Configuracion de logging: archivo rotativo diario + consola.

Cada ejecucion genera lineas en `logs/run_YYYYMMDD.log` con formato:
    2026-07-02 09:15:22 INFO app.core.matcher [OK] pre_corte_row_count filas_original=37 filas_procesado=37

Los tags `[OK]` y `[FAIL]` estan pensados para grep facil, sin emojis.
"""
from __future__ import annotations

import logging
from datetime import date
from logging.handlers import RotatingFileHandler
from typing import Iterable

from app.config import LOGS_DIR

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configura el logger raiz una sola vez. Idempotente."""
    global _CONFIGURED
    logger = logging.getLogger("app")
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    log_file = LOGS_DIR / f"run_{date.today():%Y%m%d}.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5_000_000,
        backupCount=10,
        encoding="utf-8",
    )
    stream_handler = logging.StreamHandler()

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for h in (file_handler, stream_handler):
        h.setFormatter(fmt)
        h.setLevel(level)
        logger.addHandler(h)

    _CONFIGURED = True
    logger.info("logging inicializado file=%s", log_file)
    return logger


def log_validaciones(validaciones: Iterable, source: str = "") -> None:
    """Escribe una linea por validacion con tag [OK]/[FAIL] para grep."""
    logger = setup_logging()
    for v in validaciones:
        tag = "[OK]" if v.ok else "[FAIL]"
        detalle_str = " ".join(f"{k}={val}" for k, val in v.detalle.items())
        logger.log(
            logging.INFO if v.ok else logging.WARNING,
            "%s %s %s %s",
            tag,
            v.nombre,
            f"source={source}" if source else "",
            detalle_str,
        )
