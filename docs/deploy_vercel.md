# Deploy del frontend en Vercel (Fase 7C)

## Prerequisitos

- Backend FastAPI ya deployado en Railway (Fase 7B) con dominio publico
  (ej: `https://nutriavicola-api.up.railway.app`).
- CORS del backend permite el dominio Vercel: en Railway env vars,
  `NUTRI_CORS_ORIGINS=https://<tu-app>.vercel.app,https://*.vercel.app`.
- Cuenta Vercel (free tier suficiente para SPA estatico).

## Paso 1: apuntar `environment.prod.ts` al Railway URL

Editar [frontend/src/environments/environment.prod.ts](../frontend/src/environments/environment.prod.ts):

```typescript
export const environment = {
  production: true,
  apiBaseUrl: 'https://nutriavicola-api.up.railway.app',
  n8nWebhookMatch: '',  // opcional Fase 7D
};
```

Commit + push.

## Paso 2: crear proyecto en Vercel

1. Login en [vercel.com](https://vercel.com) con GitHub.
2. `Add New` -> `Project` -> selecciona el repo.
3. **Root Directory**: `frontend` (no la raiz).
4. **Framework Preset**: `Other`.
5. Build command / output se leen de [frontend/vercel.json](../frontend/vercel.json):
   - `buildCommand`: `npm run build -- --configuration production`
   - `outputDirectory`: `dist/frontend/browser`
   - `installCommand`: `npm ci`
   - `rewrites`: SPA rewrite `/((?!assets/|.*\.[a-zA-Z0-9]+$).*)` -> `/index.html`
     (excluye `assets/**` y cualquier ruta con extension `.js/.css/.jpg/etc`
     para que los bundles y assets se sirvan como archivo real y no como
     index.html).
   - `headers`: `Cache-Control: public, max-age=31536000, immutable` para
     hashed assets; `no-cache` para `index.html` (evita que el navegador
     sirva un HTML viejo apuntando a bundles ya borrados).
6. `Deploy`.

Primera compilacion: ~1-2 min. Genera dominio `https://<proyecto>.vercel.app`.

## Paso 3: actualizar CORS del backend

En Railway del backend, ve a la pestaña **Variables** de tu servicio y ajusta el valor de `NUTRI_CORS_ORIGINS` para habilitar el acceso a tu dominio y a los entornos de pruebas (previews) de Vercel. 

Usa exactamente esta línea (puedes copiarla y pegarla directamente):

```
NUTRI_CORS_ORIGINS=https://cumplimientoplataforma.vercel.app,https://cumplimiento-plataforma-*.vercel.app,https://cumplimientoplataforma-*.vercel.app
```

> **Nota técnica**: Vercel reemplaza los guiones bajos (`_`) por guiones medios (`-`) en las URLs generadas para las ramas (previews). El patrón `cumplimiento-plataforma-*.vercel.app` cubre esos entornos de pruebas de forma automática para que tus pruebas en otras ramas no fallen por CORS.

Restart del servicio de Railway (o Railway lo aplicará automáticamente al guardar las variables).

## Paso 4: verificacion

Abrir el dominio Vercel:

- Debe verse el toolbar con logo NutriAvicola.
- Dashboard carga lista de batches (si esta vacia, mensaje "No hay batches").
- Wizard: crear batch, subir pre_corte y flash, ver preview, generar.
- Descargas: los enlaces deben pegarle al Railway URL con signed download.

## Rollback

Vercel guarda todos los deploys. Si algo se rompe:

- `Deployments` -> selecciona el deploy anterior -> `Promote to Production`.

## Preview branches

Cada push a un branch != `main` genera un deploy preview con URL propia.
Ideal para probar cambios sin afectar produccion. Vercel comenta el
enlace en el PR de GitHub.

## Costos

- Vercel Hobby: gratis (100 GB bandwidth/mes, mas que suficiente para SPA
  corporativa interna).
- Si vas a comercial pasa a Pro (20 USD/mes por miembro).

## Troubleshooting: pagina en blanco

Sintoma: abres el sitio y el navegador muestra fondo blanco vacio, sin
toolbar, sin logo, sin errores obvios. Verifica en orden:

### 1. La URL es un **preview** deployment protegido

Los URLs con hash del deploy (ej.
`https://cumplimientoplataforma-es4plrn1p-byzocars-projects.vercel.app/`)
tienen **Deployment Protection** activada por default. Un usuario no
autenticado en Vercel recibe la pagina de login de Vercel — no la SPA. En
algunos navegadores esta pagina puede tardar en cargar y verse en blanco
mientras se hidrata.

Verificacion rapida:
```powershell
curl.exe -sSL -o preview.html https://<preview-hash>.vercel.app/
Select-String -Path preview.html -Pattern '_next/static|zeit-theme|KPSDK'
```
Si aparecen matches con `_next/static` o `zeit-theme`, es la pagina de
Vercel, no tu app.

Soluciones:
- **Usar el dominio de produccion** (`cumplimientoplataforma.vercel.app`).
  Es publico y sirve la SPA sin autenticacion.
- **Deshabilitar Deployment Protection** en Vercel dashboard: `Project
  Settings` -> `Deployment Protection` -> desactivar "Vercel
  Authentication" o cambiar a "Only Production Deployments Protected".

### 2. `apiBaseUrl` sin `https://`

`environment.prod.ts` DEBE tener el esquema. Sin el, Angular HttpClient
trata el string como path relativo y todas las requests van al propio
dominio Vercel, que rebota el `index.html` en vez de JSON. El
`JSON.parse` explota, el componente rompe, y solo queda el toolbar (o ni
eso si el error ocurre durante bootstrap).

Verificacion:
```powershell
Select-String -Path frontend\src\environments\environment.prod.ts `
              -Pattern "apiBaseUrl"
```
Debe verse `apiBaseUrl: 'https://<algo>.up.railway.app'`. Si le falta el
`https://`, agregarlo, commit + push.

### 3. Rewrite de `vercel.json` sirve HTML donde debe servir JS

Bug clasico de SPA en Vercel: un rewrite `/(.*)` -> `/index.html` sin
exclusiones captura tambien `/main-XXX.js` y `/styles-XXX.css`, y el
navegador recibe HTML donde espera JavaScript / CSS. La consola muestra
`Uncaught SyntaxError: Unexpected token '<'` y la app no arranca.

Vercel v2 hace filesystem-first (sirve el archivo real si existe), pero
solo cuando el archivo existe. Si el navegador tiene cacheado un hash
viejo de bundle (`main-OLDHASH.js`) y ese archivo ya no esta en el
output, Vercel aplica el rewrite y devuelve `index.html` con
`Content-Type: text/html`. Rompe en produccion cada vez que se redeploya
y el usuario tiene la app abierta.

Verificacion:
```powershell
curl.exe -sI https://<tu-dominio>/main-INEXISTENTE.js
```
Si el `Content-Type` es `text/html` en vez de `application/javascript`,
el rewrite esta capturando estaticos. La configuracion correcta usa
lookahead negativo para excluir extensiones:
```json
"rewrites": [
  { "source": "/((?!assets/|.*\\.[a-zA-Z0-9]+$).*)", "destination": "/index.html" }
]
```
Y ademas, `headers` con `Cache-Control: no-cache` sobre `index.html`
evita que el navegador cachee el HTML apuntando a hashes viejos.

### 4. Assets con path relativo dentro de rutas anidadas

`<img src="assets/logo.jpg">` (sin `/` inicial) resuelve **relativo al
URL actual**. Si el usuario esta en `/batches/nuevo`, el navegador pide
`/batches/nuevo/assets/logo.jpg` -> 404 -> logo roto. Aunque
`<base href="/">` esta declarado en `index.html`, el navegador usa esa
base solo para relative URLs sin resolver antes por current path.

Verificacion:
```powershell
Select-String -Path frontend\src\app\*.html,frontend\src\app\**\*.html `
              -Pattern 'src="assets/'
```
Cualquier match debe cambiarse a `src="/assets/..."`.

### 5. CORS del backend rechaza el dominio Vercel

Sintoma distinto pero facil de confundir: la app renderiza (toolbar,
logo) pero el dashboard muestra "Error" o queda cargando indefinidamente.
En devtools: `Access-Control-Allow-Origin` missing o incorrect.

Fix: actualizar `NUTRI_CORS_ORIGINS` en Railway con el dominio Vercel
(ver Paso 3 arriba).

### Checklist rapido para el usuario

Cuando el sitio se ve en blanco, abrir devtools (F12) del navegador:

1. **Console tab** — hay errores rojos? Si dice
   `Uncaught SyntaxError: Unexpected token '<'` -> problema #3 arriba.
   Si dice `Failed to fetch` / `CORS` -> problema #5. Si no dice nada
   pero el DOM esta vacio -> problema #1 (Deployment Protection).
2. **Network tab** — `main-*.js` responde 200 con `Content-Type:
   application/javascript`? Si es `text/html` -> problema #3. Si es 404
   -> hash viejo cacheado, hard refresh (Ctrl+Shift+R).
3. **Elements tab** — hay `<app-root>` en el DOM? Si esta vacio -> Angular
   no bootstrapeo, ver Console.

