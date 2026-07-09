"""Autenticacion, sesiones y autorizacion para la API."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status

from app.api.dependencies import db_connection
from app.config import (
    AUTH_COOKIE_DOMAIN,
    AUTH_COOKIE_NAME,
    AUTH_COOKIE_SAMESITE,
    AUTH_COOKIE_SECURE,
    CSRF_COOKIE_NAME,
    LOGIN_SESSION_ABSOLUTE_HOURS,
    LOGIN_SESSION_IDLE_MINUTES,
)
from app.security.passwords import token_hash


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


@dataclass(slots=True)
class AuthUser:
    user_id: int
    email: str
    is_active: bool
    must_change_pwd: bool
    roles: set[str]
    permissions: set[str]
    auth_kind: str
    session_hash: str | None = None
    csrf_token: str | None = None

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        return "admin" in self.roles or permission in self.permissions


def set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    cookie_kwargs: dict[str, Any] = {
        "secure": AUTH_COOKIE_SECURE,
        "domain": AUTH_COOKIE_DOMAIN,
        "samesite": AUTH_COOKIE_SAMESITE,
        "path": "/",
    }
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=session_token,
        httponly=True,
        **cookie_kwargs,
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        **cookie_kwargs,
    )


def clear_auth_cookies(response: Response) -> None:
    cookie_kwargs: dict[str, Any] = {
        "secure": AUTH_COOKIE_SECURE,
        "domain": AUTH_COOKIE_DOMAIN,
        "samesite": AUTH_COOKIE_SAMESITE,
        "path": "/",
    }
    response.delete_cookie(key=AUTH_COOKIE_NAME, **cookie_kwargs)
    response.delete_cookie(key=CSRF_COOKIE_NAME, **cookie_kwargs)


def _build_auth_user(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    auth_kind: str,
    session_hash: str | None,
    csrf_token: str | None,
) -> AuthUser:
    user_row = conn.execute(
        """
        SELECT id, email, is_active, must_change_pwd
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    if not user_row or int(user_row["is_active"]) != 1:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion invalida")

    roles_rows = conn.execute(
        """
        SELECT r.code
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = ?
        """,
        (user_id,),
    ).fetchall()
    roles = {str(r["code"]) for r in roles_rows}

    perms_rows = conn.execute(
        """
        SELECT DISTINCT p.code
        FROM user_roles ur
        JOIN role_permissions rp ON rp.role_id = ur.role_id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE ur.user_id = ?
        """,
        (user_id,),
    ).fetchall()
    permissions = {str(p["code"]) for p in perms_rows}

    return AuthUser(
        user_id=int(user_row["id"]),
        email=str(user_row["email"]),
        is_active=True,
        must_change_pwd=bool(user_row["must_change_pwd"]),
        roles=roles,
        permissions=permissions,
        auth_kind=auth_kind,
        session_hash=session_hash,
        csrf_token=csrf_token,
    )


def _auth_via_service_token(conn: sqlite3.Connection, raw_token: str) -> AuthUser:
    token_h = token_hash(raw_token)
    row = conn.execute(
        """
        SELECT user_id, expires_at, revoked_at
        FROM service_tokens
        WHERE token_hash = ?
        """,
        (token_h,),
    ).fetchone()
    if not row or row["revoked_at"] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalido")
    expires_at = _parse_dt(row["expires_at"])
    if expires_at and expires_at <= _now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    conn.execute(
        "UPDATE service_tokens SET last_used_at = ? WHERE token_hash = ?",
        (_now().isoformat(), token_h),
    )
    conn.commit()
    return _build_auth_user(
        conn,
        user_id=int(row["user_id"]),
        auth_kind="service_token",
        session_hash=None,
        csrf_token=None,
    )


def _auth_via_session(conn: sqlite3.Connection, raw_token: str) -> AuthUser:
    session_hash = token_hash(raw_token)
    row = conn.execute(
        """
        SELECT user_id, csrf_token, expires_at, max_expires_at, revoked_at
        FROM sessions
        WHERE id = ?
        """,
        (session_hash,),
    ).fetchone()
    if not row or row["revoked_at"] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion invalida")

    now = _now()
    expires_at = _parse_dt(row["expires_at"])
    max_expires_at = _parse_dt(row["max_expires_at"])
    if expires_at is None or max_expires_at is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion invalida")
    if expires_at <= now or max_expires_at <= now:
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE id = ?",
            (now.isoformat(), session_hash),
        )
        conn.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesion expirada")

    new_expires = min(
        now + timedelta(minutes=LOGIN_SESSION_IDLE_MINUTES),
        max_expires_at,
    )
    conn.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
        (now.isoformat(), new_expires.isoformat(), session_hash),
    )
    conn.commit()
    return _build_auth_user(
        conn,
        user_id=int(row["user_id"]),
        auth_kind="session",
        session_hash=session_hash,
        csrf_token=str(row["csrf_token"]),
    )


def _csrf_required(request: Request) -> bool:
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    path = request.url.path
    if path in {"/auth/login", "/auth/logout"}:
        return False
    return True


def require_auth(
    request: Request,
    conn: sqlite3.Connection = Depends(db_connection),
) -> AuthUser:
    cached = getattr(request.state, "auth_user", None)
    if isinstance(cached, AuthUser):
        return cached

    auth_header = request.headers.get("authorization", "")
    auth_user: AuthUser
    if auth_header.lower().startswith("bearer "):
        raw_token = auth_header.split(" ", 1)[1].strip()
        if not raw_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalido")
        auth_user = _auth_via_service_token(conn, raw_token)
    else:
        raw_session = request.cookies.get(AUTH_COOKIE_NAME, "")
        if not raw_session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")
        auth_user = _auth_via_session(conn, raw_session)

    if auth_user.auth_kind == "session" and _csrf_required(request):
        header_token = request.headers.get("x-csrf-token", "")
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
        if (
            not header_token
            or not cookie_token
            or header_token != cookie_token
            or header_token != (auth_user.csrf_token or "")
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF invalido")

    if (
        auth_user.auth_kind == "session"
        and auth_user.must_change_pwd
        and request.url.path not in {"/auth/me", "/auth/change-password", "/auth/logout"}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Debe cambiar su contrasena antes de continuar",
        )

    request.state.auth_user = auth_user
    return auth_user


def current_user(user: AuthUser = Depends(require_auth)) -> AuthUser:
    return user


def require_permission(permission: str):
    def _dep(user: AuthUser = Depends(current_user)) -> AuthUser:
        if user.has_permission(permission):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permiso insuficiente: {permission}",
        )

    return _dep


def ensure_owner_access(
    user: AuthUser,
    *,
    owner_user_id: int | None,
    read_all_permission: str,
    read_own_permission: str,
) -> None:
    if user.has_permission(read_all_permission):
        return
    if owner_user_id is not None and user.has_permission(read_own_permission):
        if int(owner_user_id) == user.user_id:
            return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso denegado")


def ensure_run_permission(user: AuthUser, *, owner_user_id: int | None) -> None:
    if user.has_permission("run:execute"):
        return
    if owner_user_id is not None and user.has_permission("run:execute:own"):
        if int(owner_user_id) == user.user_id:
            return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permiso insuficiente para ejecutar")


def compute_session_expirations() -> tuple[str, str]:
    now = _now()
    expires_at = now + timedelta(minutes=LOGIN_SESSION_IDLE_MINUTES)
    max_expires_at = now + timedelta(hours=LOGIN_SESSION_ABSOLUTE_HOURS)
    return expires_at.isoformat(), max_expires_at.isoformat()


def audit_event(
    conn: sqlite3.Connection,
    *,
    action: str,
    outcome: str,
    user_id: int | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (
            ts, user_id, action, resource_type, resource_id,
            outcome, ip, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _now().isoformat(),
            user_id,
            action,
            resource_type,
            resource_id,
            outcome,
            ip,
            json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
        ),
    )
    conn.commit()

