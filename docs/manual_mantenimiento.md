# Manual de mantenimiento tecnico

Guia para operacion y mantenimiento del sistema.

Basado en `AGENTS.md`, `road.md` y `rubrica.md`.

---

## 1) Arranque local

Backend:

- Activar entorno Python del proyecto.
- Instalar dependencias de `requirements.txt`.
- Levantar API (puerto por defecto `8000`).

Frontend:

- Instalar dependencias npm.
- Levantar Angular (`4200`) o build de produccion.

---

## 2) Verificaciones minimas diarias

- `GET /health` responde 200.
- Frontend carga listado de procesos.
- Crear draft de prueba y leer chat.
- Ejecutar run de prueba sobre fixtures.
- Generar entregables (Excel + PBIP).

---

## 3) Pruebas de regresion recomendadas

- Backend unit/integration tests.
- `tests/test_render_excel.py`
- `tests/test_render_pbip.py`
- `tests/test_api_profiles.py`
- `tests/test_verificar_pbip_numeros.py`
- Build frontend (`npm run build`).

Nota: los gates duros de rubrica requieren E2E API + UI en Fase 6.

---

## 4) Telemetria LLM

Tabla: `llm_telemetry`.

Campos clave:

- `profile_id`, `agente`, `model`
- `input_tokens`, `output_tokens`
- `costo_usd_estimado`
- `latencia_ms`, `ok`, `error`, `creado_en`

Objetivo:

- medir costo por perfil,
- latencia,
- soporte para informe de presupuesto.

---

## 5) Verificacion numerica PBIP

Script gate 4B-F:

`scripts/verificar_pbip_numeros.py`

Valida:

- motor vs fact CSV,
- fragmentos DAX esperados en TMDL.

Uso base:

`python scripts/verificar_pbip_numeros.py --profile <profile.json> --left <archivo_izq> --right <archivo_der>`

---

## 6) Incidentes frecuentes

- Backend viejo corriendo con codigo desactualizado:
  - reiniciar proceso API,
  - repetir generacion PBIP.
- Fallo por dependencias faltantes en entorno:
  - reinstalar entorno y congelar versiones usadas.
- Drift de formulas DAX:
  - ejecutar script de verificacion PBIP,
  - revisar `render_pbip.py` y tests.

---

## 7) Cambios controlados

- Actualizar `road.md` al cerrar cada bloque.
- Si cambia un gate, reflejar tambien en `rubrica.md`.
- No cambiar tecnologias base sin decision explicita en decision log.

