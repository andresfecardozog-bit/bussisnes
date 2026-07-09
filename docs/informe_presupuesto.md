# Informe de presupuesto (base de trabajo)

Documento vivo para Fase 6.

Regla de rubrica: usar costos medidos (telemetria), no solo estimaciones.

---

## 1) Resumen ejecutivo

- Modelo LLM principal: Gemini 2.5 Flash (via Pydantic AI).
- Telemetria disponible: `llm_telemetry`.
- Baseline observado (historico actual):
  - propuesta completa aproximada: ~USD 0.12
  - corrida acumulada reportada: ~USD 0.375

Estos valores deben recalcularse periodicamente desde la base.

---

## 2) Fuente de verdad de costos

Tabla SQLite: `llm_telemetry`

Consulta sugerida (total general):

```sql
SELECT
  COUNT(*) AS llamadas,
  COALESCE(SUM(input_tokens), 0) AS input_tokens,
  COALESCE(SUM(output_tokens), 0) AS output_tokens,
  COALESCE(SUM(costo_usd_estimado), 0) AS costo_usd
FROM llm_telemetry;
```

Consulta por perfil:

```sql
SELECT
  profile_id,
  COUNT(*) AS llamadas,
  COALESCE(SUM(input_tokens), 0) AS input_tokens,
  COALESCE(SUM(output_tokens), 0) AS output_tokens,
  COALESCE(SUM(costo_usd_estimado), 0) AS costo_usd
FROM llm_telemetry
GROUP BY profile_id
ORDER BY costo_usd DESC;
```

Consulta por agente:

```sql
SELECT
  agente,
  COUNT(*) AS llamadas,
  COALESCE(SUM(costo_usd_estimado), 0) AS costo_usd,
  COALESCE(AVG(latencia_ms), 0) AS latencia_media_ms
FROM llm_telemetry
GROUP BY agente
ORDER BY costo_usd DESC;
```

---

## 3) Proyeccion mensual (plantilla)

Completar con datos medidos:

- procesos nuevos al mes: `N`
- costo promedio por proceso nuevo: `C`
- costo mensual LLM estimado: `N * C`

Separar escenarios:

- conservador,
- base,
- alto crecimiento.

---

## 4) Infraestructura (completar con factura real)

Rubros minimos:

- backend (Railway o equivalente),
- frontend (Vercel o equivalente),
- base de datos/storage (SQLite local o Supabase),
- costos operativos de backup/monitoring.

Registrar por mes:

- costo fijo,
- costo variable,
- total.

---

## 5) Riesgos y mitigacion

- Aumento de tokens por prompts largos:
  - medir por agente y optimizar prompts.
- Re-trabajo por preguntas ambiguas:
  - reforzar entrevista y memoria por proceso.
- Costos ocultos por ejecuciones repetidas:
  - usar idempotencia y verificacion previa.

---

## 6) Criterio de aceptacion Fase 6

Se considera completo cuando:

- costos se calculan desde `llm_telemetry`,
- no quedan valores "estimados manualmente",
- informe incluye escenarios y supuestos explicitos.

