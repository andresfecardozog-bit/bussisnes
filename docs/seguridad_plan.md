# Plan de arquitectura de seguridad (para aprobacion del dueno)

Fecha: 2026-07-09. Estado: PROPUESTA, sin implementar. Requiere aprobacion
del dueno del producto antes de escribir codigo.

Objetivo: incorporar autenticacion y autorizacion por primera vez a la
plataforma multiagente de cruces, cumpliendo los cinco requisitos del dueno:
login obligatorio para todo, cifrado en transito y en reposo donde aplique,
un admin inicial configurable por `ADMIN_EMAIL`, RBAC con permisos
granulares, y cero puertas traseras.

Este documento acompana a `docs/seguridad_auditoria.md` (hallazgos del
estado actual). Marco: OWASP ASVS y OWASP Top 10:2025.

Restriccion de fase: NO tocar codigo de `app/` ni `frontend/` todavia (hay
un rediseno de frontend en curso). Aqui solo se disena y se define el
roadmap.

---

## 1. Decision de arquitectura de autenticacion

### Recomendacion: sesion con cookie httpOnly + SameSite (server-side sessions)

Para una SPA Angular servida en Vercel + API FastAPI en Railway,
**recomendamos sesiones opacas guardadas server-side, transportadas en una
cookie `HttpOnly`, `Secure`, `SameSite`**, por encima de JWT en
`localStorage`.

Justificacion:

- **Proteccion contra XSS**: una cookie `HttpOnly` no es accesible desde
  JavaScript. Un JWT en `localStorage` es legible por cualquier script y
  cualquier XSS lo roba. Dado que el frontend renderiza datos de negocio y
  esta en rediseno, minimizar el impacto de un XSS es prioritario.
- **Revocacion inmediata**: al ser sesiones opacas en base de datos, el
  admin puede cerrar/invalidar una sesion al instante (logout forzado,
  baja de usuario, cambio de rol). Un JWT autocontenido sigue valido hasta
  que expira, salvo que se monte una lista de revocacion (que reintroduce
  estado server-side, anulando la supuesta ventaja del JWT).
- **Escala suficiente**: el volumen de usuarios es un equipo de BI interno
  (decenas, no millones). No hay necesidad real de tokens stateless para
  escalar horizontalmente; SQLite/Postgres soportan de sobra la tabla de
  sesiones. Si en el futuro se distribuye el backend, la tabla de sesiones
  migra a Postgres/Redis sin cambiar el modelo.
- **Simplicidad de CSRF controlable**: con `SameSite=Lax`/`Strict` mas un
  token anti-CSRF por peticion mutante, el riesgo CSRF queda cubierto (ver
  seccion 8).

Compromiso: la cookie exige que frontend y backend compartan un contexto de
dominio compatible para cookies cross-site. Como el frontend esta en Vercel
(`*.vercel.app`) y el backend en Railway (`*.up.railway.app`), la cookie
sera cross-site y debe emitirse con `SameSite=None; Secure` y CORS con
`allow_credentials=True` restringido a la lista blanca EXACTA de origenes
(nunca `*`). Alternativa preferible a mediano plazo: exponer ambos bajo un
mismo dominio propio (p. ej. `app.nutriavicola.com` y
`api.nutriavicola.com`) para poder usar `SameSite=Lax` con cookie de
dominio padre. Esta es una **decision abierta** (ver seccion 11).

Si el dueno prefiere JWT (por ejemplo por integraciones maquina-a-maquina
como n8n), la alternativa aceptable es: access token corto (5-15 min) +
refresh token rotatorio en cookie `HttpOnly`, con lista de revocacion de
refresh en base de datos. Es mas piezas y mas superficie; solo lo
recomendamos si aparece un requisito real de clientes no-navegador.

### Hashing de contrasenas: Argon2id

- Algoritmo: **Argon2id** (recomendacion por defecto de OWASP y ganador de
  la Password Hashing Competition; resiste ataques de canal lateral que
  scrypt no).
- Parametros base (OWASP 2025): `m = 19456 KiB (19 MiB)`, `t = 2`,
  `p = 1`. Ajustar `m`/`t` en el hardware de produccion para que un verify
  tome 100-500 ms; mantener `p = 1` en web.
- Libreria: `argon2-cffi` (Python). Cada hash embebe sus parametros y su
  salt aleatorio unico, lo que permite rehash transparente al subir el
  costo en el futuro.
- Fallback aceptable si Argon2id no estuviera disponible: bcrypt con work
  factor >= 12 y limite de 72 bytes. No es la opcion recomendada.
- Nunca almacenar la contrasena en claro ni cifrada reversible; solo el
  hash. Considerar un "pepper" (secreto en env var) como defensa en
  profundidad opcional.

### Rotacion y expiracion de sesion

- Vida de sesion: inactividad 30-60 min (sliding) + tope absoluto de 8-12 h.
- Rotar el identificador de sesion al iniciar sesion (previene fijacion de
  sesion) y al elevar privilegios.
- Logout invalida la sesion en base de datos (borrado del registro).
- Password rotativa del admin inicial: **forzar cambio en el primer login**
  (ver seccion 3).
- Bloqueo temporal / backoff tras N intentos fallidos de login por
  usuario/IP.

---

## 2. Esquema de datos: usuarios, roles y permisos (RBAC)

Modelo RBAC clasico normalizado, disenado para crecer (roles y permisos se
agregan sin cambiar el codigo de autorizacion). Compatible con SQLite hoy
y con Postgres despues.

### Tablas

```
users
  id                INTEGER PK
  email             TEXT UNIQUE NOT NULL        -- identidad de login
  password_hash     TEXT NOT NULL               -- Argon2id (embebe params+salt)
  full_name         TEXT
  is_active         INTEGER NOT NULL DEFAULT 1  -- baja logica, no borrado
  must_change_pwd   INTEGER NOT NULL DEFAULT 0  -- fuerza cambio primer login
  failed_attempts   INTEGER NOT NULL DEFAULT 0
  locked_until      TEXT                        -- ISO datetime o NULL
  created_at        TEXT NOT NULL
  updated_at        TEXT NOT NULL

roles
  id                INTEGER PK
  code              TEXT UNIQUE NOT NULL         -- 'admin', 'analista_todos', ...
  nombre            TEXT NOT NULL
  descripcion       TEXT

permissions
  id                INTEGER PK
  code              TEXT UNIQUE NOT NULL         -- 'files:read:all', 'users:manage', ...
  descripcion       TEXT

role_permissions                                 -- N:M rol <-> permiso
  role_id           INTEGER FK -> roles(id)
  permission_id     INTEGER FK -> permissions(id)
  PRIMARY KEY (role_id, permission_id)

user_roles                                       -- N:M usuario <-> rol (permite crecer)
  user_id           INTEGER FK -> users(id)
  role_id           INTEGER FK -> roles(id)
  PRIMARY KEY (user_id, role_id)

sessions
  id                TEXT PK                       -- token opaco (secrets.token_urlsafe)
  user_id           INTEGER FK -> users(id)
  created_at        TEXT NOT NULL
  last_seen_at      TEXT NOT NULL
  expires_at        TEXT NOT NULL
  ip                TEXT
  user_agent        TEXT
  revoked_at        TEXT                          -- NULL = activa

audit_log                                         -- A09:2025
  id                INTEGER PK
  ts                TEXT NOT NULL
  user_id           INTEGER                       -- actor (NULL si anonimo)
  action            TEXT NOT NULL                 -- 'login', 'upload', 'download', ...
  resource_type     TEXT
  resource_id       TEXT
  outcome           TEXT NOT NULL                 -- 'ok' | 'denied' | 'error'
  ip                TEXT
  detail            TEXT                          -- JSON breve, sin secretos
```

### Ownership de recursos existentes

Para las reglas "ver todos" vs "solo los propios" se agrega columna de
propietario a los recursos que hoy no la tienen:

- `profiles`: agregar `owner_user_id INTEGER FK -> users(id)`.
- `batches`: agregar `owner_user_id INTEGER FK -> users(id)`.
- `cargas` (uploads): agregar `uploaded_by_user_id INTEGER FK -> users(id)`.
- `runs`: heredan owner del profile/batch al que pertenecen.

Migracion: al introducir auth, los registros historicos sin owner se
asignan al admin (o a un usuario "sistema") para no romper listados.

### Roles minimos (semilla) y su mapeo a permisos

| Rol (code)          | Descripcion                                   | Permisos clave |
|---------------------|-----------------------------------------------|----------------|
| `admin`             | Todo, incl. gestion de usuarios y permisos    | `*` (todos) |
| `analista_todos`    | Ve TODOS los archivos/procesos de la plataforma | `files:read:all`, `profiles:read:all`, `batches:read:all`, `run:execute`, `download:all` |
| `analista_propios`  | Ve solo lo que el mismo subio                 | `files:read:own`, `profiles:read:own`, `batches:read:own`, `run:execute:own`, `download:own` |
| `sin_historial`     | Puede operar cruces pero sin acceso al historial acumulado | `run:execute:own` (sin `*:read:*` sobre historial) |

Permisos granulares (catalogo inicial, ampliable): `users:manage`,
`roles:manage`, `files:upload`, `files:read:own`, `files:read:all`,
`profiles:read:own`, `profiles:read:all`, `profiles:write`,
`profiles:approve`, `batches:read:own`, `batches:read:all`,
`batches:write`, `run:execute`, `download:own`, `download:all`,
`audit:read`. El modelo N:M permite crear nuevos roles combinando permisos
sin tocar codigo.

### Bootstrap del admin inicial

- El correo del admin se lee de la variable de entorno **`ADMIN_EMAIL`**
  (placeholder; el dueno la define en Railway/`.env`, nunca en el repo).
- Contrasena inicial: generada aleatoriamente al primer arranque y mostrada
  UNA vez en el log de despliegue (o definida por `ADMIN_INITIAL_PASSWORD`
  en env var y luego rotada). El usuario admin se crea con
  `must_change_pwd = 1`, de modo que el primer login obliga a cambiarla.
- Idempotencia: si ya existe un usuario con `ADMIN_EMAIL`, no se recrea ni
  se resetea su contrasena (evita puerta trasera por re-seed).
- El admin arranca con el rol `admin` (todos los permisos). No se hardcodea
  ninguna credencial en el codigo (cumple "cero puertas traseras").

---

## 3. Proteccion de TODOS los endpoints (backend)

Principio: **seguro por defecto**. Un endpoint nuevo debe nacer protegido
sin que el desarrollador tenga que acordarse de anadir auth.

### Mecanismo

- Aplicar una dependencia de autenticacion a nivel de aplicacion/router,
  no endpoint por endpoint. En FastAPI: `app.include_router(router,
  dependencies=[Depends(require_auth)])` para todos los routers de negocio,
  o un `Depends` global via `FastAPI(dependencies=[...])`.
- Lista blanca explicita de rutas publicas (unicas sin auth): `POST
  /auth/login`, `GET /health`, y el endpoint de cambio de contrasena
  forzado. Todo lo demas exige sesion valida.
- `require_auth`: lee la cookie de sesion, valida contra `sessions`
  (existe, no revocada, no expirada), refresca `last_seen_at`, carga el
  `User` + sus roles/permisos y lo inyecta en el request.
- Autorizacion: dependencias componibles tipo `require_permission("files:
  read:all")` y helpers de ownership (seccion 4). Devuelven 401 si no hay
  sesion, 403 si hay sesion pero sin permiso.
- Deshabilitar `/docs`, `/redoc`, `/openapi.json` en produccion o
  protegerlos tras rol admin (cierra A-4 de la auditoria).
- Corregir CORS: quitar el `+ ["*"]`, dejar solo la lista blanca de
  `NUTRI_CORS_ORIGINS`, con `allow_credentials=True` y metodos/headers
  acotados (cierra C-2).

### Frontend Angular (guards + credenciales)

- **Route guards** (`CanActivate`/`CanMatch`) que verifican sesion antes de
  entrar a cualquier ruta protegida; redirigen a `/login` si no hay sesion.
  Guards adicionales por permiso para rutas de admin y de historial.
- **HTTP interceptor** que envia `withCredentials: true` en todas las
  peticiones (para que el navegador adjunte la cookie de sesion) y adjunta
  el token anti-CSRF en las mutaciones. Hoy `app.config.ts` usa
  `provideHttpClient()` sin interceptor y sin `withCredentials`: se debe
  agregar.
- Manejo de 401 en el interceptor: limpiar estado de sesion en el cliente y
  redirigir a login.
- La UI oculta/inhabilita acciones segun permisos, pero la autorizacion
  real SIEMPRE se valida en el backend (la UI es conveniencia, no control
  de seguridad).

---

## 4. Autorizacion por recurso (todos / propios / sin historial)

La regla se aplica en DOS capas: filtrado en las consultas (backend) y
ocultamiento en la UI (frontend). El backend es la fuente de verdad.

- **analista_todos**: los listados (`GET /profiles`, `/batches`, `/runs`,
  `/catalog`, descargas) devuelven todos los registros. Permiso
  `*:read:all`.
- **analista_propios**: los mismos listados se filtran por
  `owner_user_id == usuario_actual`. El acceso directo por id a un recurso
  ajeno devuelve 403 (no 404, para no filtrar existencia; o 404 si se
  prefiere ocultar existencia; decision abierta menor). Permiso
  `*:read:own`.
- **sin_historial**: no tiene ningun permiso `*:read:*` sobre el historial;
  puede ejecutar cruces sobre lo que sube en su sesion pero no listar ni
  descargar el acumulado. Los endpoints de listado le devuelven 403.
- Enforcement concreto:
  - Endpoints de listado: aplicar filtro por owner segun permiso.
  - Endpoints por id / descarga: dependencia que carga el recurso, resuelve
    su owner y compara contra permisos (`read:all` pasa siempre;
    `read:own` exige coincidencia de owner).
  - Descargas (`/profiles/{id}/downloads/{filename}`,
    `/batches/{id}/downloads/{filename}`): ademas de la validacion de path
    traversal ya existente, verificar autorizacion sobre el batch/profile
    dueno del archivo.
- Toda decision de acceso denegado se registra en `audit_log` con
  `outcome='denied'`.

---

## 5. Cifrado (transito y reposo)

### En transito

- TLS obligatorio extremo a extremo. Railway y Vercel terminan TLS por
  defecto; forzar HTTPS y activar HSTS (seccion 8).
- Cookies de sesion siempre `Secure` (solo viajan por HTTPS).
- Rechazar/redirigir cualquier acceso HTTP plano.

### En reposo

- **Contrasenas**: Argon2id (seccion 1). Nunca reversible.
- **Tokens de sesion**: se guarda el token opaco tal cual (o su hash) en
  `sessions`; al ser aleatorio de alta entropia y revocable, el riesgo es
  acotado. Recomendado guardar solo el hash del token de sesion para que un
  volcado de base no permita secuestrar sesiones vivas.
- **Uploads y outputs**: viven en el volumen `/data` de Railway o en
  Supabase Storage (bucket privado). El cifrado de disco lo provee la
  plataforma. Para datos especialmente sensibles se puede anadir cifrado a
  nivel de aplicacion (AES-GCM con clave en env var), pero se evalua contra
  el costo operativo; **decision abierta** segun clasificacion de datos.
- **Reemplazar pickle por parquet/feather** (cierra A-1): elimina el vector
  de RCE por deserializacion en el almacenamiento en reposo.
- **Secretos**: solo en variables de entorno (`ADMIN_EMAIL`,
  `SESSION_SECRET`/pepper, `GEMINI_API_KEY`, `SUPABASE_*`). Nunca en el
  repo. El `.gitignore` ya cubre `.env`, `*.pem`, `*.key`. Rotacion
  documentada en `docs/deploy_railway.md`.

### Que NO se envia al LLM (Gemini)

- Regla ya vigente en AGENTS.md: al LLM solo van METADATOS + MUESTRAS
  acotadas de los archivos (ver `app/agents/file_probe.py`: `samples`
  truncados, `sample_rows[:5]`), nunca el archivo completo ni la base de
  datos acumulada.
- Reforzar: nunca enviar al LLM contrasenas, tokens, `audit_log`, PII
  completa (listas de NITs/clientes), ni credenciales. El envio de archivo
  completo, si algun dia se necesita, sera una escalacion explicita y
  aprobada, nunca por defecto.
- La API de Gemini es de pago sin retencion para entrenamiento (ya
  documentado). Mantener esa condicion contractual.

---

## 6. Endurecimiento adicional

- **Rate limiting**: por IP y por usuario. Limites estrictos en `/auth/login`
  (anti fuerza bruta) y en endpoints que gastan Gemini
  (`/profiles/draft`, `/refine`). Cierra M-4.
- **Cabeceras de seguridad** (middleware): `Strict-Transport-Security`
  (HSTS), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` o CSP
  `frame-ancestors 'none'`, `Content-Security-Policy` acorde al frontend,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store` en respuestas
  con datos sensibles. Cierra M-3.
- **Validacion y limite de uploads**: tamano maximo por archivo y por
  request, numero maximo de archivos, validacion de magic bytes (no solo
  extension), y proteccion anti zip-bomb (limite de tamano descomprimido y
  de entradas). Sanitizar nombre con `Path(name).name` + generar nombre de
  servidor. Cierra A-2 y A-3.
- **CSRF**: con cookies de sesion, exigir token anti-CSRF en toda peticion
  mutante (POST/PUT/PATCH/DELETE). Patron double-submit cookie o token por
  sesion validado server-side. Con `SameSite=Strict/Lax` como capa extra.
  Si se elige el modelo JWT-en-header, CSRF no aplica igual (pero se pierde
  la proteccion XSS de la cookie httpOnly). Cierra CWE-352.
- **Auditoria/logs de acceso**: registrar login/logout, accesos denegados,
  subidas, descargas, ejecuciones, cambios de rol y de usuario en
  `audit_log`, sin volcar secretos. Reemplazar los `except Exception: pass`
  silenciosos por logging con contexto (cierra M-5). Cubre A09:2025.
- **Contenedor**: mantener el proceso Python como usuario `nutri` (ya
  ocurre). Evaluar reducir el arranque como root (M-2).

---

## 7. Roadmap de implementacion por bloques

Cada bloque tiene criterios de aceptacion verificables. No se pasa al
siguiente sin cumplir el anterior. La suite existente (238+ tests) debe
seguir verde en cada bloque (regresion PRE CORTE intacta).

### Bloque 0 - Correcciones inmediatas de la auditoria (sin auth aun)
- Quitar `+ ["*"]` de CORS; dejar lista blanca (C-2).
- Sanitizar nombres de upload + validar path (A-2).
- Reemplazar pickle por parquet/feather (A-1).
- Limites de tamano/numero en uploads y ZIP (A-3).
- Deshabilitar `/docs` y `/openapi.json` en prod (A-4).
- Aceptacion: pruebas que demuestren rechazo de traversal, rechazo de
  archivo sobredimensionado, CORS que no refleja origenes fuera de lista,
  y que no queda pickle en el path de datos.

### Bloque 1 - Esquema y bootstrap de identidad
- Tablas `users/roles/permissions/role_permissions/user_roles/sessions/
  audit_log` + columnas de owner en `profiles/batches/cargas`.
- Seed idempotente de roles/permisos y del admin via `ADMIN_EMAIL` con
  `must_change_pwd=1`.
- Hashing Argon2id con parametros OWASP.
- Aceptacion: crear admin desde env var; verificar que re-arrancar no
  resetea su contrasena; hash Argon2id verificable; migracion asigna owner
  a historico.

### Bloque 2 - Autenticacion (login/logout/sesion)
- `POST /auth/login`, `POST /auth/logout`, `POST /auth/change-password`,
  `GET /auth/me`.
- Cookie `HttpOnly; Secure; SameSite`; rotacion de id de sesion en login;
  expiracion sliding + absoluta; bloqueo por intentos fallidos.
- Aceptacion: login valido crea sesion; credenciales malas devuelven 401 y
  suman intento; logout revoca; primer login del admin exige cambio de
  contrasena.

### Bloque 3 - Autorizacion global (todo protegido)
- Dependencia de auth global; lista blanca minima de rutas publicas.
- `require_permission` + helpers de ownership.
- Aceptacion: TODO endpoint de negocio devuelve 401 sin sesion; test
  automatizado que recorre el `openapi.json` y falla si algun endpoint (que
  no este en la whitelist) responde distinto de 401 sin credenciales
  (checklist "sin puertas traseras", seccion 9).

### Bloque 4 - Autorizacion por recurso (RBAC granular)
- Filtrado por owner en listados; 403 por acceso a recurso ajeno;
  `sin_historial` sin acceso al acumulado.
- Aceptacion: matriz de pruebas rol x endpoint (admin ve todo,
  analista_todos ve todo, analista_propios solo lo suyo, sin_historial sin
  historial), incluyendo descargas.

### Bloque 5 - Frontend (guards + interceptor + login UI)
- Pantalla de login + cambio de contrasena forzado.
- Guards por sesion y por permiso; interceptor con `withCredentials` +
  CSRF + manejo de 401.
- UI que oculta acciones sin permiso (con enforcement real en backend).
- Aceptacion: rutas protegidas inaccesibles sin sesion; acciones no
  permitidas ocultas y ademas rechazadas por el backend.

### Bloque 6 - Endurecimiento
- Rate limiting, cabeceras de seguridad/HSTS, CSRF completo, auditoria de
  accesos, logging no silencioso.
- Aceptacion: headers presentes en respuestas; login limitado por tasa;
  eventos clave en `audit_log`.

### Bloque 7 - Gestion de usuarios (admin)
- CRUD de usuarios y asignacion de roles desde la UI admin; baja logica.
- Aceptacion: el admin crea/inactiva usuarios y asigna roles; un usuario
  inactivo no puede iniciar sesion; cambios quedan auditados.

---

## 8. Checklist "sin puertas traseras"

- [ ] Ninguna credencial (usuario, contrasena, API key, token) hardcodeada
      en codigo, tests o config versionada.
- [ ] Admin creado solo via `ADMIN_EMAIL`; seed idempotente que no resetea
      contrasenas existentes.
- [ ] Todos los endpoints exigen sesion salvo la whitelist minima
      (`/auth/login`, `/health`, cambio de contrasena forzado), verificado
      por un test que recorre `openapi.json`.
- [ ] Sin endpoints de debug/admin ocultos ni flags que salten la auth.
- [ ] Sin `eval`/`exec`/`pickle.load` sobre datos controlables por el
      usuario.
- [ ] CORS sin `*`; cookies `HttpOnly; Secure; SameSite`.
- [ ] `/docs` y `/openapi.json` cerrados o protegidos en produccion.
- [ ] Autorizacion enforced en backend, no solo en UI.
- [ ] Secretos solo en env vars; `.env`, `*.pem`, `*.key` en `.gitignore`.
- [ ] Toda decision de acceso denegado y toda accion sensible quedan en
      `audit_log`.
- [ ] Rotacion de secretos documentada y ejecutada al cierre del proyecto.

---

## 9. Riesgos y decisiones abiertas (requieren respuesta del dueno)

1. **Modelo de sesion cross-site vs dominio propio.** Con frontend en
   Vercel y backend en Railway, la cookie sera `SameSite=None; Secure`
   (cross-site). Recomendamos, a mediano plazo, unificar bajo un dominio
   propio (`app.` / `api.nutriavicola.com`) para usar `SameSite=Lax`.
   Decision del dueno: comprar/configurar dominio propio ahora, o aceptar
   cookie cross-site con la lista blanca CORS estricta.

2. **Sesion server-side (recomendado) vs JWT con refresh.** Recomendamos
   sesiones. Si el dueno preve integraciones maquina-a-maquina (n8n u otros
   clientes no navegador que necesiten autenticarse), habria que decidir un
   esquema de API keys/tokens de servicio ademas del login humano.

3. **Alcance del cifrado en reposo de uploads.** Railway/Supabase ya cifran
   el disco. Decidir si algun subconjunto de datos (p. ej. archivos con PII
   de clientes) requiere cifrado adicional a nivel de aplicacion, con el
   costo operativo de gestion de llaves que implica.

Decisiones abiertas menores (se pueden resolver en implementacion):
politica de contrasenas (longitud/complejidad), 401 vs 404 para recursos
ajenos, y si se habilita MFA para el admin en una fase posterior.
