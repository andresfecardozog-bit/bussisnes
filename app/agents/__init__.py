"""Capa de agentes LLM (Gemini 2.5 Flash via Pydantic AI).

Cuatro agentes con roles de equipo BI real:

- SchemaScout (ingeniero de datos): infiere estructura, tipos y GRANO de
  cada fuente. El grano es obligatorio: si las keys candidatas se repiten
  y no puede confirmar el significado, emite pregunta bloqueante.
- MappingArchitect (ingeniero ETL): propone loaders, keys de cruce con
  normalizadores y transforms (incluido group_by para ajustar grano).
- KpiDesigner (analista de datos): propone computed columns y KPIs.
- ReportDesigner (analista BI): propone el spec de reporte Excel/Power BI.

Principio de entrevista (AGENTS.md): los agentes PREGUNTAN, no asumen.
Cada output incluye `open_questions` tipadas con hipotesis e impacto.
Los agentes solo PROPONEN fragmentos del MatchProfile; el humano aprueba
y el motor deterministico (app/platform/engine.py) ejecuta. Ninguna
llamada LLM ocurre durante la ejecucion de un cruce.
"""
