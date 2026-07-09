"""Tests de persistencia de la plataforma: profiles versionados + runs
idempotentes + contabilidad en cruce_generico/no_cruzados_generico."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from app.core.db import get_conn, init_db
from app.platform.engine import run_profile
from app.platform.profile import MatchProfile
from app.platform.store import (
    approve_profile,
    list_profiles,
    list_runs,
    load_profile,
    load_run_matched,
    persist_run,
    save_profile,
)

PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "platform.sqlite"
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    init_db(db_path)
    return db_path


def _mini_profile(version: int = 1) -> MatchProfile:
    return MatchProfile.model_validate(
        {
            "profile_id": "mini_store",
            "version": version,
            "left": {
                "role": "plan",
                "label": "Plan",
                "loader": {
                    "type": "tabular",
                    "header_row": 1,
                    "columns": [
                        {"name": "k", "source": "K", "dtype": "str"},
                        {"name": "plan", "source": "PLAN", "dtype": "float_clean"},
                    ],
                },
            },
            "right": {
                "role": "real",
                "label": "Real",
                "loader": {
                    "type": "tabular",
                    "header_row": 1,
                    "columns": [
                        {"name": "k", "source": "K", "dtype": "str"},
                        {"name": "real", "source": "REAL", "dtype": "float_clean"},
                    ],
                },
            },
            "join": {"keys": [{"left": "k", "right": "k"}]},
            "kpis": [
                {
                    "id": "cumpl",
                    "label": "Cumplimiento",
                    "op": "ratio_pct_of_sums",
                    "numerator": "real",
                    "denominator": "plan",
                }
            ],
        }
    )


def _mini_files(tmp_path: Path) -> tuple[Path, Path]:
    def mk(name, headers, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for r in rows:
            ws.append(r)
        p = tmp_path / name
        wb.save(p)
        return p

    left = mk("plan.xlsx", ["K", "PLAN"], [["A", 100], ["B", 50]])
    right = mk("real.xlsx", ["K", "REAL"], [["A", 90], ["C", 5]])
    return left, right


def test_save_y_load_profile_versionado(db):
    with get_conn(db) as conn:
        save_profile(conn, _mini_profile(1))
        save_profile(conn, _mini_profile(2))
        ultimo = load_profile(conn, "mini_store")
        assert ultimo.version == 2
        v1 = load_profile(conn, "mini_store", version=1)
        assert v1.version == 1
        assert len(list_profiles(conn)) == 2


def test_approve_profile(db):
    with get_conn(db) as conn:
        save_profile(conn, _mini_profile(1), status="proposed")
        approve_profile(conn, "mini_store", 1, aprobado_por="analista")
        rows = list_profiles(conn)
        assert rows[0]["status"] == "approved"
        assert rows[0]["aprobado_por"] == "analista"


def test_approve_inexistente_falla(db):
    with get_conn(db) as conn:
        with pytest.raises(KeyError):
            approve_profile(conn, "no_existe", 1, aprobado_por="x")


def test_persist_run_y_contabilidad(db, tmp_path):
    left, right = _mini_files(tmp_path)
    result = run_profile(_mini_profile(), left_path=left, right_path=right)
    with get_conn(db) as conn:
        info = persist_run(conn, result)
        assert info["reemplazado"] is False
        assert info["filas_cruce"] == 1
        assert info["filas_no_cruzados"] == 2
        matched = load_run_matched(conn, info["run_id"])
        assert len(matched) == 1
        runs = list_runs(conn, "mini_store")
        assert len(runs) == 1
        assert runs[0]["kpis"]["cumpl"] == 90.0


def test_persist_run_idempotente(db, tmp_path):
    left, right = _mini_files(tmp_path)
    result = run_profile(_mini_profile(), left_path=left, right_path=right)
    with get_conn(db) as conn:
        first = persist_run(conn, result)
        second = persist_run(conn, result)
        assert second["reemplazado"] is True
        runs = list_runs(conn, "mini_store")
        assert len(runs) == 1
        # las filas del run reemplazado no quedan huerfanas
        old = load_run_matched(conn, first["run_id"])
        assert old.empty


def test_nueva_version_del_profile_es_run_distinto(db, tmp_path):
    left, right = _mini_files(tmp_path)
    r1 = run_profile(_mini_profile(1), left_path=left, right_path=right)
    r2 = run_profile(_mini_profile(2), left_path=left, right_path=right)
    with get_conn(db) as conn:
        persist_run(conn, r1)
        info2 = persist_run(conn, r2)
        assert info2["reemplazado"] is False
        assert len(list_runs(conn, "mini_store")) == 2
