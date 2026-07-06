# AGENTS.md - Memoria persistente del proyecto

Guia de contexto que todo agente (humano o AI) debe leer antes de trabajar
en este repositorio.

---

## system_role

Analista de inteligencia de negocio y datos, especializado en automatizacion
de procesos BI con agentes, flujos (n8n, Power Automate, Copilot Studio),
modelos de lenguaje y machine learning.

Contexto del rol: empresa avicola en Colombia. Existe un proceso diario en
las fabricas para notificar los huevos/bandejas que se produciran al dia
siguiente (PRE CORTE) y luego compararlo con la produccion real registrada
en el FLASH (facturas SAP). Este proceso era completamente manual: comparar
archivo por archivo, fecha por fecha. La mision es sustituirlo por un flujo
end-to-end confiable, auditable, operable por una persona no tecnica y cuyo
entregable final para negocio es un archivo Excel formateado (Fase 6) que el
equipo de BI usa como fuente para armar sus dashboards en Power BI.

Compromisos no negociables:

- **Cero perdida de datos**: cada fila se contabiliza; si no cruza, va a la
  tabla `no_cruzados` con motivo. Hash SHA256 del binario original.
- **Sin LLM en el core**: el matching es 100% deterministico por codigo SAP
  MATERIAL. Los LLM solo pueden interpretar preguntas del usuario en Copilot
  Studio, jamas calcular ni decidir KPIs.
- **Puntos de control humanos explicitos**: nada se persiste sin aprobacion
  visual de la persona operativa. KPIs marcados como "borrador" hasta
  validacion de negocio.

---

## Modo de operacion clarificado (2026-07-02)

**Escenario real:**

- El usuario tiene **N archivos PRE CORTE** (uno por dia habil de un mes,
  ejemplo: todo febrero 2026, ~20 archivos).
- El usuario tiene **UN solo archivo FLASH** que cubre todo el mes.
- El flujo debe permitir subir en batch los PRE CORTE + subir una sola vez
  el FLASH, y cruzar cada PRE CORTE con las filas del FLASH correspondientes
  a su fecha de produccion (fecha del nombre + 1 dia).

**Implicaciones tecnicas:**

- **Streamlit** debe aceptar `st.file_uploader(..., accept_multiple_files=True)`
  para los PRE CORTE, y un uploader unico para el FLASH.
- **FastAPI** expone endpoint `POST /files/upload-batch` que recibe multiples
  PRE CORTE y guarda cada uno con su hash; y `POST /files/upload-flash` que
  guarda el FLASH del mes.
- El **FLASH se carga una vez en memoria y se reutiliza** para todos los
  PRE CORTE del batch (o bien se persiste en SQLite y se consulta con
  `WHERE fecha_factura = ?`). En cualquier caso, no se re-parsea N veces.
- **Un run por PRE CORTE**: cada archivo produce su propio `run_id`, su
  propio cruce, sus propias filas en `cruce`. El orquestador Power Automate
  itera sobre la lista de PRE CORTE.
- **Idempotencia por par**: la unicidad se define por `(pre_corte_hash,
  flash_hash)`. Si se resube el mismo PRE CORTE con el mismo FLASH, no
  duplica el cruce (aunque si se sube un nuevo FLASH corregido, se puede
  reprocesar todo).
- **Vista mensual**: la salida natural es una tabla mensual (todos los dias
  del mes) que es exactamente lo que los directivos quieren ver.

---

## Convenciones tecnicas

- Python >= 3.11, `pandas>=2`, `openpyxl`, `fastapi`, `streamlit`, `pytest`.
- Todo el codigo en `app/`; tests en `tests/`; datos en `data/`; logs en
  `logs/`; documentacion en `docs/`.
- Sin emojis en codigo ni comentarios.
- Sin comentarios que narran obviedades; solo comentarios que explican
  intencion no obvia, trade-offs o restricciones.
- Nombres en español para dominio (referencia, notificado, cumplimiento_pct),
  ingles para infraestructura (`run_id`, `hash_sha256`, `load_pre_corte`).
- Cada modulo del core es una funcion pura, sin efectos secundarios; los
  efectos (I/O, DB) viven en `db.py` y `api/routes/`.

---

## Que evitar

- Usar LLM para el matching de categorias. El codigo SAP `MATERIAL` (con o
  sin padding de ceros) es la clave deterministica.
- Modificar formulas o umbrales de KPIs sin confirmacion explicita del
  negocio.
- Escribir en SQLite sin la aprobacion humana visual en Streamlit.
- Reprocesar el mismo par `(pre_corte_hash, flash_hash)` sin advertir al
  usuario.
- Refactor grandes al notebook `test.ipynb`: se mantiene como sandbox de
  exploracion; toda la logica productiva vive en `app/core/`.

---

## Estado de fases

- [x] **Fase 0** - Fix del bug de fecha en `test.ipynb` (2026-02-13 -> 14).
- [x] **Fase 1** - Core Python: `date_extractor`, `loaders`, `aggregator`, `matcher`.
- [x] **Fase 2** - Validadores anti-perdida + logging rotativo.
- [x] **Fase 3** - Historico SQLite acumulado con idempotencia (batch N vs 1).
- [x] **Fase 4** - Backend FastAPI con endpoints atomicos (2026-07-02).
- [x] **Fase 4.5** - Migracion PRE CORTE de NOTIFICACION a RESUMEN + catalogo
  SKU (2026-07-02).
- [ ] **Fase 5** - Streamlit UI + embed Copilot Studio.
- [x] **Fase 6** - Export a Excel formateado para el equipo de BI (KPI unico:
  cumplimiento %, en 3 niveles de granularidad) (2026-07-06).
- [x] **Fase 6.6** - Calendario laboral colombiano: `fecha_produccion` salta
  al proximo dia habil si cae en domingo o festivo (2026-07-06).
- [x] **Fase 6.5** - Excel multi-fecha: agrupacion visual de fechas, hoja
  `Por_Semana`, generacion de N dailies + consolidado + zip (2026-07-06).
- [x] **Fase 7A** - Modelo Batch + endpoints CRUD + preview + generate +
  downloads. Detecta colisiones. Valida periodo del flash. Acepta ZIP.
  (2026-07-06).
- [x] **Fase 7B** - Dockerize + Railway ready: Dockerfile multi-stage
  non-root, `railway.json`, config con env vars (`NUTRI_DATA_DIR`,
  `PORT`, `NUTRI_CORS_ORIGINS`, `SUPABASE_*`), storage_adapter (Local +
  Supabase), tests, docs de deploy (2026-07-06).
- [x] **Fase 7C** - Frontend Angular 22 en `frontend/` con Material 3 y
  paleta corporativa NutriAvicola. 4 componentes lazy-loaded (dashboard,
  wizard de 5 pasos, detail, downloads). `BatchesService` cablea todos
  los endpoints. Build production OK (100 KB gzip inicial), E2E manual
  contra backend local validado. Config Vercel lista (2026-07-06).
- [x] **Fase 7D** - n8n workflow importable (`n8n/n8n_workflow_match_batch.json`)
  + docs de deploy en Railway. Backend expone todo lo necesario; n8n solo
  llama endpoints en secuencia y notifica (2026-07-06).
- [x] **Fase 7E** - StorageAdapter cableado en `generate_batch`: mirror
 automatico de outputs al bucket Supabase cuando `SUPABASE_URL +
 SUPABASE_SERVICE_KEY` estan definidas. `download_file` retorna 307
 Redirect a signed URL. Fallback silencioso a disco local si Supabase
 falla. 2 tests con fake storage (2026-07-06).
- [x] **Fase 7C.1** - Fix pagina en blanco Vercel + local (2026-07-06):
 (a) `environment.prod.ts` faltaba el esquema `https://` en `apiBaseUrl`,
 lo que provocaba que HttpClient tratara la URL como path relativo y
 pegara al propio Vercel devolviendo `index.html` en vez de JSON;
 (b) `vercel.json` con `rewrites: [{ source: "/(.*)", ... }]` capturaba
 tambien los bundles `main-*.js` y `styles-*.css` cuando el navegador
 pedia un hash viejo (post-redeploy), rompiendo la app con
 `SyntaxError: Unexpected token '<'`. Nueva regex excluye `assets/` y
 cualquier ruta con extension. Agregamos `Cache-Control: no-cache` sobre
 `index.html` para evitar el cache de hashes zombies; y
 `max-age=31536000, immutable` sobre los hashed assets; (c) `app.html`
 usaba `src="assets/logo.jpg"` (relativo), que se rompia en rutas
 anidadas tipo `/batches/nuevo/assets/logo.jpg`; ahora usa
 `/assets/logo.jpg` (absoluto); (d) el preview URL
 `cumplimientoplataforma-<hash>-byzocars-projects.vercel.app` sirve la
 pagina de login de Vercel (Deployment Protection activa por default),
 no la SPA. Usar el dominio de produccion o desactivar la proteccion en
 Vercel dashboard. Troubleshooting completo en
 [docs/deploy_vercel.md](docs/deploy_vercel.md#troubleshooting-pagina-en-blanco).
---

## Fase 4 - resumen de lo entregado (2026-07-02)

**API endpoint tree:** 21 endpoints, Swagger en `http://127.0.0.1:8000/docs`.

```
/health                                      GET
/files/upload-pre-corte                      POST   (multipart, single, xlsx obligatorio)
/files/upload-pre-corte-batch                POST   (multipart, multiple)
/files/upload-flash                          POST   (multipart, xlsx o csv)
/runs                                        GET    (list recientes)
/runs/start-batch                            POST   (crea master + N sub-runs)
/runs/{run_id}                               GET
/runs/{run_id}/sub-runs                      GET
/runs/{run_id}/approve                       POST
/runs/{run_id}/reject                        POST
/pipeline/{run_id}/extract-date              POST
/pipeline/{run_id}/load-pre-corte            POST
/pipeline/{run_id}/load-flash                POST
/pipeline/{run_id}/aggregate                 POST
/pipeline/{run_id}/match                     POST
/pipeline/{run_id}/validate                  POST   (setea status awaiting_approval)
/pipeline/{run_id}/kpis-preview              POST   (deprecado; reemplazado por /kpis/excel)
/pipeline/{run_id}/persist                   POST   (requiere status=approved)
/catalog                                     GET    (lista + stats del catalogo SKU)
/catalog/manual-mapping                      POST   (registra un SAP manualmente)
/catalog/import-homologacion                 POST   (recarga homologacion .xlsx)
/kpis/excel                                  GET    (Fase 6: descarga .xlsx formateado, ?desde=&hasta=)
/calendario/no-laborales                     GET    (Fase 6.6: festivos oficiales, ?year=Y)
/batches                                     POST   (Fase 7A: crea batch draft)
/batches                                     GET    (lista batches)
/batches/{id}                                GET    (detalle: pre_cortes + flash + status)
/batches/{id}                                PATCH  (renombrar / notas)
/batches/{id}                                DELETE (solo draft/archived/failed)
/batches/{id}/archive                        POST   (soft delete)
/batches/{id}/pre-cortes                     POST   (multipart: 1..N pre_cortes)
/batches/{id}/pre-cortes/from-zip            POST   (multipart zip, filtra por regex)
/batches/{id}/pre-cortes/{carga_id}          DELETE
/batches/{id}/flash                          POST   (multipart + query year,month)
/batches/{id}/flash                          DELETE
/batches/{id}/preview                        GET    (JSON: dias, colisiones, saltos, flash_ok)
/batches/{id}/confirm                        POST   (draft -> ready_to_match, bloquea si colisiones)
/batches/{id}/generate                       POST   (persist runs + genera excels + zip)
/batches/{id}/downloads                      GET    (lista archivos generados)
/batches/{id}/downloads/{filename}           GET    (stream FileResponse)
```

**Cache local:** `data/uploads/{carga_id}.{ext}` (fuente original) +
`data/uploads/{carga_id}.pkl` (DataFrame parseado). Cada endpoint del pipeline
lee del pickle -> no re-parsea el xlsx.

**Levantar el server:** `run_backend.bat` (usa uvicorn con reload).

**Tests:** 90/90 tests OK. `tests/test_api.py` cubre happy path completo,
idempotencia, batch multi-pre-corte, errores 404/409/415/422, y endpoints
del catalogo. `tests/test_resumen_parser.py` verifica que el parser del
RESUMEN preserva las 18 filas no-cero y el total 299,416 unidades del fixture.
`tests/test_sku_catalog.py` cubre upsert, prioridad por fuente, alias de
formato y aprendizaje via pair-learn.

**Contrato para Power Automate:** cada endpoint es HTTP JSON simple; el
orquestador solo hace POST secuenciales y toma decisiones sobre el `status`
del run que retorna `/runs/{run_id}`.

---

## Fase 4.5 - Migracion PRE CORTE de NOTIFICACION a RESUMEN (2026-07-02)

### Que cambio y por que

La hoja NOTIFICACION del PRE CORTE es notificacion de bodega, NO el plan de
produccion. La hoja **RESUMEN** contiene el plan real de comercializacion
(bandejas por REFERENCIA/TIPO/FORMATO/UNIDADES). Antes se cargaba
NOTIFICACION como fuente autoritativa, produciendo cifras que no coincidian
con lo planificado por comercializacion.

### Reglas nuevas de intake

- **PRE CORTE debe llegar como `.xlsx` (o `.xlsm`)**. El CSV/TSV destruyen
  los merged cells del RESUMEN (columna REFERENCIA queda vacia en 4 de 5
  filas de cada bloque). El endpoint `POST /files/upload-pre-corte` devuelve
  `415 Unsupported Media Type` si recibe otro formato.
- **RESUMEN es la fuente autoritativa** (parseado con `openpyxl`, no pandas).
- **NOTIFICACION es opcional**. Si viene en el mismo `.xlsx`, se usa para
  aprendizaje del catalogo (pair-learn) y validacion cruzada de totales.

### Puente SAP: catalogo persistente `sku_catalog`

Nueva tabla en `data/historico.sqlite`:

```sql
CREATE TABLE sku_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referencia TEXT NOT NULL,
    tipo TEXT NOT NULL,
    formato TEXT NOT NULL,
    unidades_por_empaque INTEGER NOT NULL,
    material_sap INTEGER NOT NULL,
    nombre_notificacion TEXT,
    fuente TEXT NOT NULL DEFAULT 'aprendido',
    primera_vez_visto TIMESTAMP,
    ultima_vez_visto TIMESTAMP,
    veces_visto INTEGER NOT NULL DEFAULT 1,
    UNIQUE(referencia, tipo, formato, unidades_por_empaque)
);
```

**Prioridad de fuentes al haber conflicto:**
`manual` (3) > `aprendido_pair` (2) > `homologacion` (1).

**Aliases de FORMATO** (RESUMEN <-> homologacion externa):
`ESTUCHE <-> ESTUCHERIA`, `VITAFILM <-> TERMOENCOGIDO`.

### Como se puebla el catalogo (3 mecanismos)

1. **Import inicial** desde `homologacion materiales Nuevo.xlsx` (hojas
   `Hoja2 (2)` y `Ata`):
   `python -m app.core.sku_catalog --import-homologacion <path>`
   o `POST /catalog/import-homologacion`. Solo inserta tuplas no ambiguas
   (skip si la misma REFERENCIA/TIPO/FORMATO/UNIDADES apunta a >1 SAP).

2. **Aprendizaje automatico** al procesar cada PRE CORTE con NOTIFICACION:
   `update_catalog_from_pair(conn, resumen_df, notif_df)` aparea celdas
   por `bandejas == necesidad_bandeja` y unidades. Sobrescribe entradas
   de `homologacion` si tenian el SAP incorrecto.

3. **Mapeo manual** para SKUs que ni la homologacion ni el aprendizaje
   pudieron resolver: `POST /catalog/manual-mapping` (prioridad maxima).

### Modulos nuevos y renombrados

- `app/core/resumen_parser.py`: `load_resumen(path) -> (df_long, meta)`.
- `app/core/sku_catalog.py`: `import_from_homologacion`,
  `update_catalog_from_pair`, `resolve_sap`, `attach_sap_to_resumen`,
  `upsert_entry`, `list_catalog`, `catalog_stats`, CLI con `--stats`,
  `--list`, `--import-homologacion`, `--backfill`.
- `app/core/loaders.py`: `load_pre_corte` reescrito para orquestar
  RESUMEN + NOTIFICACION opcional + catalogo; nuevo `load_notificacion`.
- `app/core/validators.py`: `validate_resumen_total_preserved`,
  `validate_resumen_vs_notificacion`, `validate_catalog_coverage`
  reemplazan al viejo `validate_sum_preserved(NOTIFICADO)`.
- `app/api/routes/catalog.py`: endpoints `/catalog`, `/catalog/manual-mapping`,
  `/catalog/import-homologacion`.
- `app/api/schemas.py`: `CatalogEntry`, `CatalogListResponse`,
  `ManualMappingRequest/Response`. `FileUploadResponse` amplia con
  `notificacion_presente`, `num_filas_sin_sap`, `catalog_coverage_pct`,
  `pair_learn_stats`.

### Resultado verificado sobre el fixture

`tests/fixtures/PRE_CORTE_muestra.xlsx` (PRE CORTE 13.02.2026 real):
- 18 filas no-cero en RESUMEN, suma total 299,416 unidades.
- Con `homologacion.xlsx` importado + pair-learn: **100% cobertura SAP**
  (0 filas en `no_cruzados` por catalogo faltante).
- Ambiguidad de homologacion resuelta correctamente por pair-learn (ej:
  MARCA ORO/A/AMARRADO/30 tenia 3 candidatos en la homologacion; el
  pair-learn con NOTIFICACION resolvio a 30040, no a 30030).

---

## Fase 6.6 - Calendario laboral colombiano (2026-07-06)

**Regla nueva**: `fecha_produccion = siguiente_dia_habil(fecha_archivo + 1)`.

Si el dia siguiente al archivo es domingo o festivo colombiano oficial, la
fecha de produccion se desplaza al proximo dia habil. Puede saltar varios
dias si hay festivos encadenados (ej. sabado -> domingo + festivo lunes ->
martes). Esto evita cruzar contra un FLASH inexistente (no hay ventas los
domingos/festivos) y no contamina el KPI de cumplimiento.

**Fuente de festivos**: CSV estatico
`resources/festivos_colombia_2024_2030.csv`, generado offline por
`scripts/generar_calendario.py` con la libreria `holidays` (dev-only,
`requirements-dev.txt`). **Sin LLM**: los festivos son deterministicos, no
requieren razonamiento. **Sin API externa**: el CSV se carga en memoria al
importar `app.core.calendario`, lookups O(1).

**Que se excluye del CSV**: "Dia de Nuestra Senora del Rosario de
Chiquinquira" (13 julio observado). No es festivo laboral segun Ley
51/1983 aunque la libreria `holidays` lo incluya. Documentado en la
constante `EXCLUIR_MOTIVOS` de [scripts/generar_calendario.py](scripts/generar_calendario.py).

**Nuevos modulos y endpoints**:

- [app/core/calendario.py](app/core/calendario.py): `is_no_laboral`,
  `motivo_no_laboral`, `siguiente_dia_habil`, `dias_habiles`,
  `festivos_del_ano`, con excepcion `CalendarioSinCobertura` para fechas
  fuera del rango.
- [app/core/date_extractor.py](app/core/date_extractor.py):
  `extract_production_date_verbose` retorna `(fecha, dias_saltados,
  motivos_saltados)`. El endpoint de upload propaga estos campos.
- [app/api/routes/calendario.py](app/api/routes/calendario.py):
  `GET /calendario/no-laborales?year=Y` -> lista de festivos oficiales del
  ano (para el mini-calendario del frontend).
- `FileUploadResponse` extendido con `dias_saltados` y `motivos_saltados`.

**Regenerar el CSV**: cuando el Gobierno anada/quite un festivo o para
extender el rango de anios, correr `python scripts/generar_calendario.py`
y commitear el diff. Auditable en el repo.

**Tests** (`tests/test_calendario.py` + extensiones a
`tests/test_date_extractor.py` + `tests/test_api.py`):

- Sabado 07/02/2026 -> lunes 09/02/2026 (`dias_saltados=1`).
- Domingo 11/01/2026 + lunes 12/01/2026 (Reyes trasladado) -> martes
  13/01/2026 (`dias_saltados=2`).
- Pascua 2026: Jueves Santo 02/04, Viernes Santo 03/04, Domingo
  Resurreccion 05/04, salta a lunes 06/04 desde Jueves Santo.
- Chiquinquira NO figura como festivo en el CSV.
- `GET /calendario/no-laborales?year=2026` retorna ~18 festivos oficiales.
- El fixture existente (viernes 13/02 -> sabado 14/02) sigue verde porque
  el sabado es laboral.

---

## Fase 6 - Contrato de salida (clarificado 2026-07-06)

**El entregable final NO es una conexion a Power BI**. Es **un archivo `.xlsx`
bien formateado** que el equipo de BI descarga y usa como fuente para armar
sus dashboards por su cuenta. Nada de ODBC, nada de DirectQuery.

### KPI unico

**Cumplimiento %** = `unidades_reales_flash / unidades_plan_resumen * 100`.

Nada de "top desviaciones", "alertas", "fugas por marca", etc. hasta que
negocio pida mas. **Un solo KPI**.

### Batch multi-fecha (Fase 6.5, 2026-07-06)

Cuando el usuario sube N pre_cortes del mes contra UN flash mensual, el
sistema produce:

- **N archivos daily** `cumplimiento_YYYYMMDD.xlsx`, uno por cada fecha de
  produccion con datos. Cada daily lleva Portada + Resumen (1 fila) +
  Por_Categoria + Detalle_Material + No_Cruzados. Sin hoja Por_Semana
  (no aporta para un solo dia).
- **1 consolidado** `cumplimiento_consolidado_YYYYMMDD_YYYYMMDD.xlsx` que
  agrega ademas la hoja **`Por_Semana`** (una fila por semana ISO con
  cumplimiento agregado + total al pie), y agrupa visualmente la fecha
  en `Por_Categoria`, `Detalle_Material`, `No_Cruzados`.
- **1 ZIP** `cumplimiento_batch_YYYYMMDD_YYYYMMDD.zip` con el consolidado
  + todos los dailies.

**Agrupacion visual de fechas** (soluciona el "14/02/2026 repetido 150
veces"): `write_dataframe_as_table(group_by_key="fecha_produccion")` muestra
la fecha solo en la primera fila del grupo y aplica un borde medium naranja
sobre todas las celdas al cambio de grupo. **No usa merge de celdas**, asi
el ListObject de Excel sigue soportando filtros y ordenamiento sin romper.

**API publica de exporters.py**:
- `export_cumplimiento_xlsx(desde, hasta, dest, ...)`: consolidado.
- `export_cumplimiento_diario(fecha, dest, ...)`: alias de conveniencia
  para un dia unico.
- `export_batch_completo(desde, hasta, output_dir, ...)`: paquete completo,
  retorna dict con `consolidado`, `dailies`, `zip`, `fechas_procesadas`,
  `fechas_sin_datos_en_rango`.
- `suggested_daily_filename(fecha)`, `suggested_consolidado_filename(desde,
  hasta)`, `suggested_zip_filename(desde, hasta)`.

Demo end-to-end multi-fecha:
`venv\Scripts\python.exe _demo_export.py` -> genera batch con 6 dailies +
consolidado + zip (~185 KB) desde el fixture real.

---

## Fase 7A - Modelo Batch (2026-07-06)

Un `batch` es el envelope que agrupa **N pre_cortes diarios + 1 flash
mensual en staging** antes de disparar el match. Reemplaza el "master run"
del schema previo. Es la unidad principal con la que interactuan el
frontend (Fase 7C) y el orquestador (Fase 7D).

**Estados** (`app/core/batches.py::BatchStatus`):

`draft` -> `ready_to_match` -> `matching` -> `matched` (feliz)
                                          -> `failed`
                                          -> `archived` (soft delete desde
                                             cualquier estado terminal)

Solo `draft` permite CRUD (agregar/quitar pre_cortes, cambiar flash,
renombrar). `ready_to_match` es el punto de checkpoint humano; el frontend
llama `confirm` cuando el usuario aprueba el preview.

**Regla clave de negocio: colisiones**. Con la Fase 6.6 (skip a dia habil),
dos pre_cortes distintos pueden resolver al mismo `fecha_produccion`
(ej. si por error se sube el pre_corte del domingo). El endpoint
`GET /batches/{id}/preview` retorna un array `colisiones: [{fecha,
pre_corte_carga_ids: [...]}]` y `POST /batches/{id}/confirm` responde 409
si hay colisiones sin resolver. El usuario debe eliminar el duplicado con
`DELETE /batches/{id}/pre-cortes/{id}` antes de continuar.

**Regla clave del flash: periodo declarado**. `POST /batches/{id}/flash`
requiere `year` y `month` como query params. La API valida que el flash
contenga facturas de ese periodo (via `validar_flash_periodo` que revisa
`fecha_factura` del df parseado). Retorna 422 si no cuadra, con mensaje que
incluye el rango real del archivo. Este endpoint es lo que reemplaza a la
extraccion automatica (el flash no tiene fecha en el nombre).

**ZIP upload**: `POST /batches/{id}/pre-cortes/from-zip` acepta un `.zip`
con multiples pre_cortes. Filtra por `FILENAME_PRE_CORTE_REGEX` y retorna
`{procesados, ignorados: [{filename, motivo}]}`. Los archivos ignorados
incluyen: extension no permitida, nombre no matchea el regex, error al
parsear, etc. Nunca falla la request completa por un archivo malo.

**Generate**: `POST /batches/{id}/generate` (idempotente por UNIQUE en
tabla `cruce`) itera sobre los pre_cortes del batch, ejecuta `match_by_material`
contra el flash, persiste via `persist_run`, y llama a
`export_batch_completo` para producir N dailies + 1 consolidado + 1 zip en
`data/onedrive_export/batch_{id}/`. Cambia el estado a `matched` y guarda
`output_dir`. Si falla, marca `failed`.

**Downloads**: `GET /batches/{id}/downloads` lista los archivos con
`{filename, size_bytes, kind}` donde `kind` es `consolidado | daily | zip`.
`GET /batches/{id}/downloads/{filename}` sirve el archivo via
`FileResponse` con proteccion contra path traversal.

**Tests**: 20 en `tests/test_batches.py` cubriendo CRUD, ZIP mixto,
colisiones (sabado 07 + domingo 08 -> ambos apuntan a lunes 09),
validacion de mes del flash, preview con saltos, confirm bloqueado por
colisiones, flujo E2E completo, path traversal bloqueado.

---

## Fase 7B - Docker + Railway ready (2026-07-06)

**Archivos nuevos en el root**:
- [Dockerfile](Dockerfile) — multi-stage `python:3.14-slim-bookworm`,
  non-root user `nutri`, `HEALTHCHECK` contra `/health`, `EXPOSE 8000`,
  `PATH` incluye `/home/nutri/.local/bin` para uvicorn.
- [.dockerignore](.dockerignore) — excluye `tests/`, `data/`, `venv/`,
  `.git/`, `.env`, docs, cursor metadata.
- [railway.json](railway.json) — `DOCKERFILE` builder, healthcheck
  `/health` timeout 30 s, restart `ON_FAILURE` max 3.
- [.gitignore](.gitignore) — bloquea `data/*.sqlite*`, `data/uploads/`,
  `data/onedrive_export/`, `.env*`, `.venv/`, `.cursor/`.
- [.env.example](.env.example) — template de env vars documentado.
- [requirements.txt](requirements.txt) — deps runtime (Pillow + supabase
  agregadas). Pytest y holidays movidos a `requirements-dev.txt`.
- [scripts/setup_supabase.py](scripts/setup_supabase.py) — crea buckets
  `uploads`/`outputs` privados. Idempotente. Requiere SECRET key.
- [app/core/storage_adapter.py](app/core/storage_adapter.py) — `Storage`
  Protocol + `LocalStorage` (default) + `SupabaseStorage` (activado por
  env vars). Factory `get_storage()` singleton. Path traversal
  protection en LocalStorage.
- [docs/deploy_railway.md](docs/deploy_railway.md) — guia paso a paso
  (crear proyecto, volumen /data, env vars, healthcheck, Supabase,
  smoke build local, rotacion de credenciales, troubleshooting).

**Cambios en [app/config.py](app/config.py)**:
- Todas las rutas configurables por env: `NUTRI_DATA_DIR`, `NUTRI_LOGS_DIR`.
- `PORT` de Railway respetado (fallback a `NUTRI_API_PORT` local).
- `NUTRI_CORS_ORIGINS` parseado de CSV.
- Nuevo `storage_backend_activo()` -> `"supabase" | "local"`.
- Cargador `.env` minimo sin dep externa; se salta en pytest.

**Estado de las env vars**:

| Variable                    | Default local        | Railway prod                    |
|-----------------------------|----------------------|---------------------------------|
| `NUTRI_DATA_DIR`            | `<repo>/data`        | `/data` (volumen persistente)   |
| `NUTRI_LOGS_DIR`            | `<repo>/logs`        | `/data/logs`                    |
| `NUTRI_API_HOST`            | `127.0.0.1`          | `0.0.0.0`                       |
| `PORT`                      | (no aplica)          | inyectada por Railway           |
| `NUTRI_API_PORT`            | `8000`               | ignorada (usa `PORT`)           |
| `NUTRI_CORS_ORIGINS`        | localhost dev        | dominio Vercel + previews       |
| `SUPABASE_URL`              | del `.env` opcional  | de Railway UI                   |
| `SUPABASE_SERVICE_KEY`      | del `.env` opcional  | de Railway UI (SECRET, no anon) |
| `SUPABASE_BUCKET_UPLOADS`   | `uploads`            | `uploads`                       |
| `SUPABASE_BUCKET_OUTPUTS`   | `outputs`            | `outputs`                       |

**Tests nuevos (17)**:
- `tests/test_config.py` (7): default data_dir, override por env, PORT
  de Railway, CORS parsing, storage backend local/supabase.
- `tests/test_storage_adapter.py` (10): protocol compliance, round-trip
  put/get/delete/exists/list, `public_url` file URI, path traversal
  bloqueado, `SupabaseStorage` requiere creds y libreria, factory
  singleton.

**Notas de seguridad**:
- `.env` local con las credenciales Supabase compartidas por el usuario
  esta en el repo pero excluido por `.gitignore`.
- La key que el usuario compartio es **publishable** (`sb_publishable_...`),
  no permite writes a buckets privados. Para Fase 7E se necesita la
  **secret** (`sb_secret_...`).
- Rotar credenciales al cerrar el proyecto (checklist en
  [docs/deploy_railway.md](docs/deploy_railway.md#rotacion-de-credenciales)).

**Pendiente para 7E**: cablear los endpoints de upload/download del router
`batches.py` para que ademas de escribir localmente escriban al
`StorageAdapter.get()`. Con `LocalStorage` es no-op. Con `SupabaseStorage`
los blobs viajan a Supabase y las descargas usan signed URLs.

---

## Fase 7C - Frontend Angular (2026-07-06)

**Stack**: Angular 22 (standalone components + signals + lazy routing) +
Angular Material 3 con paleta corporativa NutriAvicola (navy `#0F2E4C`
primary + naranja `#E87722` accent). SCSS puro, sin Tailwind, sin React.

**Estructura** ([frontend/](frontend/)):

```
frontend/
  src/
    app/
      core/
        models.ts               interfaces TS <-> Pydantic schemas
        batches.service.ts      HttpClient wrapper de los 16 endpoints /batches
      features/
        dashboard/              contadores + tabla batches recientes
        batches/
          batch-wizard.*        mat-stepper 5 pasos
          batch-detail.*        detalle read-only + archivar
          downloads.*           3 secciones (consolidado / dailies / zip)
      app.{ts,html,scss}        root con toolbar + logo NutriAvicola
      app.config.ts             providers globales (Router + HttpClient + Animations)
      app.routes.ts             rutas lazy loaded
    environments/
      environment.ts            dev localhost:8000
      environment.prod.ts       prod URL Railway
    assets/logo.jpg             copia del resources/
    styles.scss                 tema Material 3 + CSS vars corporativas
  vercel.json                   SPA rewrite + build settings
  angular.json                  fileReplacements dev -> prod
  README.md                     guia dev + deploy
```

**Wizard de 5 pasos** ([batch-wizard.component](frontend/src/app/features/batches/batch-wizard.component.ts)):

1. **Nombrar** — form con validacion `Validators.required`.
2. **PRE CORTES** — input multiple con soporte `.xlsx` + `.zip`. Detecta
   por extension y dispara `uploadPreCortes` o `uploadPreCortesFromZip`.
   Tabla en vivo con fecha extraida (incluyendo skip de dias no laborales),
   boton eliminar por fila.
3. **FLASH mensual** — dos selectores anio/mes + input file. La API valida
   coherencia del periodo antes de aceptar.
4. **Preview** — llama `GET /batches/:id/preview`, renderiza:
   - Alertas si el flash no cuadra (nutri-badge warn).
   - Alertas si hay colisiones (nutri-badge bad, bloquea confirmacion).
   - Info si hubo saltos por dia no laboral (nutri-badge info).
   - Tabla dias con `cumplimiento_pct` coloreado (verde/amarillo/rojo).
5. **Generar** — llama `POST /confirm` + `POST /generate` en secuencia,
   muestra resultado (consolidado, zip) + boton "Ir a descargas".

**Descargas** ([downloads.component](frontend/src/app/features/batches/downloads.component.ts)):
tres secciones (consolidado, N dailies, ZIP) con `<a href download>`
directo al endpoint `GET /batches/:id/downloads/:filename` del backend.

**Verificacion E2E** contra backend local:

```
$ curl POST /batches -> {"id":"073cb45e...","status":"draft"}
$ curl POST /batches/{id}/pre-cortes (multipart) -> 200
$ curl POST /batches/{id}/flash?year=2026&month=2 (multipart) -> 200
$ curl GET /batches/{id}/preview -> {"listo_para_confirmar":true,"dias":1}
```

CORS preflight OPTIONS retorna
`Access-Control-Allow-Origin: http://127.0.0.1:4200`.

**Build production**: 100 KB gzip bundle inicial, chunks lazy por ruta
(dashboard 6 KB, wizard 43 KB, detail 2 KB, downloads 2 KB).

**Deploy en Vercel** — ver [docs/deploy_vercel.md](docs/deploy_vercel.md).
Framework preset `Other`, root directory `frontend`, `vercel.json` ya
tiene el SPA rewrite. Antes del deploy, actualizar
`environment.prod.ts` con la URL Railway del backend.

### Niveles de granularidad (en orden de simplicidad)

1. **Total huevos** (nivel prioritario para arrancar) - suma de todo el
   RESUMEN vs suma del FLASH del dia. Esto sale directo de la fila `TOTAL`
   del RESUMEN (columnas C-Q sumadas) y es lo que negocio quiere ver primero.
2. **Por categoria de huevo** (TIPO: A, AA, AAA, AAAA, B, C) - agrupa por
   la columna `tipo` del `sku_catalog` / RESUMEN. Corresponde a las filas
   39-43 del RESUMEN (subtotales por TIPO).
3. **Por marca / referencia** (MARCA ORO, PLUS, CAMPESINO, KOSHER, DHA,
   SELENIO, JUNIOR, TAEQ, OLIMPICA, ...) - se implementa despues; requiere
   coordinar aliases entre RESUMEN y homologacion.

### Estructura del .xlsx (5 hojas, 100% tabular, sin literatura)

Archivo corporativo: **solo tablas relacionadas, cero instrucciones**. El
equipo de BI ya sabe leer un Excel; no hace falta "como leer este archivo",
ni leyenda de semaforo, ni cards decorativos. Todo es tabla con header
navy y bordes visibles.

- **Portada**: logo NutriAvicola en A1 + titulo + tabla `Indicador | Valor`
  con 7 filas: cumplimiento global (%), plan total, real total, delta,
  dias, materiales cruzados, filas no cruzadas. El valor del cumplimiento
  se semaforea (verde/amarillo/rojo) segun rango. **Nada mas.**
- **Resumen**: una fila por `fecha_produccion` con `plan_total`,
  `real_total`, `delta_total`, `cumplimiento_pct`. Fila TOTAL al final.
- **Por_Categoria**: filas `fecha_produccion x tipo` con `plan_categoria`,
  `real_categoria`, `cumplimiento_pct`. Las 6 categorias siempre.
- **Detalle_Material**: dump de la tabla `cruce` con columnas legibles
  para tablas dinamicas del equipo de BI.
- **No_Cruzados**: `no_cruzados` con `origen` (pre_corte | flash) y motivo.

### Diseno visual y replicable a escala

**No es aceptable** un archivo con celdas planas y sin formato. El archivo
tiene que verse profesional al abrirlo. Pero tampoco es aceptable formatear
celda por celda de forma ad-hoc: eso no escala cuando agreguemos hojas o
cuando negocio pida cambiar la paleta.

**Solucion:** un modulo `app/core/excel_style.py` con:

- **Paleta corporativa NutriAvicola** derivada del logo `resources/`:
  `BRAND_NAVY = "#0F2E4C"` (headers), `BRAND_ORANGE = "#E87722"` (acentos),
  `BRAND_ORANGE_LIGHT = "#FCE1C7"` (fila TOTAL), grid `#8C8C8C` (bordes
  visibles), y semaforo estilo Excel clasico (`#63BE7B / #FFEB84 /
  #F8696B`). Cambiar la paleta = editar una constante.
- **`NamedStyle` registrados una sola vez en el workbook**
  (`st_header`, `st_kpi_header`, `st_body_int`, `st_body_pct`,
  `st_body_text`, `st_body_date`, `st_total_int`, `st_total_pct`,
  `st_total_label`, `st_kpi_label`, `st_kpi_value_int`,
  `st_kpi_value_pct_good|warn|bad`, `st_title`, `st_subtitle`). Openpyxl
  los aplica por nombre; es ~10x mas rapido que estilar celda por celda.
- **`write_dataframe_as_table(...)`** escribe el DataFrame como Excel Table
  nativa con `TableStyleMedium9` (bordes marcados) + aplica bordes
  explicitos por celda para que no dependa del TableStyle.
- **`write_kpi_table(ws, kpis, start_row)`** arma una tabla `Indicador |
  Valor` con header navy, filas alternas gris claro y bordes visibles.
  Reemplaza el "kpi card" grande — es tabla, no decorativo.
- **`insert_logo(ws, path, anchor, max_height_px)`** carga el logo
  corporativo desde `resources/` y lo escala.
- **`apply_traffic_light(ws, col_letter, from_row, to_row)`** con
  `CellIsRule` en 3 rangos discretos.
- **`add_title_block(ws, title, subtitle)`** titulo + subtitulo con merge.
- **`set_page_setup(ws)`**: landscape, fit-to-page, gridlines ocultas,
  zoom 100 %, freeze panes.

**Regla de oro para que escale:** el modulo `exporters.py` **nunca** llama
a `Font(...)`, `Fill(...)` o `Border(...)` directamente. Solo llama a las
funciones/estilos de `excel_style.py`. Si maniana negocio pide cambiar el
azul corporativo a verde, se edita una constante y todas las hojas se
actualizan. Si se agrega la hoja `Por_Marca` en la Fase 6.5, se reutilizan
las mismas funciones.

**Elementos visuales concretos por hoja:**
- Header: fondo `BRAND_NAVY` (`#0F2E4C`), texto blanco negrita 11pt,
  altura 28, alineacion centrada, borde medium navy oscuro (`#081A2C`).
- Body: fuente navy sobre blanco, borde `thin` gris `#8C8C8C` en las 4
  direcciones de cada celda (bordes 100% visibles).
- Banded rows nativas via Excel Table Medium9 (Excel las repinta al
  filtrar).
- Fila TOTAL: fondo naranja claro `#FCE1C7`, negrita, borde medium
  naranja profundo `#B85F1A`.
- Columnas de `cumplimiento_pct`: formato `"0.00%"`, semaforo con
  `CellIsRule` (verde `#63BE7B`, amarillo `#FFEB84`, rojo `#F8696B`).
- Columnas de unidades: formato `"#,##0"` (separador de miles).
- Freeze panes debajo del header.
- Anchos de columna autoajustados con tope de 40 caracteres.
- Portada: logo NutriAvicola en A1 + titulo + tabla KPI. Sin cards
  decorativos ni instrucciones.

### Resultado verificado sobre el fixture (Fase 6)

`_demo_export.py` sobre `PRE CORTE 13.02.2026 (1).xlsx` + `FLASH.xlsx`:

- Archivo generado: `data/onedrive_export/cumplimiento_20260214_20260214.xlsx`
  (~16.5 KB, 5 hojas, 4 Excel Tables, 3 rangos con semaforo).
- Portada: card con "Cumplimiento global del rango" en pantalla completa,
  info del rango de fechas y una guia rapida de las 5 hojas.
- Resumen: 1 fila por dia + fila TOTAL con fondo amarillo.
- Por_Categoria: 7 categorias (las 6 estandar + `AA-A` derivada del
  catalogo) para cada fecha, con 0 donde no hay actividad.
- Detalle_Material: dump legible del cruce con SAP, referencia, tipo,
  formato, plan, real, delta, cumplimiento %.
- No_Cruzados: fugas (origen='flash') y solo_pre (origen='pre_corte')
  con motivo.

Tests: 18/18 en `test_excel_style.py` + 14/14 en `test_exporters.py`,
incluyendo el test de "puritanismo" que **falla el build** si alguien
mete `Font(...)` directo en `exporters.py`.

---

## Referencias

- Fixtures de test: `tests/fixtures/`
- Documentacion Power Automate: `docs/tutorial_power_automate_orquestador.md` (pendiente Fase 7)
- Documentacion Copilot Studio: `docs/tutorial_copilot_studio_embed.md` (pendiente Fase 5)
- Excel de la Fase 6: se genera via `GET /kpis/excel?desde=&hasta=` o via
  `app.core.exporters.export_cumplimiento_xlsx()`. Se cachea en
  `data/onedrive_export/cumplimiento_YYYYMMDD_YYYYMMDD.xlsx`.
- Demo end-to-end del Excel: `venv\Scripts\python.exe _demo_export.py`.
- Estilo del Excel: **todo** en `app/core/excel_style.py`. `exporters.py`
  no puede importar `Font/PatternFill/Border/Alignment/Side` (test
  `test_exporters_no_importa_styles_directamente` lo verifica).
