"""Persistencia SQLite de la plataforma generica.

Tablas nuevas (conviven con el schema legado en el mismo historico.sqlite):

    profiles               - MatchProfiles versionados (JSON + estado)
    profile_runs           - una ejecucion de un profile sobre un par de archivos
    cruce_generico         - filas matched de cada run (columnas dinamicas en JSON)
    no_cruzados_generico   - filas sin cruce con origen y motivo
    profile_knowledge      - memoria por proceso: brief, Q&A, correcciones (Fase 2 la puebla)
    profile_questions      - cola de preguntas de los agentes (Fase 2 la puebla)

Idempotencia de runs: UNIQUE(profile_id, profile_version, left_hash,
right_hash, params_json). Re-ejecutar el mismo cruce reemplaza el run
anterior (delete + insert transaccional), nunca duplica.

Las filas matched se guardan con sus columnas serializadas a JSON por fila:
los profiles tienen columnas distintas entre si y un schema EAV o una tabla
por profile serian peores para consultar/exportar. Los renderers (Fase 4)
leen el JSON y lo expanden a DataFrame.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.config import ADMIN_EMAIL
from app.core.db import get_conn, init_db  # noqa: F401 (re-export conveniente)
from app.platform.engine import GenericMatchResult
from app.platform.profile import MatchProfile

_PLATFORM_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        owner_user_id INTEGER REFERENCES users(id),
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'proposed', 'approved', 'archived')),
        json TEXT NOT NULL,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        aprobado_en TIMESTAMP,
        aprobado_por TEXT,
        UNIQUE(profile_id, version)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT NOT NULL,
        profile_version INTEGER NOT NULL,
        owner_user_id INTEGER REFERENCES users(id),
        left_hash TEXT NOT NULL,
        right_hash TEXT NOT NULL,
        left_filename TEXT,
        right_filename TEXT,
        params_json TEXT NOT NULL DEFAULT '{}',
        kpis_json TEXT NOT NULL DEFAULT '{}',
        summary_json TEXT NOT NULL DEFAULT '{}',
        ejecutado_en TIMESTAMP NOT NULL,
        UNIQUE(profile_id, profile_version, left_hash, right_hash, params_json)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS cruce_generico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES profile_runs(id) ON DELETE CASCADE,
        row_json TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS no_cruzados_generico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES profile_runs(id) ON DELETE CASCADE,
        origen TEXT NOT NULL,
        key TEXT,
        motivo TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS breakdown_generico (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES profile_runs(id) ON DELETE CASCADE,
        breakdown_id TEXT NOT NULL,
        row_json TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_knowledge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('brief', 'qa', 'correccion', 'nota')),
        autor TEXT NOT NULL,
        contenido TEXT NOT NULL,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT NOT NULL,
        agente TEXT NOT NULL,
        sobre TEXT,
        pregunta TEXT NOT NULL,
        hipotesis TEXT,
        impacto TEXT,
        bloqueante INTEGER NOT NULL DEFAULT 0,
        estado TEXT NOT NULL DEFAULT 'abierta'
            CHECK (estado IN ('abierta', 'respondida', 'asumida')),
        respuesta TEXT,
        creado_en TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        respondido_en TIMESTAMP
    );
    """,
]


def init_platform_schema(conn: sqlite3.Connection) -> None:
    for stmt in _PLATFORM_SCHEMA:
        conn.execute(stmt)
    _ensure_column(conn, "profiles", "owner_user_id INTEGER REFERENCES users(id)")
    _ensure_column(conn, "profile_runs", "owner_user_id INTEGER REFERENCES users(id)")
    admin_row = conn.execute(
        "SELECT id FROM users WHERE lower(email) = ?",
        (ADMIN_EMAIL.lower(),),
    ).fetchone()
    if admin_row:
        admin_id = int(admin_row["id"])
        conn.execute(
            "UPDATE profiles SET owner_user_id = ? WHERE owner_user_id IS NULL",
            (admin_id,),
        )
        conn.execute(
            "UPDATE profile_runs SET owner_user_id = ? WHERE owner_user_id IS NULL",
            (admin_id,),
        )
    conn.commit()


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in rows)


def _ensure_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    name = column_def.split()[0]
    if _column_exists(conn, table, name):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def save_profile(
    conn: sqlite3.Connection,
    profile: MatchProfile,
    status: str = "draft",
    owner_user_id: int | None = None,
) -> int:
    init_platform_schema(conn)
    owner = owner_user_id
    if owner is None:
        row = conn.execute(
            """
            SELECT owner_user_id FROM profiles
            WHERE profile_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (profile.profile_id,),
        ).fetchone()
        if row and row["owner_user_id"] is not None:
            owner = int(row["owner_user_id"])
    cur = conn.execute(
        """
        INSERT INTO profiles (profile_id, version, owner_user_id, status, json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(profile_id, version)
        DO UPDATE SET
            json = excluded.json,
            status = excluded.status,
            owner_user_id = COALESCE(profiles.owner_user_id, excluded.owner_user_id)
        """,
        (profile.profile_id, profile.version, owner, status, profile.to_json(indent=0)),
    )
    conn.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute(
        "SELECT id FROM profiles WHERE profile_id = ? AND version = ?",
        (profile.profile_id, profile.version),
    ).fetchone()
    return int(row["id"])


def load_profile(
    conn: sqlite3.Connection, profile_id: str, version: int | None = None
) -> MatchProfile:
    init_platform_schema(conn)
    if version is None:
        row = conn.execute(
            "SELECT json FROM profiles WHERE profile_id = ? ORDER BY version DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT json FROM profiles WHERE profile_id = ? AND version = ?",
            (profile_id, version),
        ).fetchone()
    if row is None:
        raise KeyError(f"Profile no encontrado: {profile_id} v{version}")
    return MatchProfile.from_json(row["json"])


def approve_profile(
    conn: sqlite3.Connection, profile_id: str, version: int, aprobado_por: str
) -> None:
    init_platform_schema(conn)
    cur = conn.execute(
        """
        UPDATE profiles SET status = 'approved', aprobado_en = ?, aprobado_por = ?
        WHERE profile_id = ? AND version = ?
        """,
        (_now(), aprobado_por, profile_id, version),
    )
    if cur.rowcount == 0:
        raise KeyError(f"Profile no encontrado: {profile_id} v{version}")
    conn.commit()


def list_profiles(
    conn: sqlite3.Connection,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    init_platform_schema(conn)
    if owner_user_id is None:
        rows = conn.execute(
            """
            SELECT profile_id, version, owner_user_id, status, creado_en, aprobado_en, aprobado_por
            FROM profiles ORDER BY profile_id, version DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT profile_id, version, owner_user_id, status, creado_en, aprobado_en, aprobado_por
            FROM profiles
            WHERE owner_user_id = ?
            ORDER BY profile_id, version DESC
            """,
            (owner_user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def _params_canonical(parameters: dict[str, Any]) -> str:
    return json.dumps(
        {k: str(v) for k, v in sorted(parameters.items())}, ensure_ascii=False
    )


def persist_run(
    conn: sqlite3.Connection, result: GenericMatchResult
) -> dict[str, Any]:
    """Persiste un run de forma idempotente. Si el mismo cruce (profile +
    version + hashes + parametros) ya existia, lo reemplaza completo."""
    init_platform_schema(conn)
    left_hash = result.left_meta.get("hash_sha256", "")
    right_hash = result.right_meta.get("hash_sha256", "")
    params_json = _params_canonical(result.parameters)

    existing = conn.execute(
        """
        SELECT id FROM profile_runs
        WHERE profile_id = ? AND profile_version = ?
          AND left_hash = ? AND right_hash = ? AND params_json = ?
        """,
        (result.profile_id, result.profile_version, left_hash, right_hash, params_json),
    ).fetchone()

    reemplazado = existing is not None
    if reemplazado:
        conn.execute("DELETE FROM profile_runs WHERE id = ?", (existing["id"],))

    owner_row = conn.execute(
        """
        SELECT owner_user_id
        FROM profiles
        WHERE profile_id = ? AND version = ?
        """,
        (result.profile_id, result.profile_version),
    ).fetchone()
    owner_user_id = int(owner_row["owner_user_id"]) if owner_row and owner_row["owner_user_id"] is not None else None

    cur = conn.execute(
        """
        INSERT INTO profile_runs (
            profile_id, profile_version, owner_user_id, left_hash, right_hash,
            left_filename, right_filename, params_json, kpis_json,
            summary_json, ejecutado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.profile_id,
            result.profile_version,
            owner_user_id,
            left_hash,
            right_hash,
            result.left_meta.get("filename"),
            result.right_meta.get("filename"),
            params_json,
            json.dumps(result.kpis, ensure_ascii=False, default=str),
            json.dumps(result.summary(), ensure_ascii=False),
            _now(),
        ),
    )
    run_id = int(cur.lastrowid)

    matched_records = result.matched.to_dict(orient="records")
    conn.executemany(
        "INSERT INTO cruce_generico (run_id, row_json) VALUES (?, ?)",
        [
            (run_id, json.dumps(rec, ensure_ascii=False, default=str))
            for rec in matched_records
        ],
    )
    conn.executemany(
        """
        INSERT INTO no_cruzados_generico (run_id, origen, key, motivo)
        VALUES (?, ?, ?, ?)
        """,
        [
            (run_id, rec["origen"], rec["key"], rec["motivo"])
            for rec in result.no_cruzados.to_dict(orient="records")
        ],
    )
    for bd_id, bd_df in result.breakdowns.items():
        conn.executemany(
            "INSERT INTO breakdown_generico (run_id, breakdown_id, row_json) VALUES (?, ?, ?)",
            [
                (run_id, bd_id, json.dumps(rec, ensure_ascii=False, default=str))
                for rec in bd_df.to_dict(orient="records")
            ],
        )
    conn.commit()
    return {
        "run_id": run_id,
        "reemplazado": reemplazado,
        "filas_cruce": len(matched_records),
        "filas_no_cruzados": len(result.no_cruzados),
    }


def load_run_matched(conn: sqlite3.Connection, run_id: int) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT row_json FROM cruce_generico WHERE run_id = ? ORDER BY id", (run_id,)
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(r["row_json"]) for r in rows])


def load_run_breakdown(
    conn: sqlite3.Connection, run_id: int, breakdown_id: str
) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT row_json FROM breakdown_generico
        WHERE run_id = ? AND breakdown_id = ? ORDER BY id
        """,
        (run_id, breakdown_id),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([json.loads(r["row_json"]) for r in rows])


def list_runs(
    conn: sqlite3.Connection,
    profile_id: str | None = None,
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    init_platform_schema(conn)
    if profile_id and owner_user_id is not None:
        rows = conn.execute(
            """
            SELECT * FROM profile_runs
            WHERE profile_id = ? AND owner_user_id = ?
            ORDER BY ejecutado_en DESC
            """,
            (profile_id, owner_user_id),
        ).fetchall()
    elif profile_id:
        rows = conn.execute(
            "SELECT * FROM profile_runs WHERE profile_id = ? ORDER BY ejecutado_en DESC",
            (profile_id,),
        ).fetchall()
    elif owner_user_id is not None:
        rows = conn.execute(
            "SELECT * FROM profile_runs WHERE owner_user_id = ? ORDER BY ejecutado_en DESC",
            (owner_user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM profile_runs ORDER BY ejecutado_en DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kpis"] = json.loads(d.pop("kpis_json"))
        d["summary"] = json.loads(d.pop("summary_json"))
        d["params"] = json.loads(d.pop("params_json"))
        out.append(d)
    return out
