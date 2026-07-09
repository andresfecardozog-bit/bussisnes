# Deploy en Railway (Fase 7B)

Guia paso a paso para deployar la API en Railway con volumen persistente
+ Supabase Storage opcional.

## Prerequisitos

- Cuenta en [Railway.com](https://railway.com) (plan Hobby, 5 USD/mes).
- Repo en GitHub (Railway se conecta via GitHub App).
- Proyecto Supabase (opcional, para Fase 7E): [supabase.com](https://supabase.com).
- Docker instalado localmente **solo si quieres smoke test**; para deploy
  no es necesario — Railway builda el `Dockerfile` en su infraestructura.

## Estructura de archivos relevante

- [Dockerfile](../Dockerfile) — multi-stage, non-root, python:3.14-slim, healthcheck `/health`.
- [.dockerignore](../.dockerignore) — excluye `tests/`, `data/`, `venv/`, `.git/`, `.env`.
- [railway.json](../railway.json) — config de build + healthcheck + restart policy.
- [requirements.txt](../requirements.txt) — deps runtime (Pillow, supabase, fastapi, uvicorn...).
- [.env.example](../.env.example) — template. En Railway se llenan desde la UI.

## Paso 1: crear proyecto en Railway

1. Login en [railway.com](https://railway.com) con GitHub.
2. `New Project` -> `Deploy from GitHub repo` -> selecciona el repo.
3. Railway detecta `Dockerfile` y `railway.json` automaticamente.

## Paso 2: configurar volumen persistente

**Critico**: SQLite + uploads viven en `/data`. Sin volumen, cada deploy
borra el historico.

**UI actualizada (Railway 2025)** — la opcion cambio de lugar:

**Opcion A - via Command Palette (rapida)**:

1. Abre el servicio (click en su card).
2. Presiona `Ctrl+K` (o `Cmd+K` en Mac) -> escribe `create volume`.
3. Selecciona `Create Volume`.
4. Mount path: `/data`.
5. Se crea con 1 GB por default (Free plan: 500 MB max; Hobby $5: 5 GB).

**Opcion B - via UI del servicio**:

1. Abre el servicio.
2. Al lado del boton **Deployments / Metrics / Settings**, busca el
   tab llamado **Data** o **Storage** (nombre varia por version).
3. `+ Add Volume` (o `+ New Volume`).
4. Mount path: `/data`.

**Opcion C - via el proyecto (workaround si el servicio no la tiene)**:

1. En la vista del **proyecto** (no del servicio), click en `+ Create`
   arriba a la derecha.
2. Selecciona `Volume`.
3. Asocia al servicio y define mount path `/data`.

Despues de crear el volumen, Railway hace re-deploy automatico. Verifica
en los logs que aparezca `chown -R nutri:nutri /data` (del entrypoint) sin
errores.

**Nota importante**: aunque el volumen se cree como root:root en el mount,
el `docker-entrypoint.sh` de este proyecto lo arregla en runtime haciendo
`chown` antes de arrancar uvicorn. Por eso el container corre como root
al inicio y luego drop-de-privilegios a `nutri` para el proceso Python.

## Paso 3: variables de entorno

En `Settings` -> `Variables` agregar:

```
NUTRI_DATA_DIR=/data
NUTRI_LOGS_DIR=/data/logs
NUTRI_API_HOST=0.0.0.0
NUTRI_CORS_ORIGINS=https://cumplimientoplataforma.vercel.app,https://cumplimiento-plataforma-*.vercel.app,https://cumplimientoplataforma-*.vercel.app
```

`PORT` la inyecta Railway automaticamente; no la definas manualmente.

Si vas a usar Supabase Storage (Fase 7E):

```
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...   # la SECRET, no la publishable
SUPABASE_BUCKET_UPLOADS=uploads
SUPABASE_BUCKET_OUTPUTS=outputs
```

Sin estas, la app usa `LocalStorage` sobre el volumen `/data`. Los tests
`test_storage_backend_local_por_default` y `test_get_storage_devuelve_local_por_default`
verifican este fallback.

## Paso 4: deploy y verificacion

Al hacer push a `main`:

1. Railway builda el `Dockerfile` (~2-4 min primera vez, ~30-60 s subsiguientes).
2. Corre `HEALTHCHECK` cada 30 s contra `/health`.
3. Si `/health` responde 200, el deploy se marca `Success`.

Verifica en logs:

```
INFO:     Uvicorn running on http://0.0.0.0:$PORT (Press CTRL+C to quit)
INFO:     Application startup complete.
```

Genera un dominio publico: `Settings` -> `Networking` -> `Generate Domain`.
Prueba:

```
curl https://<tu-servicio>.up.railway.app/health
```

Debe responder `{"status":"ok","version":"...","now":"..."}`.

Documentacion Swagger: `https://<tu-servicio>.up.railway.app/docs`.

## Paso 5: setup Supabase Storage (Fase 7E)

Cuando tengas la SECRET KEY:

1. Ponla en `SUPABASE_SERVICE_KEY` (Railway + `.env` local).
2. Corre `python scripts/setup_supabase.py` -> crea buckets `uploads` y
   `outputs` como privados.
3. Restart del servicio en Railway. El `StorageAdapter` detecta las env
   vars y usa `SupabaseStorage` automaticamente.

## Smoke build local (opcional)

Requiere Docker Desktop corriendo:

```powershell
docker build -t nutriavicola-api:local .
docker run --rm -p 8000:8000 -v ${PWD}/data-local:/data nutriavicola-api:local
```

Luego `curl http://127.0.0.1:8000/health`.

Para test con Supabase real:

```powershell
docker run --rm -p 8000:8000 -v ${PWD}/data-local:/data --env-file .env nutriavicola-api:local
```

## Rotacion de credenciales

Cuando cierres el proyecto:

1. Supabase -> Settings -> API -> **Rotate** publishable + secret keys.
2. Supabase -> Settings -> Database -> **Reset** password.
3. Railway -> Variables -> actualiza los valores nuevos.
4. Borra el `.env` local.

## Troubleshooting

### Healthcheck falla en el primer deploy

Sintoma en Deploy Logs:
```
Attempt #1 failed with service unavailable. Continuing to retry for 19s
Attempt #2 failed with service unavailable. Continuing to retry for 8s
1/1 replicas never became healthy!
```

Causas comunes:

1. **Cold start > 30s**: Python 3.14 + pandas + openpyxl + Pillow +
   supabase-py tarda ~30-45s en importar en el primer arranque.
   `railway.json` ya tiene `healthcheckTimeout: 300` (5 min buffer).
2. **Falta libjpeg/zlib para Pillow**: el runtime stage ahora instala
   `libjpeg62-turbo, zlib1g, libopenjp2-7, libtiff6`. Sin ellas, el
   `import PIL` en Python falla al hacer `import _imaging`.
3. **Permisos del volumen**: si `/data` es root:root y el user es `nutri`,
   `init_db()` falla con PermissionError. El `docker-entrypoint.sh` hace
   `chown -R nutri:nutri /data` antes de arrancar uvicorn.
4. **`PORT` no llega al proceso**: verifica en Deploy Logs que aparezca
   `Uvicorn running on http://0.0.0.0:<puerto>`. Si dice `port 8000` en
   vez del puerto real de Railway, hay un problema con `$PORT`.

**Como diagnosticar**:

1. Railway UI -> tu servicio -> `Deployments` -> click en el deploy
   fallido -> `View Logs` (o `Deploy Logs`).
2. Busca cualquiera de estos indicadores:
   - `ModuleNotFoundError` -> falta una dep en `requirements.txt`.
   - `Permission denied: '/data/...'` -> volumen sin permisos, revisa
     que el entrypoint corrio.
   - `Address already in use` -> conflicto de puertos.
   - Ausencia de linea `Uvicorn running on ...` -> no arranco uvicorn.
3. Si no hay errores obvios pero tarda mucho: aumenta
   `healthcheckTimeout` a `600` en `railway.json` y re-deploy.

### Build falla por wheel de Pillow / numpy

La imagen usa Python 3.14 y algunos wheels pueden no existir aun. El
builder tiene `build-essential`; compila desde source si es necesario
(tarda mas, pero funciona).

### Supabase 403 "row-level security policy"

Estas usando la publishable key (`sb_publishable_...`) en vez de la
secret (`sb_secret_...`). Cambia el valor de `SUPABASE_SERVICE_KEY` en
Railway env vars.

### SQLite lock

SQLite serializa writes. `numReplicas: 1` en `railway.json` evita
concurrencia. Si necesitas escalar, migra a Postgres (Fase 7F opcional).

### El volumen no se ve en Settings

Railway rediseno la UI. Ver Paso 2 arriba: usar Command Palette
(`Ctrl+K` -> `create volume`) o el tab `Storage`/`Data` del servicio.
