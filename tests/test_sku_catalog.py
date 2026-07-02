"""Tests para app.core.sku_catalog."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from app.core.db import get_conn, init_db
from app.core.resumen_parser import load_resumen
from app.core.sku_catalog import (
    attach_sap_to_resumen,
    catalog_stats,
    import_from_homologacion,
    list_catalog,
    resolve_sap,
    update_catalog_from_pair,
    upsert_entry,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_XLSX = FIXTURES / "PRE_CORTE_muestra.xlsx"
HOMOLOG_XLSX = FIXTURES / "homologacion.xlsx"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    init_db(db_path)
    yield db_path


def _conn(db_path: Path) -> sqlite3.Connection:
    return get_conn(db_path)


def test_upsert_entry_nueva(isolated_db):
    with _conn(isolated_db) as conn:
        entry_id, es_nueva = upsert_entry(
            conn,
            referencia="MARCA ORO",
            tipo="A",
            formato="AMARRADO",
            unidades_por_empaque=30,
            material_sap=30040,
            fuente="manual",
        )
        conn.commit()
    assert es_nueva
    assert entry_id > 0


def test_upsert_entry_mismo_sap_incrementa_veces_visto(isolated_db):
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=30040, fuente="homologacion")
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=30040, fuente="homologacion")
        conn.commit()
        row = conn.execute("SELECT veces_visto FROM sku_catalog").fetchone()
    assert row["veces_visto"] == 2


def test_upsert_entry_prioridad_pair_override_homologacion(isolated_db):
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=999, fuente="homologacion")
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=30040, fuente="aprendido_pair")
        conn.commit()
        sap = resolve_sap(conn, "X", "A", "AMARRADO", 30)
    assert sap == 30040


def test_upsert_entry_homologacion_no_override_pair(isolated_db):
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=30040, fuente="aprendido_pair")
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=999, fuente="homologacion")
        conn.commit()
        sap = resolve_sap(conn, "X", "A", "AMARRADO", 30)
    assert sap == 30040


def test_upsert_entry_manual_maxima_prioridad(isolated_db):
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=1, fuente="aprendido_pair")
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=99, fuente="manual")
        conn.commit()
        sap = resolve_sap(conn, "X", "A", "AMARRADO", 30)
    assert sap == 99


def test_resolve_sap_aliases_formato(isolated_db):
    """ESTUCHERIA (homologacion) debe resolver desde ESTUCHE (RESUMEN)."""
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="MARCA ORO", tipo="A", formato="ESTUCHERIA",
                     unidades_por_empaque=12, material_sap=30037, fuente="homologacion")
        conn.commit()
        sap = resolve_sap(conn, "MARCA ORO", "A", "ESTUCHE", 12)
    assert sap == 30037


def test_resolve_sap_aliases_termoencogido_vitafilm(isolated_db):
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="CAMPESINO", tipo="AA", formato="TERMOENCOGIDO",
                     unidades_por_empaque=30, material_sap=30018, fuente="homologacion")
        conn.commit()
        sap = resolve_sap(conn, "CAMPESINO", "AA", "VITAFILM", 30)
    assert sap == 30018


def test_import_from_homologacion_inserta_no_ambiguas(isolated_db):
    with _conn(isolated_db) as conn:
        stats = import_from_homologacion(HOMOLOG_XLSX, conn)
        s = catalog_stats(conn)
    assert stats["leidas"] > 0
    assert stats["insertadas"] > 0
    assert stats.get("ambiguas_no_insertadas", 0) >= 0
    assert s["total_entradas"] == stats["insertadas"]
    assert s["por_fuente"].get("homologacion", 0) == stats["insertadas"]


def test_update_catalog_from_pair_aprende_sap_del_notificacion(isolated_db):
    resumen_df = pd.DataFrame([
        {"referencia": "MARCA ORO", "tipo": "A", "formato": "AMARRADO",
         "unidades_por_empaque": 30, "bandejas": 8214.0, "unidades_totales": 246_420.0},
    ])
    notif_df = pd.DataFrame([
        {"material": 30040, "referencia": "HUEVOS ORO A X 30 AMARRADO",
         "necesidad_bandeja": 8214.0, "necesidad_unidades": 246_420.0,
         "fisico_bandejas": 0, "fisico_unidades": 0,
         "producir_bandeja": 8214, "producir_unidades": 246420, "notificado": 246420},
    ])
    with _conn(isolated_db) as conn:
        stats = update_catalog_from_pair(conn, resumen_df, notif_df)
        sap = resolve_sap(conn, "MARCA ORO", "A", "AMARRADO", 30)
    assert stats["aprendidas"] == 1
    assert sap == 30040


def test_update_catalog_from_pair_ignora_ambiguos(isolated_db):
    resumen_df = pd.DataFrame([
        {"referencia": "MARCA ORO", "tipo": "A", "formato": "AMARRADO",
         "unidades_por_empaque": 30, "bandejas": 100.0, "unidades_totales": 3000.0},
    ])
    notif_df = pd.DataFrame([
        {"material": 30040, "referencia": "A", "necesidad_bandeja": 100.0,
         "necesidad_unidades": 3000.0, "fisico_bandejas": 0, "fisico_unidades": 0,
         "producir_bandeja": 0, "producir_unidades": 0, "notificado": 0},
        {"material": 30046, "referencia": "AA", "necesidad_bandeja": 100.0,
         "necesidad_unidades": 3000.0, "fisico_bandejas": 0, "fisico_unidades": 0,
         "producir_bandeja": 0, "producir_unidades": 0, "notificado": 0},
    ])
    with _conn(isolated_db) as conn:
        stats = update_catalog_from_pair(conn, resumen_df, notif_df)
        sap = resolve_sap(conn, "MARCA ORO", "A", "AMARRADO", 30)
    assert stats["ambiguas"] == 1
    assert sap is None


def test_attach_sap_separa_con_y_sin_sap(isolated_db):
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="X", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=42, fuente="manual")
        conn.commit()
    df = pd.DataFrame([
        {"referencia": "X", "tipo": "A", "formato": "AMARRADO",
         "unidades_por_empaque": 30, "bandejas": 1, "unidades_totales": 30},
        {"referencia": "DESCONOCIDO", "tipo": "Z", "formato": "AMARRADO",
         "unidades_por_empaque": 30, "bandejas": 1, "unidades_totales": 30},
    ])
    with _conn(isolated_db) as conn:
        con_sap, sin_sap = attach_sap_to_resumen(conn, df)
    assert len(con_sap) == 1
    assert len(sin_sap) == 1
    assert int(con_sap.iloc[0]["material_sap"]) == 42


def test_list_catalog_con_filtros(isolated_db):
    with _conn(isolated_db) as conn:
        upsert_entry(conn, referencia="MARCA ORO", tipo="A", formato="AMARRADO",
                     unidades_por_empaque=30, material_sap=30040, fuente="manual")
        upsert_entry(conn, referencia="PLUS", tipo="AAA", formato="VITAFILM",
                     unidades_por_empaque=30, material_sap=40265, fuente="manual")
        conn.commit()
        rows = list_catalog(conn, referencia="MARCA ORO")
    assert len(rows) == 1
    assert rows[0]["referencia"] == "MARCA ORO"


def test_integracion_completa_100pct_cobertura_en_fixture(isolated_db):
    """Escenario feliz end-to-end sobre el .xlsx real."""
    with _conn(isolated_db) as conn:
        import_from_homologacion(HOMOLOG_XLSX, conn)
    r_df, _ = load_resumen(FIXTURE_XLSX)
    with _conn(isolated_db) as conn:
        from app.core.loaders import load_notificacion
        n_df, _ = load_notificacion(FIXTURE_XLSX)
        update_catalog_from_pair(conn, r_df, n_df)
        con_sap, sin_sap = attach_sap_to_resumen(conn, r_df)
    assert len(sin_sap) == 0, (
        f"Deberiamos resolver 100% del fixture; sin_sap tiene {len(sin_sap)} filas: "
        f"{sin_sap[['referencia', 'tipo', 'formato', 'unidades_por_empaque']].to_dict(orient='records')}"
    )
    assert len(con_sap) == 18
