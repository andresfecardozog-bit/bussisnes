# Auditoria de seguridad - estado ACTUAL del repositorio

Fecha: 2026-07-09. Alcance: backend FastAPI (`app/`), configuracion
(`app/config.py`), almacenamiento (SQLite + uploads en disco + Supabase
opcional), frontend Angular (`frontend/`) y despliegue (`Dockerfile`,
`railway.json`, `docker-entrypoint.sh`, `docs/deploy_railway.md`).

Metodo: lectura de codigo + revision de historial git + verificacion de
patrones (secretos, inyeccion, path traversal, deserializacion, CORS). Las
referencias son `archivo:linea` sobre el estado del repo en esta fecha.

Marco de referencia: OWASP Top 10:2025 (A01 Broken Access Control sigue en
el puesto 1; A02 Security Misconfiguration; A04 Cryptographic Failures; A05
Injection) y OWASP ASVS. Fuentes citadas al final.

---

## Resumen del estado

La plataforma HOY no tiene ninguna capa de autenticacion ni de
autorizacion. Todos los endpoints del backend son publicos y el backend
esta desplegado en un dominio publico de Railway
(`bussisnes-intelligen-t-production.up.railway.app`, ver
`frontend/src/environments/environment.prod.ts:11`). Cualquier persona en
internet que conozca o descubra la URL puede subir archivos, disparar
cruces, leer datos de negocio (clientes, NITs, materiales, niveles de
cumplimiento) y descargar los Excel/PBIP generados. Este es el hallazgo
dominante y condiciona la severidad de casi todo lo demas.

Conteo de hallazgos: 2 Criticos, 4 Altos, 5 Medios, 3 Bajos.

---

## Confirmacion explicita: credenciales hardcodeadas y puertas traseras

- **No se encontraron credenciales hardcodeadas** en el codigo. Todos los
  secretos se leen de variables de entorno: `GEMINI_API_KEY`
  (`app/config.py:166`), `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`
  (`app/config.py:97-98`). No hay API keys, contrasenas ni tokens
  embebidos en `.py`, `.ts`, `.json` ni `Dockerfile`.
- **No hay secretos en el historial de git.** El repo tiene 43 commits; no
  aparece ningun `.env`, `.pem` ni `.key` agregado en el historial, y una
  busqueda de patrones de claves (`AIza...`, `sb_secret_...`, JWT `eyJ...`)
  no arrojo coincidencias. El `.gitignore` excluye `.env`, `*.pem`, `*.key`
  y `data/` (`.gitignore:56-62`). Existe un `.env` local en la maquina de
  desarrollo (fuera de git), lo cual es correcto.
- **No se encontraron puertas traseras** en el sentido de codigo malicioso:
  no hay endpoints ocultos, no hay bypass de logica, no hay usuarios ni
  contrasenas semilla en el codigo, no hay `eval`/`exec` de entrada del
  usuario, no hay ejecucion de comandos del sistema con datos del usuario.
- Matiz importante: la ausencia de puertas traseras NO significa que el
  sistema este protegido. La superficie completa esta abierta por diseno
  actual (sin auth), que es funcionalmente equivalente a "todo es una
  puerta principal sin cerradura". Los agentes LLM tampoco calculan ni
  ejecutan cruces (eso lo hace el motor deterministico), asi que no hay
  vector de inyeccion de prompt que derive en ejecucion de codigo.

---

## Hallazgos priorizados

### CRITICO

#### C-1. Ausencia total de autenticacion y autorizacion (OWASP A01:2025)

- **Descripcion**: ningun router monta dependencia de auth. `app/api/main.py`
  registra 8 routers (`files`, `runs`, `pipeline`, `catalog`, `exports`,
  `calendario`, `batches`, `profiles`) sin `dependencies=[Depends(...)]` de
  seguridad ni middleware de autenticacion. La unica dependencia global es
  CORS. Endpoints como `POST /files/upload-flash`, `POST /profiles/draft`,
  `POST /batches/{id}/generate`, `GET /profiles/{id}/downloads/{filename}`,
  `GET /kpis/excel` estan abiertos.
- **Archivo:linea**: `app/api/main.py:53-88` (registro de routers sin auth);
  `app/api/dependencies.py` (solo hay `db_connection`, `get_carga_or_404`,
  `get_run_or_404`; ninguna de auth).
- **Impacto**: exposicion completa de datos de negocio (clientes, NITs,
  materiales, cumplimiento de entregas, devoluciones), manipulacion y
  borrado de datos (`DELETE /batches/{id}`), consumo de la API de Gemini de
  pago por terceros (costo economico directo via `POST /profiles/draft` y
  `/refine`), y uso del servidor como almacenamiento/computo gratis. Es el
  riesgo #1 de OWASP y bloquea la salida a produccion.
- **Recomendacion**: implementar autenticacion obligatoria y aplicarla de
  forma global (router-level o app-level `dependencies`), de modo que un
  endpoint nuevo nazca protegido por defecto. Ver `docs/seguridad_plan.md`.

#### C-2. CORS mal configurado: comodin `*` junto con `allow_credentials=True`

- **Descripcion**: el middleware agrega `"*"` a la lista de origenes
  permitidos y ademas habilita credenciales, metodos y headers comodin.
  `allow_origins=API_CORS_ORIGINS + ["*"]` anula por completo la lista
  blanca de `NUTRI_CORS_ORIGINS`: cualquier origen queda permitido.
- **Archivo:linea**: `app/api/main.py:53-59`.
- **Impacto**: la combinacion `allow_origins=["*"]` + `allow_credentials=True`
  es explicitamente insegura (OWASP A05:2021 / A01:2025, CWE-942). Los
  navegadores modernos rechazan reflejar `*` cuando hay credenciales, pero
  la implementacion de Starlette, al recibir `*`, tiende a reflejar el
  `Origin` entrante, lo que en la practica habilita que cualquier sitio
  web haga peticiones autenticadas cross-origin contra la API una vez que
  exista sesion. Hoy, sin auth, agrava C-1 permitiendo ataques desde
  cualquier pagina que la victima visite.
- **Recomendacion**: eliminar el `+ ["*"]`. Mantener solo la lista blanca
  explicita de `NUTRI_CORS_ORIGINS`. Restringir `allow_methods` y
  `allow_headers` a lo necesario. Nunca usar `*` con credenciales.

### ALTO

#### A-1. Deserializacion insegura con pickle (OWASP A08:2025, CWE-502)

- **Descripcion**: el cache de DataFrames parseados usa pickle. `save_parsed_df`
  hace `df.to_pickle(p)` y `load_parsed_df` hace `pd.read_pickle(p)` sobre
  `data/uploads/{carga_id}.pkl`. `pd.read_pickle` ejecuta codigo arbitrario
  al deserializar contenido malicioso.
- **Archivo:linea**: `app/api/storage.py:35` (`to_pickle`) y
  `app/api/storage.py:43` (`read_pickle`).
- **Impacto**: hoy el `.pkl` lo genera el propio backend, no el usuario, por
  lo que no es explotable directamente desde la API. Se convierte en RCE si
  un atacante logra escribir en `data/uploads/` (por ejemplo via el path
  traversal de A-2, o por un volumen compartido/backup restaurado). Es una
  bomba latente que ademas dificulta portar a Postgres.
- **Recomendacion**: reemplazar pickle por un formato de datos sin ejecucion
  (parquet o feather con pyarrow, o CSV). Si se mantiene pickle a corto
  plazo, garantizar que el directorio no sea escribible por ninguna ruta
  controlable por el usuario y validar el `carga_id` como entero.

#### A-2. Path traversal en el nombre de archivo subido (OWASP A01:2025, CWE-22)

- **Descripcion**: al subir archivos, el nombre original del cliente se
  concatena a una ruta sin sanitizar. En `_process_upload`:
  `tmp_path = storage.UPLOADS_DIR / f"_tmp_{filename}"` donde `filename =
  upload.filename`. Un `filename` como `../../etc/algo` o
  `..\\..\\app\\config.py` puede escapar de `UPLOADS_DIR`. Igual patron en
  el import de homologacion: `tmp_path = storage.UPLOADS_DIR /
  f"_tmp_homolog_{filename}"`.
- **Archivo:linea**: `app/api/routes/files.py:56-57`;
  `app/api/routes/catalog.py:90`.
- **Impacto**: escritura de archivos fuera del directorio previsto
  (sobrescritura de codigo, del `.env`, de la base SQLite) y, encadenado con
  A-1, ejecucion de codigo. Los endpoints de descarga SI resuelven y validan
  contra traversal (`app/api/routes/batches.py:704-710`,
  `app/api/routes/profiles.py:656-659`), pero la subida NO.
- **Recomendacion**: sanitizar SIEMPRE el nombre con
  `Path(filename).name` (descartar componentes de directorio) y/o generar un
  nombre de servidor propio (uuid) ignorando el nombre del cliente para la
  ruta fisica. Validar la ruta final con `resolve()` +
  `relative_to(UPLOADS_DIR)` como ya se hace en las descargas.

#### A-3. Sin limite de tamano ni de tipo real en uploads (OWASP A04/A06, CWE-400)

- **Descripcion**: los endpoints de upload leen el archivo completo en
  memoria (`upload.file.read()`) sin limite de tamano ni control de numero
  de archivos por request. `POST /files/upload-pre-corte-batch` y
  `/batches/{id}/pre-cortes` aceptan listas de `UploadFile` ilimitadas; el
  ZIP de `pre-cortes/from-zip` se lee completo a memoria y se descomprime
  sin control (riesgo de zip bomb). La validacion de tipo es solo por
  extension, no por contenido.
- **Archivo:linea**: `app/api/routes/files.py:55`;
  `app/api/routes/batches.py:279-315`; `app/api/routes/catalog.py:89`.
- **Impacto**: denegacion de servicio por agotamiento de memoria/disco
  (el volumen `/data` de Railway es pequeno, 500 MB - 5 GB), y llenado del
  disco que tumba SQLite. Sin auth (C-1) es trivial de explotar.
- **Recomendacion**: imponer limite de tamano por archivo y por request
  (streaming a disco con corte), limitar numero de archivos, validar la
  firma/magic bytes del archivo, y proteger la descompresion de ZIP
  (limite de tamano descomprimido y de numero de entradas).

#### A-4. Documentacion interactiva y esquema OpenAPI publicos

- **Descripcion**: `/docs` (Swagger UI), `/openapi.json` y `/` (landing con
  links) estan expuestos sin autenticacion y anunciados en la raiz.
- **Archivo:linea**: `app/api/main.py:41-71` (FastAPI con `docs` por
  defecto; root que publica `/docs`, `/openapi.json`).
- **Impacto**: entrega a un atacante el mapa completo de la API (todos los
  endpoints, parametros y esquemas), acelerando la explotacion de C-1.
- **Recomendacion**: en produccion, deshabilitar `/docs`, `/redoc` y
  `/openapi.json` (o protegerlos tras auth de admin). Mantenerlos solo en
  entornos internos/dev.

### MEDIO

#### M-1. Datos de negocio sensibles expuestos sin control de acceso

- **Descripcion**: endpoints como `GET /catalog`, `GET /profiles`,
  `GET /profiles/{id}/proposal`, `GET /batches`, `GET /runs` y las descargas
  devuelven informacion comercial (clientes, NITs, segmentos, materiales,
  cumplimiento, devoluciones). Es consecuencia directa de C-1, pero se
  lista aparte por su impacto de privacidad/negocio.
- **Archivo:linea**: `app/api/routes/catalog.py:27-49`;
  `app/api/routes/profiles.py:314-317, 374-386, 637-660`.
- **Impacto**: fuga de datos comerciales de NutriAvicola y de sus clientes
  (posible dato personal: NIT, razon social).
- **Recomendacion**: aplicar autorizacion por recurso (ver plan): ownership
  de `profiles`/`batches`/uploads y roles que definan quien ve todo vs solo
  lo propio vs nada del historial.

#### M-2. Contenedor arranca como root para hacer chown del volumen

- **Descripcion**: el `docker-entrypoint.sh` corre como root, hace
  `chown -R nutri:nutri /data` y luego dropea privilegios con `runuser`.
  El proceso Python final corre como `nutri` (correcto), pero el arranque
  como root amplia la superficie si el entrypoint fuera comprometido.
- **Archivo:linea**: `docker-entrypoint.sh:15-27`; `Dockerfile:64-71`.
- **Impacto**: bajo-medio; es un patron comun en Railway por como monta el
  volumen. El riesgo es aceptable pero conviene documentarlo y minimizarlo.
- **Recomendacion**: si Railway permite fijar el owner del volumen, evitar
  el arranque como root. Mantener el drop de privilegios (ya presente) y
  no ejecutar logica de aplicacion como root.

#### M-3. Sin cabeceras de seguridad HTTP

- **Descripcion**: no hay middleware que agregue `Strict-Transport-Security`,
  `X-Content-Type-Options`, `X-Frame-Options`/CSP `frame-ancestors`,
  `Content-Security-Policy`, `Referrer-Policy`, etc. La app responde JSON,
  pero las descargas de archivos y el landing HTML se sirven sin endurecer.
- **Archivo:linea**: `app/api/main.py` (no hay middleware de headers).
- **Impacto**: menor superficie de defensa en profundidad; clickjacking,
  sniffing de MIME, degradacion a HTTP.
- **Recomendacion**: agregar middleware de security headers y HSTS cuando
  se sirva sobre HTTPS. Definir CSP acorde al frontend.

#### M-4. Sin rate limiting ni proteccion anti-abuso

- **Descripcion**: no hay limitacion de tasa en ningun endpoint. Los
  endpoints que llaman a Gemini (`/profiles/draft`, `/refine`) tienen costo
  monetario por request.
- **Archivo:linea**: `app/api/routes/profiles.py:320-372, 461-491`.
- **Impacto**: abuso economico (facturacion de Gemini), fuerza bruta futura
  contra el login que se va a agregar, y DoS.
- **Recomendacion**: rate limiting por IP y por usuario (una vez exista
  auth), con limites mas estrictos en endpoints que gastan LLM.

#### M-5. Manejo silencioso de errores de almacenamiento externo

- **Descripcion**: `_mirror_outputs_to_storage` y las llamadas a Supabase en
  descargas capturan `except Exception: pass`, ocultando fallos de subida.
  Aunque no es una vuln directa, dificulta la deteccion de incidentes.
- **Archivo:linea**: `app/api/routes/batches.py:128-152, 692-702`.
- **Impacto**: fallos de integridad/entrega no observables; contribuye a
  A09:2025 (Security Logging and Alerting Failures).
- **Recomendacion**: registrar (log) el error con contexto en vez de
  silenciarlo; alertar en fallos repetidos.

### BAJO

#### B-1. `.env` local presente en la maquina de desarrollo

- **Descripcion**: existe un `.env` en la raiz local (ignorado por git,
  correcto). El riesgo es de manejo humano (copias, respaldos, pantallas
  compartidas), no del repo.
- **Recomendacion**: rotar claves al cerrar el proyecto (ya documentado en
  `docs/deploy_railway.md:146-153`) y evitar compartir el archivo.

#### B-2. Aprobaciones/rechazos atribuidos a texto libre sin identidad

- **Descripcion**: `runs` y `profiles` guardan `aprobado_por` /
  `rechazado_por` como string arbitrario del cliente ("usuario", "anonimo").
  No hay identidad verificada detras de la aprobacion.
- **Archivo:linea**: `app/api/routes/runs.py:109-145`;
  `app/api/routes/profiles.py:271-274, 494-507`.
- **Impacto**: la trazabilidad de "puntos de control humanos" (requisito de
  negocio de AGENTS.md) no es confiable sin auth.
- **Recomendacion**: derivar el actor del usuario autenticado, no del body.

#### B-3. `apiBaseUrl` de produccion embebido en el bundle del frontend

- **Descripcion**: `environment.prod.ts` fija la URL del backend en el
  build. No es un secreto (una SPA siempre expone su backend), pero
  confirma publicamente el dominio del backend.
- **Archivo:linea**: `frontend/src/environments/environment.prod.ts:11`.
- **Impacto**: informativo; ayuda al reconocimiento. Sin impacto si el
  backend queda protegido.
- **Recomendacion**: sin accion urgente; asegurar que el backend exija auth.

---

## Verificaciones que dieron resultado CORRECTO

- **Inyeccion SQL**: todas las consultas usan parametros (`?`). El unico
  f-string en SQL (`runs.py:50-52`) solo interpola placeholders `?`
  generados por conteo, con los valores pasados como tupla parametrizada:
  no es inyectable. `PRAGMA foreign_keys = ON` activo (`app/core/db.py:197,
  210`).
- **Inyeccion de comandos**: no hay `os.system`, `subprocess` con input del
  usuario, ni `eval`/`exec` sobre datos del usuario.
- **Path traversal en descargas**: los endpoints de descarga validan con
  `resolve()` + `relative_to()` (`batches.py:704-710`,
  `profiles.py:656-659`). Correcto (el problema es la SUBIDA, A-2).
- **Logging de secretos**: no se encontro logging de claves ni de
  contenidos sensibles.
- **Datos reales en el repo**: `.gitignore` excluye `data/`,
  `data_nivel_cumplimiento/`, `FLASH`, `PRE CORTE*`, `homologacion*`.
  Correcto.

---

## Priorizacion recomendada (orden de remediacion)

1. C-1 y C-2 antes de cualquier exposicion publica (bloqueantes de prod).
2. A-2 y A-1 (path traversal + pickle) por el riesgo combinado de RCE.
3. A-3 y A-4 (limites de upload + ocultar docs).
4. M-1..M-5 junto con el diseno de RBAC.
5. B-1..B-3 como higiene.

---

## Fuentes

- OWASP Top 10:2025 - https://owasp.org/Top10/2025/
- OWASP Password Storage Cheat Sheet -
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP CORS / A05 y CWE-942 (Permissive Cross-domain Policy).
- CWE-22 (Path Traversal), CWE-502 (Deserialization), CWE-400 (Resource
  Exhaustion).
