"""Tests del catalogo de procesos predefinidos (habilidades reutilizables)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _wait_job(client: TestClient, resp, timeout: float = 90.0):
    if resp.status_code != 200:
        return resp.status_code, resp.json()
    body = resp.json()
    if "job_id" not in body:
        return resp.status_code, body
    jid = body["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/jobs/{jid}").json()
        if j["status"] == "done":
            return 200, j["result"]
        if j["status"] == "error":
            return int(j["error"]["status"]), {"detail": j["error"]["detail"]}
        time.sleep(0.15)
    raise AssertionError("job no termino a tiempo")

FIXTURES = Path(__file__).parent / "fixtures"
CEN_JUNIO = FIXTURES / "cen" / "cen_junio_muestra.xlsx"
SAP_MUESTRA = FIXTURES / "cen" / "sap_junio_muestra.xlsx"
PRE_CORTE = FIXTURES / "PRE_CORTE_muestra.xlsx"
FLASH = FIXTURES / "FLASH_muestra.csv"
HOMOLOG = FIXTURES / "homologacion.xlsx"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.api.main import app
    from app.api.routes import catalogo as catalogo_route
    from app.core.db import init_db

    db_path = tmp_path / "api.sqlite"
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    monkeypatch.setattr("app.api.dependencies.DB_PATH", db_path)
    monkeypatch.setattr("app.core.db.ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setattr("app.core.db.ADMIN_INITIAL_PASSWORD", "AdminPass123!")
    monkeypatch.setattr("app.config.AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(catalogo_route, "_CATALOG_OUTPUTS", tmp_path / "cat_out")
    monkeypatch.setattr(catalogo_route, "_CATALOG_UPLOADS", tmp_path / "cat_up")
    init_db(db_path)
    c = TestClient(app)
    login = c.post(
        "/auth/login",
        json={"email": "admin@test.local", "password": "AdminPass123!"},
    )
    assert login.status_code == 200, login.text
    csrf = c.cookies.get("nutri_csrf")
    change = c.post(
        "/auth/change-password",
        json={"current_password": "AdminPass123!", "new_password": "AdminPass123!_nueva"},
        headers={"X-CSRF-Token": csrf},
    )
    assert change.status_code == 200, change.text
    c.headers.update({"X-CSRF-Token": c.cookies.get("nutri_csrf", "")})
    return c


def test_catalogo_lista_procesos(client):
    resp = client.get("/catalogo")
    assert resp.status_code == 200, resp.text
    ids = {it["skill_id"] for it in resp.json()["items"]}
    assert "cen_vs_sap" in ids
    assert "pre_corte" in ids


def test_catalogo_requiere_auth():
    from app.api.main import app

    c = TestClient(app)
    assert c.get("/catalogo").status_code == 401


@pytest.mark.skipif(
    not (CEN_JUNIO.exists() and SAP_MUESTRA.exists()),
    reason="faltan fixtures CEN/SAP",
)
def test_catalogo_ejecuta_consolidado_cen_vs_sap(client):
    with CEN_JUNIO.open("rb") as f1, SAP_MUESTRA.open("rb") as f2:
        resp = client.post(
            "/catalogo/cen_vs_sap/ejecutar",
            data={"modo": "consolidado"},
            files=[
                ("left_files", ("cen.xlsx", f1, "application/vnd.ms-excel")),
                ("right_files", ("sap.xlsx", f2, "application/vnd.ms-excel")),
            ],
        )
    status, body = _wait_job(client, resp)
    assert status == 200, body
    assert body["modo"] == "consolidado"
    assert len(body["resultados"]) == 1
    res = body["resultados"][0]
    assert any(a.endswith(".xlsx") for a in res["archivos"])
    assert any(a.endswith(".zip") for a in res["archivos"])

    dl = client.get(f"/catalogo/descargas/{body['run_token']}")
    assert dl.status_code == 200
    assert len(dl.json()["archivos"]) >= 2


@pytest.mark.skipif(
    not (PRE_CORTE.exists() and FLASH.exists() and HOMOLOG.exists()),
    reason="faltan fixtures PRE CORTE/FLASH/homologacion",
)
def test_catalogo_pre_corte_reusa_exportador_legado(client):
    # El catalogo SKU debe estar poblado para que PRE CORTE resuelva materiales.
    from app.core.db import get_conn
    from app.core.sku_catalog import import_from_homologacion

    with get_conn() as conn:
        import_from_homologacion(HOMOLOG, conn)

    with PRE_CORTE.open("rb") as f1, FLASH.open("rb") as f2:
        resp = client.post(
            "/catalogo/pre_corte/ejecutar",
            data={"modo": "consolidado"},
            files=[
                ("left_files", ("PRE CORTE 13.02.2026.xlsx", f1, "application/vnd.ms-excel")),
                ("right_files", ("flash.csv", f2, "text/csv")),
            ],
        )
    status, body = _wait_job(client, resp)
    assert status == 200, body
    assert len(body["resultados"]) == 1
    assert any(a.endswith(".xlsx") for a in body["resultados"][0]["archivos"])
    dl = client.get(f"/catalogo/descargas/{body['run_token']}")
    assert dl.status_code == 200
    assert any(a["path"].endswith(".xlsx") for a in dl.json()["archivos"])
