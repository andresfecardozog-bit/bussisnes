"""Endpoints de autenticacion, sesiones y tokens de servicio."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.dependencies import db_connection
from app.api.security import (
    AuthUser,
    audit_event,
    clear_auth_cookies,
    compute_session_expirations,
    require_auth,
    require_permission,
    set_auth_cookies,
)
from app.config import LOGIN_LOCK_MINUTES, LOGIN_MAX_ATTEMPTS
from app.security.passwords import hash_password, needs_rehash, new_token, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class LoginIn(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=512)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=8, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


class ServiceTokenIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    expires_days: int | None = Field(default=90, ge=1, le=3650)
    user_email: str | None = None


class CreateUserIn(BaseModel):
    email: str
    full_name: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["analista_propios"])
    initial_password: str | None = Field(default=None, min_length=12, max_length=512)
    is_active: bool = True


class UpdateUserIn(BaseModel):
    full_name: str | None = None
    roles: list[str] | None = None
    is_active: bool | None = None
    reset_password: bool = False


def _user_identity(conn: sqlite3.Connection, user_id: int) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT id, email, full_name, must_change_pwd
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    roles = conn.execute(
        """
        SELECT r.code
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = ?
        ORDER BY r.code
        """,
        (user_id,),
    ).fetchall()
    perms = conn.execute(
        """
        SELECT DISTINCT p.code
        FROM user_roles ur
        JOIN role_permissions rp ON rp.role_id = ur.role_id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE ur.user_id = ?
        ORDER BY p.code
        """,
        (user_id,),
    ).fetchall()
    return {
        "id": int(row["id"]),
        "email": str(row["email"]),
        "full_name": row["full_name"],
        "must_change_password": bool(row["must_change_pwd"]),
        "roles": [str(r["code"]) for r in roles],
        "permissions": [str(p["code"]) for p in perms],
    }


def _roles_catalog(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, code FROM roles ORDER BY code").fetchall()
    return {str(r["code"]): int(r["id"]) for r in rows}


def _set_user_roles(conn: sqlite3.Connection, user_id: int, roles: list[str]) -> list[str]:
    catalog = _roles_catalog(conn)
    unique_roles = sorted({r.strip() for r in roles if r and r.strip()})
    if not unique_roles:
        raise HTTPException(status_code=422, detail="Debe asignar al menos un rol")
    unknown = [r for r in unique_roles if r not in catalog]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Roles desconocidos: {unknown}")
    conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    for role_code in unique_roles:
        conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (user_id, catalog[role_code]),
        )
    conn.commit()
    return unique_roles


def _create_session(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    request: Request,
) -> tuple[str, str]:
    raw_session = new_token(48)
    raw_csrf = new_token(24)
    session_h = token_hash(raw_session)
    expires_at, max_expires_at = compute_session_expirations()
    conn.execute(
        """
        INSERT INTO sessions (
            id, user_id, csrf_token, created_at, last_seen_at,
            expires_at, max_expires_at, ip, user_agent
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_h,
            user_id,
            raw_csrf,
            _now().isoformat(),
            _now().isoformat(),
            expires_at,
            max_expires_at,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        ),
    )
    conn.commit()
    return raw_session, raw_csrf


@router.post("/login")
def post_login(
    body: LoginIn,
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    ip = request.client.host if request.client else None
    email = str(body.email).strip().lower()
    row = conn.execute(
        """
        SELECT id, email, password_hash, is_active, must_change_pwd, failed_attempts, locked_until
        FROM users
        WHERE lower(email) = ?
        """,
        (email,),
    ).fetchone()
    if not row or int(row["is_active"]) != 1:
        audit_event(
            conn,
            action="auth.login",
            outcome="denied",
            ip=ip,
            detail={"email": email, "reason": "not_found_or_inactive"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales invalidas")

    lock_until = _parse_dt(row["locked_until"])
    if lock_until and lock_until > _now():
        audit_event(
            conn,
            action="auth.login",
            outcome="denied",
            user_id=int(row["id"]),
            ip=ip,
            detail={"email": email, "reason": "locked"},
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Usuario bloqueado temporalmente por intentos fallidos",
        )

    if not verify_password(body.password, str(row["password_hash"])):
        failed = int(row["failed_attempts"]) + 1
        locked_until = None
        if failed >= LOGIN_MAX_ATTEMPTS:
            locked_until = (_now() + timedelta(minutes=LOGIN_LOCK_MINUTES)).isoformat()
            failed = 0
        conn.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ?, updated_at = ? WHERE id = ?",
            (failed, locked_until, _now().isoformat(), int(row["id"])),
        )
        conn.commit()
        audit_event(
            conn,
            action="auth.login",
            outcome="denied",
            user_id=int(row["id"]),
            ip=ip,
            detail={"email": email, "reason": "bad_password"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales invalidas")

    if needs_rehash(str(row["password_hash"])):
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(body.password), _now().isoformat(), int(row["id"])),
        )
    conn.execute(
        "UPDATE users SET failed_attempts = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
        (_now().isoformat(), int(row["id"])),
    )
    conn.execute(
        "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (_now().isoformat(), int(row["id"])),
    )
    conn.commit()

    session_token, csrf_token = _create_session(conn, user_id=int(row["id"]), request=request)
    set_auth_cookies(response, session_token, csrf_token)
    audit_event(
        conn,
        action="auth.login",
        outcome="ok",
        user_id=int(row["id"]),
        ip=ip,
        detail={"email": email},
    )

    return {
        "ok": True,
        "user": _user_identity(conn, int(row["id"])),
        # Se devuelve el token CSRF en el body ademas de la cookie: en un
        # deploy cross-site (frontend Vercel + backend Railway) el JS del
        # frontend NO puede leer la cookie CSRF (es de otro dominio), asi que
        # necesita el token por respuesta para mandarlo en X-CSRF-Token.
        "csrf_token": csrf_token,
    }


@router.post("/logout")
def post_logout(
    response: Response,
    request: Request,
    user: AuthUser = Depends(require_auth),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, bool]:
    if user.session_hash:
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE id = ?",
            (_now().isoformat(), user.session_hash),
        )
        conn.commit()
    clear_auth_cookies(response)
    audit_event(
        conn,
        action="auth.logout",
        outcome="ok",
        user_id=user.user_id,
        ip=request.client.host if request.client else None,
    )
    return {"ok": True}


@router.get("/me")
def get_me(
    user: AuthUser = Depends(require_auth),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    return {
        "ok": True,
        "user": _user_identity(conn, user.user_id),
        "auth_kind": user.auth_kind,
        # Permite re-obtener el token CSRF tras un refresh de pagina (la
        # sesion sigue viva por cookie) sin forzar re-login.
        "csrf_token": user.csrf_token,
    }


@router.post("/change-password")
def post_change_password(
    body: ChangePasswordIn,
    request: Request,
    response: Response,
    user: AuthUser = Depends(require_auth),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    if user.auth_kind != "session":
        raise HTTPException(status_code=400, detail="Cambio de contrasena no disponible para token de servicio")

    row = conn.execute(
        "SELECT password_hash FROM users WHERE id = ?",
        (user.user_id,),
    ).fetchone()
    if not row or not verify_password(body.current_password, str(row["password_hash"])):
        audit_event(
            conn,
            action="auth.change_password",
            outcome="denied",
            user_id=user.user_id,
            ip=request.client.host if request.client else None,
            detail={"reason": "bad_current_password"},
        )
        raise HTTPException(status_code=401, detail="Contrasena actual invalida")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=422, detail="La nueva contrasena debe ser diferente")

    conn.execute(
        """
        UPDATE users
        SET password_hash = ?, must_change_pwd = 0, updated_at = ?
        WHERE id = ?
        """,
        (hash_password(body.new_password), _now().isoformat(), user.user_id),
    )
    conn.execute(
        "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (_now().isoformat(), user.user_id),
    )
    conn.commit()

    session_token, csrf_token = _create_session(conn, user_id=user.user_id, request=request)
    set_auth_cookies(response, session_token, csrf_token)
    audit_event(
        conn,
        action="auth.change_password",
        outcome="ok",
        user_id=user.user_id,
        ip=request.client.host if request.client else None,
    )
    return {
        "ok": True,
        "user": _user_identity(conn, user.user_id),
        "csrf_token": csrf_token,
    }


@router.get("/tokens")
def list_service_tokens(
    _: AuthUser = Depends(require_permission("users:manage")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT st.id, st.name, st.created_at, st.last_used_at, st.expires_at, st.revoked_at,
               u.email AS user_email
        FROM service_tokens st
        JOIN users u ON u.id = st.user_id
        ORDER BY st.id DESC
        """
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/tokens")
def create_service_token(
    body: ServiceTokenIn,
    user: AuthUser = Depends(require_permission("users:manage")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    target_user_id = user.user_id
    if body.user_email:
        row = conn.execute(
            "SELECT id FROM users WHERE lower(email) = ?",
            (str(body.user_email).strip().lower(),),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Usuario destino no encontrado")
        target_user_id = int(row["id"])
    raw_token = f"nutri_st_{new_token(36)}"
    exp = None
    if body.expires_days is not None:
        exp = (_now() + timedelta(days=body.expires_days)).isoformat()
    cur = conn.execute(
        """
        INSERT INTO service_tokens (
            user_id, name, token_hash, created_at, expires_at, created_by_user_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            target_user_id,
            body.name.strip(),
            token_hash(raw_token),
            _now().isoformat(),
            exp,
            user.user_id,
        ),
    )
    conn.commit()
    audit_event(
        conn,
        action="auth.service_token.create",
        outcome="ok",
        user_id=user.user_id,
        resource_type="service_token",
        resource_id=str(int(cur.lastrowid)),
        detail={"target_user_id": target_user_id, "name": body.name.strip()},
    )
    return {
        "ok": True,
        "token_id": int(cur.lastrowid),
        "token": raw_token,
        "expires_at": exp,
    }


@router.delete("/tokens/{token_id}")
def revoke_service_token(
    token_id: int,
    user: AuthUser = Depends(require_permission("users:manage")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, bool]:
    cur = conn.execute(
        """
        UPDATE service_tokens
        SET revoked_at = ?
        WHERE id = ? AND revoked_at IS NULL
        """,
        (_now().isoformat(), token_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Token no encontrado")
    audit_event(
        conn,
        action="auth.service_token.revoke",
        outcome="ok",
        user_id=user.user_id,
        resource_type="service_token",
        resource_id=str(token_id),
    )
    return {"ok": True}


@router.get("/roles")
def list_roles(
    _: AuthUser = Depends(require_permission("users:manage")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT r.code AS role_code, r.nombre, r.descripcion, p.code AS permission_code
        FROM roles r
        LEFT JOIN role_permissions rp ON rp.role_id = r.id
        LEFT JOIN permissions p ON p.id = rp.permission_id
        ORDER BY r.code, p.code
        """
    ).fetchall()
    grouped: dict[str, dict[str, object]] = {}
    for r in rows:
        code = str(r["role_code"])
        if code not in grouped:
            grouped[code] = {
                "code": code,
                "nombre": r["nombre"],
                "descripcion": r["descripcion"],
                "permissions": [],
            }
        if r["permission_code"]:
            grouped[code]["permissions"].append(str(r["permission_code"]))
    return {"items": list(grouped.values())}


@router.get("/users")
def list_users(
    _: AuthUser = Depends(require_permission("users:manage")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT id, email, full_name, is_active, must_change_pwd, created_at, updated_at
        FROM users
        ORDER BY id ASC
        """
    ).fetchall()
    items = []
    for r in rows:
        d = _user_identity(conn, int(r["id"]))
        d["is_active"] = bool(r["is_active"])
        d["created_at"] = r["created_at"]
        d["updated_at"] = r["updated_at"]
        items.append(d)
    return {"items": items}


@router.post("/users")
def create_user(
    body: CreateUserIn,
    request: Request,
    user: AuthUser = Depends(require_permission("users:manage")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Email invalido")
    exists = conn.execute("SELECT id FROM users WHERE lower(email) = ?", (email,)).fetchone()
    if exists:
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email")
    temp_password = body.initial_password or new_token(16)
    now = _now().isoformat()
    cur = conn.execute(
        """
        INSERT INTO users (
            email, password_hash, full_name, is_active, must_change_pwd,
            failed_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, 0, ?, ?)
        """,
        (
            email,
            hash_password(temp_password),
            body.full_name,
            1 if body.is_active else 0,
            now,
            now,
        ),
    )
    created_id = int(cur.lastrowid)
    assigned_roles = _set_user_roles(conn, created_id, body.roles)
    audit_event(
        conn,
        action="users.create",
        outcome="ok",
        user_id=user.user_id,
        resource_type="user",
        resource_id=str(created_id),
        ip=request.client.host if request.client else None,
        detail={"email": email, "roles": assigned_roles},
    )
    out = _user_identity(conn, created_id)
    out["is_active"] = body.is_active
    out["temporary_password"] = temp_password
    out["must_change_password"] = True
    return {"ok": True, "user": out}


@router.patch("/users/{target_user_id}")
def update_user(
    target_user_id: int,
    body: UpdateUserIn,
    request: Request,
    user: AuthUser = Depends(require_permission("users:manage")),
    conn: sqlite3.Connection = Depends(db_connection),
) -> dict[str, object]:
    row = conn.execute("SELECT id FROM users WHERE id = ?", (target_user_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    fields: list[str] = []
    values: list[object] = []
    if body.full_name is not None:
        fields.append("full_name = ?")
        values.append(body.full_name)
    if body.is_active is not None:
        fields.append("is_active = ?")
        values.append(1 if body.is_active else 0)
    if body.reset_password:
        new_temp = new_token(16)
        fields.append("password_hash = ?")
        values.append(hash_password(new_temp))
        fields.append("must_change_pwd = 1")
        fields.append("failed_attempts = 0")
        fields.append("locked_until = NULL")
    else:
        new_temp = None
    if fields:
        fields.append("updated_at = ?")
        values.append(_now().isoformat())
        values.append(target_user_id)
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", tuple(values))
        conn.commit()
    assigned_roles: list[str] | None = None
    if body.roles is not None:
        assigned_roles = _set_user_roles(conn, target_user_id, body.roles)

    audit_event(
        conn,
        action="users.update",
        outcome="ok",
        user_id=user.user_id,
        resource_type="user",
        resource_id=str(target_user_id),
        ip=request.client.host if request.client else None,
        detail={
            "roles": assigned_roles,
            "is_active": body.is_active,
            "reset_password": body.reset_password,
        },
    )
    out = _user_identity(conn, target_user_id)
    out_row = conn.execute("SELECT is_active FROM users WHERE id = ?", (target_user_id,)).fetchone()
    out["is_active"] = bool(out_row["is_active"]) if out_row else True
    if new_temp:
        out["temporary_password"] = new_temp
    return {"ok": True, "user": out}

