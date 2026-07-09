"""Capa de persistencia SQLite + orquestacion transaccional.

Objetivos:
- Historico acumulado creciente con idempotencia por par
  (pre_corte_hash, flash_hash).
- Reprocesar el mismo par NO duplica filas ni en cruce ni en no_cruzados.
- Reprocesar un archivo viejo (backfill) es seguro y observable.
- El orquestador (Power Automate/FastAPI en fases posteriores) consulta y
  actualiza el estado de cada run a traves de las funciones de este modulo.

Schema (6 tablas):
    cargas             - un archivo subido (pre_corte o flash)
    pre_corte          - filas del pre corte parseadas
    flash_agregado     - flash agrupado por (flash_carga, fecha, material)
    cruce              - resultado del match por par (pre_carga, flash_carga, material)
    no_cruzados        - filas sin match, con motivo
    runs               - estado del pipeline por run (para orquestador)
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import pandas as pd

from app.config import ADMIN_EMAIL, ADMIN_INITIAL_PASSWORD, DB_PATH
from app.security.passwords import hash_password, new_token

if TYPE_CHECKING:
    from app.core.matcher import MatchResult

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS cargas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('pre_corte', 'flash')),
        fecha_archivo DATE,
        fecha_produccion DATE,
        hash_sha256 TEXT NOT NULL,
        num_filas_original INTEGER NOT NULL,
        num_filas_procesadas INTEGER NOT NULL,
        uploaded_by_user_id INTEGER REFERENCES users(id),
        cargado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tipo, hash_sha256)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS pre_corte (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        carga_id INTEGER NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
        fecha_produccion DATE NOT NULL,
        material INTEGER NOT NULL,
        referencia TEXT,
        necesidad_bandeja REAL,
        necesidad_unidades REAL,
        fisico_bandejas REAL,
        fisico_unidades REAL,
        producir_bandeja REAL,
        producir_unidades REAL,
        notificado REAL,
        UNIQUE(carga_id, material)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS flash_agregado (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        carga_id INTEGER NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
        fecha_produccion DATE NOT NULL,
        material INTEGER NOT NULL,
        nomb_material_flash TEXT,
        cantidad_neta_total REAL,
        facturado_real_total REAL,
        num_facturas INTEGER,
        UNIQUE(carga_id, fecha_produccion, material)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS cruce (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pre_corte_carga_id INTEGER NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
        flash_carga_id INTEGER NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
        fecha_produccion DATE NOT NULL,
        material INTEGER NOT NULL,
        referencia TEXT,
        nomb_material_flash TEXT,
        notificado_unidades REAL,
        producir_unidades REAL,
        real_unidades_flash REAL,
        delta_unidades REAL,
        cumplimiento_pct REAL,
        match_bool INTEGER,
        generado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(pre_corte_carga_id, flash_carga_id, material)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS no_cruzados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pre_corte_carga_id INTEGER NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
        flash_carga_id INTEGER NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
        fecha_produccion DATE NOT NULL,
        origen TEXT NOT NULL CHECK (origen IN ('pre_corte', 'flash')),
        material INTEGER NOT NULL,
        referencia_o_nombre TEXT,
        valor REAL,
        motivo TEXT,
        UNIQUE(pre_corte_carga_id, flash_carga_id, origen, material)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sku_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referencia TEXT NOT NULL,
        tipo TEXT NOT NULL,
        formato TEXT NOT NULL,
        unidades_por_empaque INTEGER NOT NULL,
        material_sap INTEGER NOT NULL,
        nombre_notificacion TEXT,
        fuente TEXT NOT NULL DEFAULT 'aprendido',
        primera_vez_visto TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ultima_vez_visto TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        veces_visto INTEGER NOT NULL DEFAULT 1,
        UNIQUE(referencia, tipo, formato, unidades_por_empaque)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        parent_run_id TEXT REFERENCES runs(id),
        owner_user_id INTEGER REFERENCES users(id),
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP,
        status TEXT NOT NULL CHECK (status IN (
            'running', 'awaiting_approval', 'approved',
            'rejected', 'failed', 'completed'
        )),
        current_step TEXT,
        pre_corte_carga_id INTEGER REFERENCES cargas(id),
        flash_carga_id INTEGER REFERENCES cargas(id),
        fecha_produccion DATE,
        summary_json TEXT,
        notes TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS batches (
        id TEXT PRIMARY KEY,
        owner_user_id INTEGER REFERENCES users(id),
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL CHECK (status IN (
            'draft', 'ready_to_match', 'matching',
            'matched', 'failed', 'archived'
        )),
        nombre TEXT,
        notas TEXT,
        flash_carga_id INTEGER REFERENCES cargas(id),
        flash_periodo_year INTEGER,
        flash_periodo_month INTEGER,
        output_dir TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS batch_pre_cortes (
        batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
        pre_corte_carga_id INTEGER NOT NULL REFERENCES cargas(id),
        agregado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(batch_id, pre_corte_carga_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        must_change_pwd INTEGER NOT NULL DEFAULT 0,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        nombre TEXT NOT NULL,
        descripcion TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        descripcion TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS role_permissions (
        role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
        PRIMARY KEY (role_id, permission_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS user_roles (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, role_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        max_expires_at TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT,
        revoked_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS service_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_by_user_id INTEGER REFERENCES users(id),
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_used_at TEXT,
        expires_at TEXT,
        revoked_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        user_id INTEGER REFERENCES users(id),
        action TEXT NOT NULL,
        resource_type TEXT,
        resource_id TEXT,
        outcome TEXT NOT NULL,
        ip TEXT,
        detail TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_cargas_hash ON cargas(tipo, hash_sha256);",
    "CREATE INDEX IF NOT EXISTS idx_cruce_fecha ON cruce(fecha_produccion);",
    "CREATE INDEX IF NOT EXISTS idx_cruce_material ON cruce(material);",
    "CREATE INDEX IF NOT EXISTS idx_pre_corte_fecha ON pre_corte(fecha_produccion);",
    "CREATE INDEX IF NOT EXISTS idx_flash_fecha ON flash_agregado(fecha_produccion);",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);",
    "CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id);",
    "CREATE INDEX IF NOT EXISTS idx_catalog_sap ON sku_catalog(material_sap);",
    "CREATE INDEX IF NOT EXISTS idx_catalog_fuente ON sku_catalog(fuente);",
    "CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);",
    "CREATE INDEX IF NOT EXISTS idx_batches_updated_at ON batches(updated_at);",
    "CREATE INDEX IF NOT EXISTS idx_batch_pre_cortes_batch ON batch_pre_cortes(batch_id);",
    "CREATE INDEX IF NOT EXISTS idx_cargas_owner ON cargas(uploaded_by_user_id);",
    "CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs(owner_user_id);",
    "CREATE INDEX IF NOT EXISTS idx_batches_owner ON batches(owner_user_id);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_service_tokens_user ON service_tokens(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_user_ts ON audit_log(user_id, ts);",
]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in rows)


def _ensure_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    col_name = column_def.split()[0]
    if _column_exists(conn, table, col_name):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def _seed_rbac(conn: sqlite3.Connection) -> None:
    permissions: dict[str, str] = {
        "users:manage": "Gestionar usuarios y tokens de servicio",
        "files:upload": "Subir archivos",
        "files:read:all": "Leer archivos de cualquier usuario",
        "files:read:own": "Leer archivos propios",
        "profiles:read:all": "Leer perfiles de cualquier usuario",
        "profiles:read:own": "Leer perfiles propios",
        "profiles:write": "Crear/editar perfiles",
        "profiles:approve": "Aprobar perfiles",
        "batches:read:all": "Leer batches de cualquier usuario",
        "batches:read:own": "Leer batches propios",
        "batches:write": "Crear/editar/generar batches",
        "run:execute": "Ejecutar corridas de cualquier usuario",
        "run:execute:own": "Ejecutar corridas propias",
        "download:all": "Descargar archivos de cualquier usuario",
        "download:own": "Descargar archivos propios",
        "catalog:read": "Leer catalogo",
        "catalog:write": "Gestionar catalogo",
        "audit:read": "Leer auditoria",
    }
    for code, desc in permissions.items():
        conn.execute(
            """
            INSERT INTO permissions (code, descripcion)
            VALUES (?, ?)
            ON CONFLICT(code) DO UPDATE SET descripcion = excluded.descripcion
            """,
            (code, desc),
        )

    roles = {
        "admin": ("Administrador", "Acceso total"),
        "analista_todos": ("Analista global", "Accede a todos los recursos"),
        "analista_propios": ("Analista propio", "Accede solo a recursos propios"),
        "sin_historial": ("Operador sin historial", "Puede ejecutar cruces sin consultar historico"),
    }
    for code, (nombre, descripcion) in roles.items():
        conn.execute(
            """
            INSERT INTO roles (code, nombre, descripcion)
            VALUES (?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET nombre = excluded.nombre, descripcion = excluded.descripcion
            """,
            (code, nombre, descripcion),
        )

    role_permissions = {
        "admin": set(permissions.keys()),
        "analista_todos": {
            "files:upload",
            "files:read:all",
            "profiles:read:all",
            "profiles:write",
            "profiles:approve",
            "batches:read:all",
            "batches:write",
            "run:execute",
            "download:all",
            "catalog:read",
            "catalog:write",
        },
        "analista_propios": {
            "files:upload",
            "files:read:own",
            "profiles:read:own",
            "profiles:write",
            "profiles:approve",
            "batches:read:own",
            "batches:write",
            "run:execute:own",
            "download:own",
            "catalog:read",
        },
        "sin_historial": {
            "files:upload",
            "profiles:write",
            "run:execute:own",
        },
    }
    role_ids = {
        str(r["code"]): int(r["id"])
        for r in conn.execute("SELECT id, code FROM roles").fetchall()
    }
    perm_ids = {
        str(r["code"]): int(r["id"])
        for r in conn.execute("SELECT id, code FROM permissions").fetchall()
    }
    for role_code, perm_codes in role_permissions.items():
        role_id = role_ids[role_code]
        for perm_code in perm_codes:
            perm_id = perm_ids[perm_code]
            conn.execute(
                """
                INSERT OR IGNORE INTO role_permissions (role_id, permission_id)
                VALUES (?, ?)
                """,
                (role_id, perm_id),
            )


def _bootstrap_admin(conn: sqlite3.Connection) -> int:
    admin = conn.execute(
        "SELECT id FROM users WHERE lower(email) = ?",
        (ADMIN_EMAIL.lower(),),
    ).fetchone()
    if admin:
        return int(admin["id"])
    initial_password = ADMIN_INITIAL_PASSWORD or new_token(16)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO users (
            email, password_hash, full_name, is_active, must_change_pwd,
            failed_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, 1, 1, 0, ?, ?)
        """,
        (ADMIN_EMAIL.lower(), hash_password(initial_password), "Administrador", now, now),
    )
    admin_id = int(cur.lastrowid)
    role_row = conn.execute(
        "SELECT id FROM roles WHERE code = 'admin'"
    ).fetchone()
    if role_row:
        conn.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
            (admin_id, int(role_row["id"])),
        )
    if ADMIN_INITIAL_PASSWORD is None:
        print(
            "[SECURITY] Admin inicial creado. Email="
            f"{ADMIN_EMAIL} password_temporal={initial_password}"
        )
    return admin_id


def _backfill_owners(conn: sqlite3.Connection, admin_user_id: int) -> None:
    conn.execute(
        "UPDATE cargas SET uploaded_by_user_id = ? WHERE uploaded_by_user_id IS NULL",
        (admin_user_id,),
    )
    conn.execute(
        "UPDATE runs SET owner_user_id = ? WHERE owner_user_id IS NULL",
        (admin_user_id,),
    )
    conn.execute(
        "UPDATE batches SET owner_user_id = ? WHERE owner_user_id IS NULL",
        (admin_user_id,),
    )


def init_db(path: str | Path | None = None) -> Path:
    """Crea las tablas si no existen. Idempotente.

    `DB_PATH` se resuelve dinamicamente para que tests puedan monkeypatch
    `app.core.db.DB_PATH` y afecten esta funcion.
    """
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        for stmt in _SCHEMA:
            conn.execute(stmt)
        _ensure_column(conn, "cargas", "uploaded_by_user_id INTEGER REFERENCES users(id)")
        _ensure_column(conn, "runs", "owner_user_id INTEGER REFERENCES users(id)")
        _ensure_column(conn, "batches", "owner_user_id INTEGER REFERENCES users(id)")
        _seed_rbac(conn)
        admin_user_id = _bootstrap_admin(conn)
        _backfill_owners(conn, admin_user_id)
        conn.commit()
    return p


@contextmanager
def get_conn(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager que abre conexion, activa FKs y garantiza cierre."""
    p = Path(path) if path else DB_PATH
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def _fecha_iso(d: date | str | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, str):
        return d
    return d.isoformat()


def already_loaded(conn: sqlite3.Connection, tipo: str, hash_sha256: str) -> int | None:
    """Retorna el `carga_id` si el archivo ya fue cargado antes, o None."""
    row = conn.execute(
        "SELECT id FROM cargas WHERE tipo = ? AND hash_sha256 = ?",
        (tipo, hash_sha256),
    ).fetchone()
    return int(row["id"]) if row else None


def get_or_insert_carga(
    conn: sqlite3.Connection,
    meta: dict[str, Any],
    fecha_archivo: date | None = None,
    fecha_produccion: date | None = None,
    uploaded_by_user_id: int | None = None,
) -> tuple[int, bool]:
    """Inserta la carga o retorna la existente si ya se cargo. `(carga_id, es_nueva)`."""
    existing = already_loaded(conn, meta["tipo"], meta["hash_sha256"])
    if existing is not None:
        if uploaded_by_user_id is not None:
            conn.execute(
                """
                UPDATE cargas
                SET uploaded_by_user_id = COALESCE(uploaded_by_user_id, ?)
                WHERE id = ?
                """,
                (uploaded_by_user_id, existing),
            )
        return existing, False
    cur = conn.execute(
        """
        INSERT INTO cargas (
            filename, tipo, fecha_archivo, fecha_produccion,
            hash_sha256, num_filas_original, num_filas_procesadas, uploaded_by_user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            meta["filename"],
            meta["tipo"],
            _fecha_iso(fecha_archivo),
            _fecha_iso(fecha_produccion),
            meta["hash_sha256"],
            int(meta["num_filas_original"]),
            int(meta["num_filas_procesadas"]),
            uploaded_by_user_id,
        ),
    )
    return int(cur.lastrowid), True


def persist_pre_corte(
    conn: sqlite3.Connection,
    carga_id: int,
    fecha_produccion: date,
    df: pd.DataFrame,
) -> int:
    """Inserta filas del pre_corte. Si ya existe la carga, no duplica."""
    fecha = _fecha_iso(fecha_produccion)
    rows = [
        (
            carga_id,
            fecha,
            int(r["material"]),
            r.get("referencia"),
            float(r.get("necesidad_bandeja", 0) or 0),
            float(r.get("necesidad_unidades", 0) or 0),
            float(r.get("fisico_bandejas", 0) or 0),
            float(r.get("fisico_unidades", 0) or 0),
            float(r.get("producir_bandeja", 0) or 0),
            float(r.get("producir_unidades", 0) or 0),
            float(r.get("notificado", 0) or 0),
        )
        for _, r in df.iterrows()
    ]
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO pre_corte (
            carga_id, fecha_produccion, material, referencia,
            necesidad_bandeja, necesidad_unidades,
            fisico_bandejas, fisico_unidades,
            producir_bandeja, producir_unidades, notificado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount


def persist_flash_agregado(
    conn: sqlite3.Connection,
    carga_id: int,
    fecha_produccion: date,
    df: pd.DataFrame,
) -> int:
    """Persiste el agregado del flash para la fecha dada."""
    fecha = _fecha_iso(fecha_produccion)
    rows = [
        (
            carga_id,
            fecha,
            int(r["material"]),
            r.get("nomb_material"),
            float(r.get("cantidad_neta_total", 0) or 0),
            float(r.get("facturado_real_total", 0) or 0),
            int(r.get("num_facturas", 0) or 0),
        )
        for _, r in df.iterrows()
    ]
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO flash_agregado (
            carga_id, fecha_produccion, material, nomb_material_flash,
            cantidad_neta_total, facturado_real_total, num_facturas
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount


def persist_cruce(
    conn: sqlite3.Connection,
    pre_carga_id: int,
    flash_carga_id: int,
    match_result: "MatchResult",
) -> int:
    """Persiste filas del cruce (matched). Idempotente por (pre, flash, material)."""
    df = match_result.matched
    if df.empty:
        return 0
    fecha = _fecha_iso(match_result.fecha_produccion)
    rows = [
        (
            pre_carga_id,
            flash_carga_id,
            fecha,
            int(r["material"]),
            r.get("referencia"),
            r.get("nomb_material"),
            float(r.get("notificado_unidades", 0) or 0),
            float(r.get("producir_unidades", 0) or 0),
            float(r.get("real_unidades_flash", 0) or 0),
            float(r.get("delta_unidades", 0) or 0),
            None if pd.isna(r.get("cumplimiento_pct")) else float(r["cumplimiento_pct"]),
            1 if r.get("match_bool", False) else 0,
        )
        for _, r in df.iterrows()
    ]
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO cruce (
            pre_corte_carga_id, flash_carga_id, fecha_produccion, material,
            referencia, nomb_material_flash,
            notificado_unidades, producir_unidades, real_unidades_flash,
            delta_unidades, cumplimiento_pct, match_bool
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount


def persist_no_cruzados(
    conn: sqlite3.Connection,
    pre_carga_id: int,
    flash_carga_id: int,
    df: pd.DataFrame,
) -> int:
    if df.empty:
        return 0
    rows = [
        (
            pre_carga_id,
            flash_carga_id,
            _fecha_iso(r["fecha_produccion"]) if not isinstance(r["fecha_produccion"], str) else r["fecha_produccion"],
            r["origen"],
            int(r["material"]),
            r.get("referencia_o_nombre"),
            float(r.get("valor", 0) or 0),
            r.get("motivo"),
        )
        for _, r in df.iterrows()
    ]
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO no_cruzados (
            pre_corte_carga_id, flash_carga_id, fecha_produccion, origen,
            material, referencia_o_nombre, valor, motivo
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount


def persist_run(
    conn: sqlite3.Connection,
    *,
    pre_corte_meta: dict[str, Any],
    pre_corte_df: pd.DataFrame,
    flash_meta: dict[str, Any],
    flash_agregado_df: pd.DataFrame,
    match_result: "MatchResult",
    fecha_archivo: date,
) -> dict[str, Any]:
    """Transaccion atomica: registra ambas cargas y persiste cruce + no_cruzados.

    Rollback total si algo falla. Idempotente: reprocesar el mismo par
    (pre_hash, flash_hash) no duplica filas gracias a los UNIQUE del schema.
    """
    fecha_prod = match_result.fecha_produccion
    try:
        pre_carga_id, pre_es_nueva = get_or_insert_carga(
            conn, pre_corte_meta, fecha_archivo=fecha_archivo, fecha_produccion=fecha_prod
        )
        flash_carga_id, flash_es_nueva = get_or_insert_carga(conn, flash_meta)

        pre_ins = persist_pre_corte(conn, pre_carga_id, fecha_prod, pre_corte_df) if pre_es_nueva else 0
        flash_ins = persist_flash_agregado(conn, flash_carga_id, fecha_prod, flash_agregado_df)
        cruce_ins = persist_cruce(conn, pre_carga_id, flash_carga_id, match_result)
        no_cruz_ins = persist_no_cruzados(
            conn, pre_carga_id, flash_carga_id, match_result.no_cruzados
        )
        conn.commit()
        return {
            "pre_carga_id": pre_carga_id,
            "pre_es_nueva": pre_es_nueva,
            "flash_carga_id": flash_carga_id,
            "flash_es_nueva": flash_es_nueva,
            "pre_corte_filas_insertadas": pre_ins,
            "flash_agregado_filas_insertadas": flash_ins,
            "cruce_filas_insertadas": cruce_ins,
            "no_cruzados_filas_insertadas": no_cruz_ins,
            "fecha_produccion": fecha_prod.isoformat(),
        }
    except Exception:
        conn.rollback()
        raise


def create_run(
    conn: sqlite3.Connection,
    *,
    parent_run_id: str | None = None,
    owner_user_id: int | None = None,
    pre_corte_carga_id: int | None = None,
    flash_carga_id: int | None = None,
    fecha_produccion: date | None = None,
    status: str = "running",
    current_step: str = "created",
    notes: str | None = None,
) -> str:
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO runs (
            id, parent_run_id, owner_user_id, status, current_step,
            pre_corte_carga_id, flash_carga_id, fecha_produccion, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            parent_run_id,
            owner_user_id,
            status,
            current_step,
            pre_corte_carga_id,
            flash_carga_id,
            _fecha_iso(fecha_produccion),
            notes,
        ),
    )
    conn.commit()
    return run_id


def update_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str | None = None,
    current_step: str | None = None,
    summary: dict[str, Any] | None = None,
    notes: str | None = None,
    ended: bool = False,
) -> None:
    fields = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if current_step is not None:
        fields.append("current_step = ?")
        values.append(current_step)
    if summary is not None:
        fields.append("summary_json = ?")
        values.append(json.dumps(summary, default=str))
    if notes is not None:
        fields.append("notes = ?")
        values.append(notes)
    if ended:
        fields.append("ended_at = ?")
        values.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if not fields:
        return
    values.append(run_id)
    conn.execute(
        f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", tuple(values)
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("summary_json"):
        d["summary"] = json.loads(d["summary_json"])
    return d


def list_recent_runs(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, c.filename AS pre_corte_filename
        FROM runs r
        LEFT JOIN cargas c ON r.pre_corte_carga_id = c.id
        ORDER BY r.started_at DESC, r.rowid DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_cargas(
    conn: sqlite3.Connection, tipo: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    if tipo:
        rows = conn.execute(
            "SELECT * FROM cargas WHERE tipo = ? ORDER BY cargado_en DESC LIMIT ?",
            (tipo, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cargas ORDER BY cargado_en DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Utilidades SQLite del historico.")
    parser.add_argument("--init", action="store_true", help="Crear tablas si no existen")
    parser.add_argument("--db", default=str(DB_PATH), help="Ruta al SQLite")
    parser.add_argument(
        "--backfill-pre-corte", nargs="+", metavar="PRE_CORTE",
        help="Rutas de archivos PRE CORTE a procesar",
    )
    parser.add_argument(
        "--flash", help="Ruta del FLASH mensual usado en el backfill",
    )
    parser.add_argument("--list-cargas", action="store_true")
    parser.add_argument("--list-runs", action="store_true")
    args = parser.parse_args(argv)

    if args.init:
        p = init_db(args.db)
        print(f"DB inicializada en {p}")

    if args.backfill_pre_corte:
        if not args.flash:
            parser.error("--backfill-pre-corte requiere --flash")
        from app.core.aggregator import aggregate_flash
        from app.core.date_extractor import extract_file_date, extract_production_date
        from app.core.loaders import load_flash, load_pre_corte
        from app.core.matcher import match_by_material

        init_db(args.db)
        flash_df, flash_meta = load_flash(args.flash)
        print(f"FLASH cargado: {flash_meta['filename']} ({flash_meta['num_filas_original']} filas)")
        with get_conn(args.db) as conn:
            for pre_path in args.backfill_pre_corte:
                pre_df, pre_meta = load_pre_corte(pre_path)
                fecha_arch = extract_file_date(pre_path)
                fecha_prod = extract_production_date(pre_path)
                agg = aggregate_flash(flash_df, fecha_prod)
                result = match_by_material(pre_df, agg, fecha_prod)
                summary = persist_run(
                    conn,
                    pre_corte_meta=pre_meta,
                    pre_corte_df=pre_df,
                    flash_meta=flash_meta,
                    flash_agregado_df=agg,
                    match_result=result,
                    fecha_archivo=fecha_arch,
                )
                skip_tag = "" if summary["pre_es_nueva"] else " [YA EXISTIA, no reinserto pre_corte]"
                print(
                    f"  {pre_meta['filename']} -> fecha_prod={fecha_prod} "
                    f"cruce+{summary['cruce_filas_insertadas']} "
                    f"no_cruzados+{summary['no_cruzados_filas_insertadas']}{skip_tag}"
                )

    if args.list_cargas:
        with get_conn(args.db) as conn:
            for c in list_cargas(conn):
                print(f"  [{c['tipo']:9s}] id={c['id']:3d} {c['filename']} "
                      f"hash={c['hash_sha256'][:12]}... filas={c['num_filas_original']} "
                      f"fecha_prod={c['fecha_produccion']}")

    if args.list_runs:
        with get_conn(args.db) as conn:
            for r in list_recent_runs(conn):
                print(f"  run={r['id'][:8]} status={r['status']:20s} "
                      f"step={r.get('current_step','')} "
                      f"pre={r.get('pre_corte_filename','')}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
