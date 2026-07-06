# Frontend NutriAvicola (Fase 7C.2)

SPA Angular 22 (standalone components + Material 3 + zoneless) para
operar el pipeline PRE CORTE vs FLASH. Consume el backend FastAPI de
las Fases 4-7A. La Fase 7C.2 (2026-07-06) introdujo un rediseño visual
corporativo end-to-end con un sistema de design tokens explicitos.

## Stack

- **Angular 22** con standalone components + signals + zoneless change
 detection + lazy routing.
- **Angular Material 3** con paleta corporativa NutriAvicola (navy + naranja).
- **SCSS puro** (sin Tailwind, sin React).
- **HttpClient** para consumir la API REST del backend.

## Filosofia visual (Fase 7C.2)

El diseño busca transmitir "software empresarial serio": sobrio,
corporativo, B2B. Las referencias mentales son Salesforce Lightning,
Vercel Dashboard (sin la parte glass) y reportes tipo Deloitte /
Bloomberg Terminal moderna. Se evitan de forma explicita los efectos
tipo glassmorphism (nada de `backdrop-filter: blur(...)`, nada de
fondos semi-transparentes), porque en un contexto de cliente B2B avicola
esos efectos restan credibilidad y son estridentes.

Todo el sistema visual esta en [src/styles.scss](src/styles.scss) como
tokens CSS custom properties. Cambiar la paleta o el spacing = editar
una constante; los componentes no llevan hex hardcoded.

### Paleta corporativa

- **Navy** `#0F2E4C` (primary) con 10 shades `--nutri-navy-{50..900}`.
 Se usa para toolbar, headers de tabla, titulos, texto de marca.
- **Naranja** `#E87722` (acento) con 10 shades `--nutri-orange-{50..900}`.
 Se usa como acento puntual (banda lateral de card featured, hover en
 fila total de tablas), jamas como fondo dominante.
- **Grises** 11 shades `--nutri-gray-{25,50,100..900}` para superficies
 (`gray-50` app bg, blanco puro para cards), bordes (`gray-200`) y
 tipografia (`gray-{500,600,700,900}`).
- **Semaforo semantico** con 4 variantes por color (`solid`, `bg`, `fg`,
 `border`): `--nutri-sem-{good,warn,bad,info}-*`. Reservado para KPIs
 de cumplimiento y para el color coding de status/alerts.

### Escalas

- **Spacing** escala 4 px: `--space-{1..16}` = 4 / 8 / 12 / 16 / 20 /
 24 / 28 / 32 / 40 / 48 / 64.
- **Typography** `--fs-{xs,sm,base,md,lg,xl,2xl,3xl}` = 12 / 13 / 14 /
 16 / 20 / 24 / 28 / 32 con line-heights `--lh-{tight,snug,normal,loose}`
 y pesos `--fw-{regular,medium,semibold,bold}`.
- **Radios** moderados: `--radius-{xs,sm,md,lg,xl}` = 3 / 4 / 6 / 8 /
 12. Los pills (badges de status) usan `--radius-pill`.
- **Elevacion** 5 niveles con sombras tintadas al navy corporativo:
 `--elev-{1..5}`.

### Utilitarias globales

`.nutri-page`, `.nutri-page-header`, `.nutri-eyebrow`, `.nutri-card`,
`.nutri-card--featured`, `.nutri-toolbar`, `.nutri-brand`,
`.nutri-badge` (variantes soft), `.nutri-status` (pill con dot),
`.nutri-alert` (banner full-width con icono), `.nutri-kpi` +
`.nutri-kpi-grid`, `.nutri-table` + `.nutri-table-wrap`,
`.nutri-empty`, `.nutri-loading`, `.nutri-actions`. Todas viven en
`styles.scss` y usan solo tokens.

## Rutas

- `/`                                 dashboard con contadores + tabla de batches
- `/batches/nuevo`                    wizard de 5 pasos (nombrar / pre-cortes / flash / preview / generar)
- `/batches/:id`                      detalle del batch (lectura + archivar)
- `/batches/:id/descargas`            lista de archivos generados con descarga individual + ZIP

## Desarrollo local

Prerequisitos: **Node.js 22 LTS** o superior, backend FastAPI corriendo en `http://localhost:8000`.

```powershell
cd frontend
npm install
npm start
```

Abre `http://localhost:4200`. El dev server hace proxy? No — usa el
`environment.ts` que apunta a `http://localhost:8000`. Si el backend
esta en otro host, ajusta `src/environments/environment.ts`.

## Build de produccion

```powershell
npm run build
```

Salida: `dist/frontend/browser/`. Bundle inicial ~100 KB gzipped, lazy
chunks por ruta (~10-45 KB c/u).

## Deploy en Vercel (Fase 7C)

1. Push del repo a GitHub.
2. En [vercel.com](https://vercel.com): `Add New Project` -> selecciona el
   repo -> `Root Directory` = `frontend`.
3. Framework preset: `Other` (Vercel detecta `vercel.json` con SPA rewrite).
4. Env vars: no aplica en runtime (Angular las embebe en build).
5. Para override del `apiBaseUrl` de prod: editar
   `src/environments/environment.prod.ts` y hacer commit.
   Alternativa dinamica: reemplazar `environment.prod.ts` por lectura de
   `window.__ENV__` inyectada por `index.html` desde variables Vercel.

Al hacer push a `main`, Vercel builda y deploya en ~1 min.

## Estructura

```
src/
  app/
    core/
      models.ts             interfaces TS equivalentes a Pydantic
      batches.service.ts    HttpClient wrapper con todos los endpoints
    features/
      dashboard/            dashboard.component.{ts,html,scss}
      batches/
        batch-wizard.component.{ts,html,scss}
        batch-detail.component.{ts,html,scss}
        downloads.component.{ts,html,scss}
    app.{ts,html,scss}      root con toolbar corporativo + <router-outlet>
    app.config.ts           providers globales
    app.routes.ts           rutas con lazy loading
  environments/
    environment.ts          dev (localhost:8000)
    environment.prod.ts     prod (URL Railway)
  assets/logo.jpg           logo NutriAvicola (copia de resources/)
  styles.scss               tema corporativo (paleta + utilitarias)
```

## Tests unitarios

Angular 22 usa el nuevo runner `@angular/build:unit-test` basado en
Vitest. Requiere setup adicional de un browser adapter:

```powershell
npm install --save-dev @vitest/browser-playwright
npx playwright install chromium
npm test -- --watch=false
```

`app.spec.ts` incluye tests base (crea componente, renderiza toolbar).

## Paleta corporativa

Colores definidos en `styles.scss` como CSS variables (`--nutri-*`)
para que los componentes los usen sin hardcodear hex. Ver
[AGENTS.md](../AGENTS.md#fase-7c2---rediseno-visual-corporativo-2026-07-06)
para el sistema completo (paleta ampliada, spacing, tipografia,
elevacion, motion) y como cambiar la marca desde un solo lugar.
