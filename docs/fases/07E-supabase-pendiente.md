# Fase 7E — Supabase Storage integrado (PENDIENTE)

**Estado:** pendiente

## Alcance planeado

Cablear uploads y downloads de batches para que los blobs viajen a Supabase
Storage cuando `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` esten configuradas.

## Componentes existentes (sin cablear del todo)

| Pieza | Estado |
|-------|--------|
| `SupabaseStorage` en `storage_adapter.py` | Implementado |
| `scripts/setup_supabase.py` | Crea buckets `uploads`/`outputs` |
| Endpoints `/batches/*/pre-cortes`, downloads | Escriben solo local hoy |

## Trabajo pendiente

- [ ] Upload PRE CORTE / FLASH -> bucket `uploads`.
- [ ] Outputs de generate -> bucket `outputs`.
- [ ] Downloads via signed URLs (TTL configurable).
- [ ] Tests de integracion con mock o proyecto Supabase de CI.

## Seguridad

- Usar **service key** (`sb_secret_...`) en Railway, nunca la publishable.
- Rotar credenciales al cerrar el proyecto (checklist en `deploy_railway.md`).

## Referencia

Seccion Fase 7B/7E en `AGENTS.md`.
