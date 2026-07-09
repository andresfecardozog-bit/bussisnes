"""Telemetria obligatoria de llamadas LLM (rubrica.md seccion 1 y 2).

Cada llamada registra tokens, costo estimado, latencia y contexto. La
tabla alimenta el informe de presupuesto (Fase 6) y la metrica de acierto
del mapeo (diff propuesta original vs aprobada).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

# USD por millon de tokens, Gemini 2.5 Flash (verificar contra factura real
# en Fase 6; el informe final usa costos medidos, esto es solo el estimador).
_PRICE_INPUT_PER_M = 0.30
_PRICE_OUTPUT_PER_M = 2.50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT,
    agente TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    costo_usd_estimado REAL NOT NULL DEFAULT 0,
    latencia_ms INTEGER,
    ok INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    creado_en TIMESTAMP NOT NULL
);
"""


def init_telemetry_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    conn.commit()


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    return round(
        input_tokens / 1_000_000 * _PRICE_INPUT_PER_M
        + output_tokens / 1_000_000 * _PRICE_OUTPUT_PER_M,
        6,
    )


def record_llm_call(
    conn: sqlite3.Connection,
    *,
    profile_id: str | None,
    agente: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latencia_ms: int | None,
    ok: bool = True,
    error: str | None = None,
) -> None:
    init_telemetry_schema(conn)
    conn.execute(
        """
        INSERT INTO llm_telemetry (
            profile_id, agente, model, input_tokens, output_tokens,
            costo_usd_estimado, latencia_ms, ok, error, creado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            agente,
            model,
            input_tokens,
            output_tokens,
            estimate_cost_usd(input_tokens, output_tokens),
            latencia_ms,
            1 if ok else 0,
            error,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def telemetry_summary(
    conn: sqlite3.Connection, profile_id: str | None = None
) -> dict[str, Any]:
    init_telemetry_schema(conn)
    where = "WHERE profile_id = ?" if profile_id else ""
    params = (profile_id,) if profile_id else ()
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS llamadas,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(costo_usd_estimado), 0) AS costo_usd,
               COALESCE(AVG(latencia_ms), 0) AS latencia_media_ms
        FROM llm_telemetry {where}
        """,
        params,
    ).fetchone()
    return dict(row)
