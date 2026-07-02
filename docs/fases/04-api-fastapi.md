# Fase 4 — Backend FastAPI atomico

**Estado:** completada  
**Fecha de referencia:** 2026-07-02

## Objetivo

Exponer el pipeline como endpoints HTTP atomicos para orquestadores
(Power Automate, n8n) y pruebas E2E.

## Endpoints principales

```
/health                                      GET
/files/upload-pre-corte                      POST
/files/upload-pre-corte-batch                POST
/files/upload-flash                          POST
/runs                                        GET
/runs/start-batch                            POST
/runs/{run_id}                               GET
/runs/{run_id}/approve|reject                POST
/pipeline/{run_id}/extract-date|load-*|...   POST
```

Swagger: `http://127.0.0.1:8000/docs`

## Artefactos

- `app/api/` — routers, schemas, storage en pickle.
- `run_backend.bat` — arranque local con uvicorn.
- `tests/test_api.py` — happy path, idempotencia, errores HTTP.

## Contrato operativo

Cada paso del pipeline es un POST independiente; el orquestador decide segun
el `status` del run (`awaiting_approval`, `approved`, etc.).
