"""Configuracion global de pytest.

- Anade el root del proyecto a sys.path para que `import app.core.*` funcione.
- Provee fixtures compartidos: `isolated_db_with_catalog` que crea una DB
  aislada por test y le importa la homologacion (para tener el catalogo SKU
  poblado antes de correr load_pre_corte).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Marca para que app.config._load_dotenv_if_present() se salte el .env real.
os.environ.setdefault("PYTEST_VERSION", "true")

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_XLSX = FIXTURES / "PRE_CORTE_muestra.xlsx"
HOMOLOG_XLSX = FIXTURES / "homologacion.xlsx"
FLASH_CSV = FIXTURES / "FLASH_muestra.csv"


@pytest.fixture
def isolated_db_with_catalog(tmp_path, monkeypatch):
    """DB aislada por test con homologacion pre-importada."""
    from app.core.db import get_conn, init_db
    from app.core.sku_catalog import import_from_homologacion

    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    monkeypatch.setattr("app.core.db.ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setattr("app.core.db.ADMIN_INITIAL_PASSWORD", "AdminPass123!")
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    init_db(db_path)
    with get_conn(db_path) as conn:
        import_from_homologacion(HOMOLOG_XLSX, conn)
    yield db_path
