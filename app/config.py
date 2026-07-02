"""Configuracion central: nombres de columnas, formatos, rutas.

Todas las constantes viven aqui para que un cambio en el formato de los
archivos SAP no obligue a tocar la logica.

**Env vars soportadas** (Fase 7B, para deploy en Railway):

- `NUTRI_DATA_DIR`: directorio raiz para SQLite + uploads + outputs.
    Default: `<repo>/data` (dev local). En Railway apunta al volumen `/data`.
- `NUTRI_API_HOST`, `NUTRI_API_PORT`: bind del uvicorn.
    Default: `127.0.0.1:8000` (local). En Railway se usa `0.0.0.0:$PORT`.
- `NUTRI_CORS_ORIGINS`: lista separada por coma de origenes permitidos.
    Default: localhost dev. En prod se agrega el dominio de Vercel.
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_BUCKET_UPLOADS`,
    `SUPABASE_BUCKET_OUTPUTS`: cuando esten definidas, el `StorageAdapter`
    usa Supabase Storage (Fase 7E). Sin ellas, usa disco local.
- `GEMINI_API_KEY`: opcional, reservada para fallback futuro (nombres raros).
    NO usada por el core; solo la lee `app.core.gemini_fallback` si existiera.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv_if_present() -> None:
    """Carga .env local sin dep externa. Usa `setdefault` para no pisar env
    vars reales del sistema (importante en Railway donde vienen del entorno).

    En pytest se salta: `PYTEST_CURRENT_TEST` la marca como test session, y
    no queremos que los valores reales del `.env` filtren a tests que
    monkeypatchean env vars especificas.
    """
    if "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ:
        return
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and val:
                os.environ.setdefault(key, val)
    except OSError:
        pass


_load_dotenv_if_present()

# ---------------------------------------------------------------------------
# Directorios (configurables por env var para deploy en volumen persistente)
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("NUTRI_DATA_DIR", str(PROJECT_ROOT / "data")))
RUNS_DIR = DATA_DIR / "runs"
UPLOADS_DIR = DATA_DIR / "uploads"
LOGS_DIR = Path(os.environ.get("NUTRI_LOGS_DIR", str(PROJECT_ROOT / "logs")))
DB_PATH = DATA_DIR / "historico.sqlite"
ONEDRIVE_EXPORT_DIR = DATA_DIR / "onedrive_export"

for _p in (DATA_DIR, RUNS_DIR, UPLOADS_DIR, LOGS_DIR, ONEDRIVE_EXPORT_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# API bind
# ---------------------------------------------------------------------------
API_HOST = os.environ.get("NUTRI_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("PORT", os.environ.get("NUTRI_API_PORT", "8000")))


def _parse_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


API_CORS_ORIGINS = _parse_csv_env(
    "NUTRI_CORS_ORIGINS",
    [
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://localhost:4200",  # Angular dev server (Fase 7C)
        "http://127.0.0.1:4200",
    ],
)

# ---------------------------------------------------------------------------
# Supabase (opcional, Fase 7E). Si no estan definidas -> LocalStorage.
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL") or None
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or None
SUPABASE_BUCKET_UPLOADS = os.environ.get("SUPABASE_BUCKET_UPLOADS", "uploads")
SUPABASE_BUCKET_OUTPUTS = os.environ.get("SUPABASE_BUCKET_OUTPUTS", "outputs")


def storage_backend_activo() -> str:
    """Retorna 'supabase' si las env vars estan, si no 'local'."""
    if SUPABASE_URL and SUPABASE_SERVICE_KEY:
        return "supabase"
    return "local"


# ---------------------------------------------------------------------------
# Regex + columnas (invariantes del formato SAP, no configurables por env)
# ---------------------------------------------------------------------------
FILENAME_PRE_CORTE_REGEX = r"PRE\s*CORTE\s*(\d{2})[.\-_](\d{2})[.\-_](\d{4})"
DATE_FORMAT_FLASH = "%d/%m/%Y"

PRE_CORTE_COLUMNS = {
    "MATERIAL": "material",
    "REFERENCIA": "referencia",
    "NECESIDAD BANDEJA": "necesidad_bandeja",
    "NECESIDAD UNIDADES": "necesidad_unidades",
    "FISICO BANDEJAS": "fisico_bandejas",
    "FISICO UNIDADES": "fisico_unidades",
    "PRODUCIR BANDEJAS": "producir_bandeja",
    "PRODUCIR UNIDADES": "producir_unidades",
    "NOTIFICADO": "notificado",
}

PRE_CORTE_KEY = "material"
PRE_CORTE_NUMERIC_COLUMNS = [
    "necesidad_bandeja",
    "necesidad_unidades",
    "fisico_bandejas",
    "fisico_unidades",
    "producir_bandeja",
    "producir_unidades",
    "notificado",
]

FLASH_COLUMNS = {
    "Factura": "factura",
    "Posicion": "posicion",
    "Cantidad Neta": "cantidad_neta",
    "Fecha de factura": "fecha_factura",
    "Facturado Real": "facturado_real",
    "Material": "material",
    "Nomb Material": "nomb_material",
    "Centro": "centro",
    "Almacen": "almacen",
    "Cl factura": "clase_factura",
    "Fact anulada": "factura_anulada",
    "Devolucion": "devolucion",
}

FLASH_KEY_MATERIAL = "material"
FLASH_DATE_COLUMN = "fecha_factura"
FLASH_NUMERIC_COLUMNS = ["cantidad_neta", "facturado_real"]

CUMPLIMIENTO_SEMAFORO = {
    "verde_min": 95.0,
    "verde_max": 105.0,
    "amarillo_min": 85.0,
    "amarillo_max": 115.0,
}

# Reservado para Fase futura (fallback opcional). NO se usa en el core.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or None
