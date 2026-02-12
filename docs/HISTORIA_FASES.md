# Historia del proyecto por fases

Este documento indexa la reconstruccion del historial Git del flujo **PRE CORTE vs FLASH**
(NutriAvicola). Cada fase tiene su nota en `docs/fases/` y un commit dedicado.

## Prerrequisitos

1. Repositorio inicializado en la raiz del proyecto:
   ```powershell
   git init
   git branch -M master   # o main, segun tu remoto
   ```
2. Tener los archivos fuente en la raiz (PRE CORTE, homologacion) para que el
   script copie fixtures en Fase 4.5 y el logo en Fase 7C.
3. Los scripts ya deben existir en `scripts/` (este paquete) aunque aun no
   esten commiteados; el commit **meta** los registra al final.

## Como usar los scripts

Desde la raiz del repositorio (PowerShell en Windows):

```powershell
# Vista previa sin tocar git
.\scripts\git_historia_por_fases.ps1 -DryRun

# Ejecutar todos los commits (repo vacio o con -Force)
.\scripts\git_historia_por_fases.ps1

# Reanudar desde una fase
.\scripts\git_historia_por_fases.ps1 -FromPhase fase-6

# Una sola fase
.\scripts\git_historia_por_fases.ps1 -OnlyPhase fase-4-5

# Fechas actuales en lugar de fechas historicas del manifiesto
.\scripts\git_historia_por_fases.ps1 -SkipDates
```

Equivalente Bash/Linux/macOS:

```bash
chmod +x scripts/git_historia_por_fases.sh
./scripts/git_historia_por_fases.sh --dry-run
./scripts/git_historia_por_fases.sh
```

El manifiesto canonico esta en [`scripts/git_historia_manifest.json`](../scripts/git_historia_manifest.json).

## Advertencia sobre historial sintetico

El codigo actual ya incorpora **todas** las fases completadas. Los commits generados
**no reconstruyen diffs incrementales reales**; agrupan archivos por fase para dejar
constancia auditable de *que se entrego en cada etapa*. Los archivos que evolucionaron
en varias fases (por ejemplo `loaders.py`) aparecen en el commit de la fase donde
se introdujo o se modifico por ultima vez segun el manifiesto.

Archivos **excluidos** a proposito (datos operativos en la raiz, temporales de Office,
`data/`, secretos `.env`, cache de Angular, etc.): ver `exclude_globs` en el manifiesto.

## Mapa de fases

| Fase | Estado | Fecha ref. | Documentacion | Mensaje de commit |
|------|--------|------------|---------------|-------------------|
| init | hecho | 2026-02-12 | (este archivo) | Inicializar repo |
| 0 | hecho | 2026-02-13 | [00-exploracion](fases/00-exploracion.md) | Fix fecha notebook |
| 1 | hecho | 2026-02-14 | [01-core-python](fases/01-core-python.md) | Core matching |
| 2 | hecho | 2026-02-18 | [02-validadores-logging](fases/02-validadores-logging.md) | Validadores + logs |
| 3 | hecho | 2026-02-21 | [03-historico-sqlite](fases/03-historico-sqlite.md) | SQLite idempotente |
| 4 | hecho | 2026-07-02 | [04-api-fastapi](fases/04-api-fastapi.md) | FastAPI pipeline |
| 4.5 | hecho | 2026-07-02 | [04-5-resumen-catalogo](fases/04-5-resumen-catalogo.md) | RESUMEN + SKU |
| 5 | **pendiente** | — | [05-streamlit-pendiente](fases/05-streamlit-pendiente.md) | Streamlit UI |
| 6 | hecho | 2026-07-06 | [06-export-excel](fases/06-export-excel.md) | Excel KPI |
| 6.5 | hecho | 2026-07-06 | [06-5-excel-multi-fecha](fases/06-5-excel-multi-fecha.md) | Multi-fecha |
| 6.6 | hecho | 2026-07-06 | [06-6-calendario-laboral](fases/06-6-calendario-laboral.md) | Calendario CO |
| 7A | hecho | 2026-07-06 | [07A-batch-modelo](fases/07A-batch-modelo.md) | Modelo Batch |
| 7B | hecho | 2026-07-06 | [07B-docker-railway](fases/07B-docker-railway.md) | Docker/Railway |
| 7C | hecho | 2026-07-06 | [07C-frontend-angular](fases/07C-frontend-angular.md) | Angular 22 |
| 7D | **pendiente** | — | [07D-n8n-pendiente](fases/07D-n8n-pendiente.md) | n8n |
| 7E | **pendiente** | — | [07E-supabase-pendiente](fases/07E-supabase-pendiente.md) | Supabase Storage |
| meta | hecho | 2026-07-06 | AGENTS.md | Memoria + scripts |

## Checklist antes de pushear

1. Ejecutar tests: `pytest` (con `requirements-dev.txt` instalado).
2. Verificar que no queden archivos sensibles: `git status`, `git log --oneline`.
3. Configurar remoto: `git remote add origin <url>`.
4. Push inicial: `git push -u origin master` (o `main` segun convencion del remoto).

## Referencia completa

La memoria operativa del proyecto vive en [`AGENTS.md`](../AGENTS.md) en la raiz.
