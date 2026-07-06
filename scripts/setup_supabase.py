"""Crea los buckets Supabase que la app necesita (Fase 7E).

Se corre UNA vez cuando arrancas un proyecto Supabase nuevo. Requiere:

- `SUPABASE_URL` (env o .env)
- `SUPABASE_SERVICE_KEY` con la SECRET key (no la publishable). Con la
  publishable no se pueden crear buckets desde el server.

Uso:
    venv\\Scripts\\python.exe scripts\\setup_supabase.py

Idempotente: si un bucket ya existe, no hace nada.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import (
    SUPABASE_BUCKET_OUTPUTS,
    SUPABASE_BUCKET_UPLOADS,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
)


def main() -> int:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("ERROR: SUPABASE_URL y SUPABASE_SERVICE_KEY deben estar definidas.")
        print("Copia .env.example a .env y llenalas, o exporta en el shell.")
        return 2
    try:
        from supabase import create_client  # type: ignore[import-not-found]
    except ImportError:
        print("ERROR: pip install supabase")
        return 2

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    buckets_deseados = [
        (SUPABASE_BUCKET_UPLOADS, {"public": False}),
        (SUPABASE_BUCKET_OUTPUTS, {"public": False}),
    ]

    try:
        buckets_raw = client.storage.list_buckets()
        # supabase-py >=2 retorna objetos SyncBucket; <2 devolvia dicts.
        existentes = set()
        for b in buckets_raw:
            name = getattr(b, "name", None) or (b.get("name") if isinstance(b, dict) else None)
            if name:
                existentes.add(name)
    except Exception as exc:
        print(f"ERROR listando buckets (probablemente la key es publishable): {exc}")
        return 3

    for name, opts in buckets_deseados:
        if name in existentes:
            print(f"  [OK] bucket '{name}' ya existe")
            continue
        try:
            client.storage.create_bucket(name, options=opts)
            print(f"  [CREADO] bucket '{name}' (privado)")
        except Exception as exc:
            print(f"  [FAIL] bucket '{name}': {exc}")

    print(f"\nSupabase URL: {SUPABASE_URL}")
    print(f"Buckets: {[n for n, _ in buckets_deseados]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
