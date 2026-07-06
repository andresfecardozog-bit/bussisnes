# syntax=docker/dockerfile:1.6
# ============================================================================
# Fase 7B - Dockerfile para deploy en Railway
#
# Multi-stage:
# - builder: instala dependencias (con toolchain de compilacion cuando aplica).
# - runtime: imagen final minima, non-root, con healthcheck.
#
# El runtime espera un volumen persistente montado en /data (donde vive
# `historico.sqlite`, `uploads/`, `onedrive_export/`). Railway lo configura
# desde la UI (Service -> Volumes -> Mount path `/data`).
# ============================================================================

# ---------------------------- BUILDER STAGE --------------------------------
FROM python:3.14-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Toolchain necesario si alguna wheel binaria no esta disponible para 3.14.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt


# ---------------------------- RUNTIME STAGE --------------------------------
FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUTRI_DATA_DIR=/data \
    NUTRI_LOGS_DIR=/data/logs \
    NUTRI_API_HOST=0.0.0.0 \
    PATH=/home/nutri/.local/bin:$PATH

# Libs de sistema para runtime (Pillow requiere libjpeg/zlib para render).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
        libopenjp2-7 \
        libtiff6 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r nutri && useradd -r -g nutri -m -d /home/nutri nutri

# Copia las deps instaladas por el builder (pip --user en /root/.local)
COPY --from=builder --chown=nutri:nutri /root/.local /home/nutri/.local

WORKDIR /app

# Solo el codigo runtime + recursos indispensables.
COPY --chown=nutri:nutri app ./app
COPY --chown=nutri:nutri resources ./resources
COPY --chown=nutri:nutri scripts ./scripts

# Entrypoint: se ejecuta como root para arreglar permisos del volumen
# (Railway monta /data como root:root aunque el usuario del container sea
# nutri). El script hace chown y despues drop de privilegios via su-exec.
COPY --chown=root:root docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /data /data/logs && chown -R nutri:nutri /data

# NO cambiamos a USER nutri aca: el entrypoint hace su drop de privilegios.
EXPOSE 8000

# Healthcheck usa el endpoint /health (existe desde Fase 4). start-period
# amplio (60s) para que pandas + supabase-py + openpyxl terminen de cargar
# en cold start.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request,sys; \
        r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); \
        sys.exit(0 if r.status==200 else 1)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]

# Railway inyecta $PORT; local usa 8000 por default.
CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
