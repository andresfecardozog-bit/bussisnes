from __future__ import annotations

from fastapi.testclient import TestClient


def _build_client(tmp_path, monkeypatch) -> TestClient:
    import app.api.dependencies as deps
    import app.api.storage as storage
    import app.config as cfg
    import app.core.db as core_db
    from app.api.main import app
    from app.core.db import init_db

    db_path = tmp_path / "sec.sqlite"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(cfg, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(cfg, "AUTH_COOKIE_SECURE", False)
    monkeypatch.setattr(core_db, "DB_PATH", db_path)
    monkeypatch.setattr(core_db, "ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setattr(core_db, "ADMIN_INITIAL_PASSWORD", "AdminPass123!")
    monkeypatch.setattr(deps, "DB_PATH", db_path)
    monkeypatch.setattr(storage, "UPLOADS_DIR", uploads_dir)
    init_db(db_path)
    return TestClient(app)


def _login_and_rotate(client: TestClient) -> str:
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
    return client.cookies.get("nutri_csrf") or ""


def test_protected_endpoint_requires_auth(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    resp = client.get("/batches")
    assert resp.status_code == 401


def test_password_change_required_after_first_login(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    login = client.post(
        "/auth/login",
        json={"email": "admin@test.local", "password": "AdminPass123!"},
    )
    assert login.status_code == 200
    blocked = client.get("/batches")
    assert blocked.status_code == 403


def test_csrf_required_for_mutating_requests(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    _login_and_rotate(client)
    resp = client.post("/batches", json={"nombre": "sin csrf"})
    assert resp.status_code == 403


def test_service_token_can_authenticate_without_cookie(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    csrf = _login_and_rotate(client)
    create = client.post(
        "/auth/tokens",
        json={"name": "n8n-test", "expires_days": 30},
        headers={"X-CSRF-Token": csrf},
    )
    assert create.status_code == 200, create.text
    token = create.json()["token"]
    client.cookies.clear()
    resp = client.get(
        "/calendario/no-laborales?year=2026",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_admin_can_manage_users_and_roles(tmp_path, monkeypatch):
    client = _build_client(tmp_path, monkeypatch)
    csrf = _login_and_rotate(client)

    roles = client.get("/auth/roles")
    assert roles.status_code == 200
    role_codes = {r["code"] for r in roles.json()["items"]}
    assert {"admin", "analista_todos", "analista_propios", "sin_historial"}.issubset(role_codes)

    created = client.post(
        "/auth/users",
        json={
            "email": "analista1@test.local",
            "full_name": "Analista Uno",
            "roles": ["analista_propios"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200, created.text
    user = created.json()["user"]
    assert user["email"] == "analista1@test.local"
    assert user["must_change_password"] is True
    assert user["temporary_password"]

    listed = client.get("/auth/users")
    assert listed.status_code == 200
    found = [u for u in listed.json()["items"] if u["email"] == "analista1@test.local"]
    assert len(found) == 1

    target_id = int(found[0]["id"])
    updated = client.patch(
        f"/auth/users/{target_id}",
        json={"is_active": False, "roles": ["sin_historial"], "reset_password": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    out = updated.json()["user"]
    assert out["is_active"] is False
    assert "sin_historial" in out["roles"]
    assert out.get("temporary_password")
