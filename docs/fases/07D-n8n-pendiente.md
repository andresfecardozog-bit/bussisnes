# Fase 7D — Orquestador n8n (PENDIENTE)

**Estado:** pendiente

## Alcance planeado

Workflow n8n en Railway que automatiza el flujo completo de batch:

1. Crear batch (`POST /batches`).
2. Subir PRE CORTE (multipart o ZIP).
3. Subir FLASH con periodo declarado.
4. Preview + decision humana (webhook o pausa).
5. Confirm + Generate.
6. Notificar descargas listas.

## Principios

- Mismos endpoints atomicos que Power Automate (Fase 4).
- Sin logica de negocio en n8n; solo orquestacion HTTP.
- Reintentos idempotentes respetando `(pre_corte_hash, flash_hash)`.

## Entregables esperados

- [ ] Workflow exportado (JSON) en `docs/` o repo dedicado.
- [ ] Variables de entorno documentadas.
- [ ] Runbook de errores (`failed`, colisiones, flash periodo incorrecto).

## Referencia

`docs/tutorial_power_automate_orquestador.md` (plantilla similar para n8n).
