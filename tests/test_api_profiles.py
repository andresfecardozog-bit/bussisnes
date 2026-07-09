"""Tests de los endpoints /profiles con crew fake (sin API real).

El fixture parchea `app.api.routes.profiles._get_crew` para inyectar el
mismo modelo fake de test_agents_fake; el resto del stack (FastAPI, DB,
motor, persistencia) es real.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from tests.test_agents_fake import make_fake_model


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.agents.crew import Crew
    from app.api.main import app
    from app.api.routes import profiles as profiles_route
    from app.core.db import init_db

    db_path = tmp_path / "api.sqlite"
    uploads = tmp_path / "uploads"
    uploads.mkdir(exist_ok=True)
    monkeypatch.setattr("app.config.DB_PATH", db_path)
    monkeypatch.setattr("app.core.db.DB_PATH", db_path)
    monkeypatch.setattr("app.api.dependencies.DB_PATH", db_path)
    monkeypatch.setattr("app.api.storage.UPLOADS_DIR", uploads)
    monkeypatch.setattr("app.core.db.ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setattr("app.core.db.ADMIN_INITIAL_PASSWORD", "AdminPass123!")
    monkeypatch.setattr("app.config.AUTH_COOKIE_SECURE", False)
    init_db(db_path)
    monkeypatch.setattr(
        profiles_route, "_get_crew", lambda: Crew(model=make_fake_model())
    )
    monkeypatch.setattr(profiles_route, "_PROFILE_UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(profiles_route, "_PROFILE_OUTPUTS", tmp_path / "outputs")
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@test.local", "password": "AdminPass123!"},
    )
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("nutri_csrf")
    assert csrf
    change = client.post(
        "/auth/change-password",
        json={
            "current_password": "AdminPass123!",
            "new_password": "AdminPass123!_nueva",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert change.status_code == 200, change.text
    client.headers.update({"X-CSRF-Token": client.cookies.get("nutri_csrf", "")})
    return client


def _mk_xlsx(tmp_path: Path, name: str, headers: list[str], rows: list[list]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    p = tmp_path / name
    wb.save(p)
    return p


def _mk_homolog_xlsx(tmp_path: Path, name: str = "homologacion.xlsx") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoja2 (2)"
    ws.append(["CODIGO", "MATERIAL", "UND", "TIPO", "CLASE", "TIPO 1", "FLASH"])
    ws.append([30018, "HUEVO AA 30 UND", 30, "ESTUCHE", "AA", "CAMPESINO", ""])
    ws.append([30049, "HUEVO AAA 12 UND", 12, "ESTUCHE", "AAA", "CAMPESINO", ""])
    p = tmp_path / name
    wb.save(p)
    return p


@pytest.fixture
def draft(client, tmp_path):
    plan = _mk_xlsx(
        tmp_path, "ordenes.xlsx",
        ["Numero Orden", "Item", "Cantidad"],
        [["ORD-1", "A", 10], ["ORD-1", "B", 5], ["ORD-2", "A", 3]],
    )
    real = _mk_xlsx(
        tmp_path, "entregas.xlsx",
        ["Pedido", "Entregado"],
        [["ORD-1", 12], ["ORD-3", 9]],
    )
    with plan.open("rb") as f1, real.open("rb") as f2:
        resp = client.post(
            "/profiles/draft",
            data={"profile_id": "api_proc", "brief": "medir cumplimiento de entregas"},
            files={
                "left_file": ("ordenes.xlsx", f1, "application/vnd.ms-excel"),
                "right_file": ("entregas.xlsx", f2, "application/vnd.ms-excel"),
            },
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_draft_propone_y_registra_preguntas(draft):
    assert draft["profile_id"] == "api_proc"
    assert draft["status"]["preguntas_bloqueantes"] == 1
    assert draft["status"]["listo_para_aprobar"] is False
    assert "justificaciones" in draft


def test_proposal_y_chat(client, draft):
    prop = client.get("/profiles/api_proc/proposal").json()
    assert prop["profile"]["profile_id"] == "api_proc"
    assert len(prop["preguntas_abiertas"]) == 1

    chat = client.get("/profiles/api_proc/chat").json()
    tipos = {m["tipo"] for m in chat}
    assert "brief" in tipos
    assert "pregunta" in tipos
    pregunta = next(m for m in chat if m["tipo"] == "pregunta")
    assert pregunta["bloqueante"] is True
    assert pregunta["hipotesis"]


def test_approve_bloqueado_luego_desbloqueado(client, draft):
    resp = client.post("/profiles/api_proc/approve", json={"aprobado_por": "ana"})
    assert resp.status_code == 409

    chat = client.get("/profiles/api_proc/chat").json()
    q = next(m for m in chat if m["tipo"] == "pregunta")
    resp = client.post(
        "/profiles/api_proc/chat",
        json={"mensaje": "Si, agrupar por orden.", "question_id": q["question_id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["status"]["listo_para_aprobar"] is True

    resp = client.post("/profiles/api_proc/approve", json={"aprobado_por": "ana"})
    assert resp.status_code == 200


def test_run_requiere_aprobacion(client, draft):
    resp = client.post("/profiles/api_proc/run", json={})
    assert resp.status_code == 409


def test_run_completo_tras_aprobar(client, draft):
    chat = client.get("/profiles/api_proc/chat").json()
    q = next(m for m in chat if m["tipo"] == "pregunta")
    client.post(
        "/profiles/api_proc/chat",
        json={"mensaje": "Si, agrupar.", "question_id": q["question_id"]},
    )
    client.post("/profiles/api_proc/approve", json={"aprobado_por": "ana"})

    resp = client.post("/profiles/api_proc/run", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["matched"] == 1
    assert body["kpis"]["cumplimiento_global"] == 80.0

    runs = client.get("/profiles/api_proc/runs").json()
    assert len(runs) == 1


def test_generate_produce_descargables(client, draft):
    chat = client.get("/profiles/api_proc/chat").json()
    q = next(m for m in chat if m["tipo"] == "pregunta")
    client.post(
        "/profiles/api_proc/chat",
        json={"mensaje": "Si, agrupar.", "question_id": q["question_id"]},
    )
    client.post("/profiles/api_proc/approve", json={"aprobado_por": "ana"})

    resp = client.post("/profiles/api_proc/generate", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(a.endswith(".xlsx") for a in body["archivos"])
    assert any(a.endswith(".zip") for a in body["archivos"])

    files = client.get("/profiles/api_proc/downloads").json()
    kinds = {f["kind"] for f in files}
    assert kinds == {"excel", "pbip"}

    resp = client.get(f"/profiles/api_proc/downloads/{files[0]['filename']}")
    assert resp.status_code == 200
    assert len(resp.content) > 1000


def test_download_path_traversal_bloqueado(client, draft):
    resp = client.get("/profiles/api_proc/downloads/..%2F..%2Fsecreto.txt")
    assert resp.status_code == 404


def test_generate_requiere_aprobacion(client, draft):
    resp = client.post("/profiles/api_proc/generate", json={})
    assert resp.status_code == 409


def test_telemetry_endpoint(client, draft):
    tele = client.get("/profiles/api_proc/telemetry").json()
    assert tele["llamadas"] == 5


def test_put_profile_editado_a_mano(client, draft):
    prop = client.get("/profiles/api_proc/proposal").json()["profile"]
    prop["version"] = 2
    prop["descripcion"] = "editado por la analista"
    resp = client.put("/profiles/api_proc", json=prop)
    assert resp.status_code == 200
    assert resp.json()["version"] == 2


def test_put_profile_invalido_da_422(client, draft):
    prop = client.get("/profiles/api_proc/proposal").json()["profile"]
    prop["join"]["type"] = "inner"
    resp = client.put("/profiles/api_proc", json=prop)
    assert resp.status_code == 422


def test_draft_sin_api_key_da_503(client, tmp_path, monkeypatch):
    from app.api.routes import profiles as profiles_route
    from fastapi import HTTPException

    def sin_key():
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY no definida")

    monkeypatch.setattr(profiles_route, "_get_crew", sin_key)
    plan = _mk_xlsx(tmp_path, "a.xlsx", ["K"], [["x"]])
    with plan.open("rb") as f1, plan.open("rb") as f2:
        resp = client.post(
            "/profiles/draft",
            data={"profile_id": "p", "brief": "b"},
            files={"left_file": ("a.xlsx", f1), "right_file": ("a.xlsx", f2)},
        )
    assert resp.status_code == 503


def test_post_homologacion_endpoint(client, draft, tmp_path):
    homolog = _mk_homolog_xlsx(tmp_path)
    with homolog.open("rb") as fh:
        resp = client.post(
            "/profiles/api_proc/homologacion",
            files={"homologacion_file": ("homologacion.xlsx", fh, "application/vnd.ms-excel")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["filename"] == "homologacion.xlsx"
    assert body["homologacion_import"]["ok"] is True

    chat = client.get("/profiles/api_proc/chat").json()
    assert any(
        m.get("tipo") == "nota"
        and m.get("role") == "usuario"
        and "homologacion" in str(m.get("contenido", "")).lower()
        for m in chat
    )


def test_draft_acepta_homologacion_opcional(client, tmp_path):
    plan = _mk_xlsx(tmp_path, "plan.xlsx", ["Orden", "Item", "Cantidad"], [["O-1", "A", 3]])
    real = _mk_xlsx(tmp_path, "real.xlsx", ["Pedido", "Entregado"], [["O-1", 3]])
    homolog = _mk_homolog_xlsx(tmp_path)
    with plan.open("rb") as f1, real.open("rb") as f2, homolog.open("rb") as fh:
        resp = client.post(
            "/profiles/draft",
            data={"profile_id": "api_homolog", "brief": "medir cumplimiento con catalogo"},
            files={
                "left_file": ("plan.xlsx", f1, "application/vnd.ms-excel"),
                "right_file": ("real.xlsx", f2, "application/vnd.ms-excel"),
                "homologacion_file": ("homologacion.xlsx", fh, "application/vnd.ms-excel"),
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile_id"] == "api_homolog"
    assert body["homologacion_import"]["ok"] is True
