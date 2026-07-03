# Fase 5 — Streamlit UI + Copilot Studio (PENDIENTE)

**Estado:** pendiente  
**Prioridad:** siguiente UI operativa para perfil no tecnico

## Alcance planeado

- App Streamlit con upload multiple de PRE CORTE + un FLASH mensual.
- Preview de KPIs en estado **borrador** hasta aprobacion humana visual.
- Persistencia a SQLite solo tras click de aprobacion.
- Embed de Copilot Studio para preguntas en lenguaje natural (sin calcular KPIs).

## Endpoints / modulos a reutilizar

- Pipeline FastAPI existente (Fase 4) o llamadas directas al core.
- Misma regla: matching deterministico; LLM solo interpreta preguntas.

## Criterios de aceptacion (borrador)

- [ ] Operador sube batch sin terminal.
- [ ] Ve colisiones de fecha y saltos de calendario antes de confirmar.
- [ ] No escribe en SQLite sin aprobacion explicita.

## Referencia

Ver seccion Fase 5 en `AGENTS.md`.
