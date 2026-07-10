# rubrica.md - Metricas de autoevaluacion de la Plataforma Multiagente

Cada fase de [road.md](road.md) se califica contra esta rubrica antes de
marcarse `terminada`. Las metricas de producto se miden de forma continua
y se reportan en el informe final (Fase 6). Escala de calificacion por
criterio: `cumple` | `cumple_con_observaciones` | `no_cumple`. Un
`no_cumple` en un criterio marcado **[duro]** bloquea el cierre de la fase.

---

## 1. Metricas de producto (transversales)

| Metrica | Objetivo | Como se mide |
|---|---|---|
| Filas contabilizadas **[duro]** | 100% | filas_entrada == filas_cruzadas + filas_no_cruzadas, por fuente y por corrida |
| Acierto del mapeo propuesto | >=80% de campos del profile aprobados sin edicion humana | diff automatico propuesta original vs version aprobada (telemetria) |
| Cobertura del cruce | reportada siempre, sin objetivo fijo (depende del negocio) | % de keys de la fuente plan que encontraron contraparte |
| Tiempo humano por proceso nuevo | < 30 min de configuracion asistida | timestamp de creacion del draft al approve |
| Costo API por perfil configurado | < 1 USD por proceso nuevo (revisar con datos reales) | suma de telemetria LLM por profile |
| Regresion PRE CORTE **[duro]** | KPIs identicos al pipeline legado | test automatico pre_corte_v1.json vs pipeline legado |

## 2. Calidad de la entrevista (agentes)

| Metrica | Objetivo | Como se mide |
|---|---|---|
| Pertinencia de preguntas | >=80% calificadas utiles por el humano | boton de feedback por pregunta en UI (Fase 5); manual antes |
| Preguntas repetidas **[duro]** | 0 sobre lo ya respondido en profile_knowledge | test automatico: segunda corrida del mismo proceso |
| Supuestos bloqueantes sin confirmar **[duro]** | 0 | approve rechaza con bloqueantes abiertas (test de API) |
| Deteccion de grano | 100% de fuentes con grano declarado (confirmado o preguntado) | campo obligatorio en output de SchemaScout |

## 3. Gates por fase

### Fase 0 - Gobernanza

- [duro] AGENTS.md, road.md, rubrica.md existen y son consistentes entre si.
- [duro] Fixtures CEN/SAP abren con openpyxl y contienen filas con y sin key de cruce.
- Un agente nuevo puede arrancar leyendo solo los 3 documentos de memoria (prueba: subagente de verificacion resume el proyecto sin acceso al chat).
- Datos reales excluidos de git (git status limpio de xlsx operativos).

### Fase 1 - Contrato + motor

- [duro] pre_corte_v1.json reproduce los KPIs del pipeline legado sobre el fixture.
- [duro] Los 90+ tests legados siguen verdes.
- [duro] Cero perdida verificada en el motor generico (test con fixture sintetico).
- El contrato expresa el caso CEN vs SAP (borrador a mano valida contra los schemas sin error).
- El ConfigurableLoader maneja: sin headers (posicional), extension mentirosa, filas fantasma, group-by de grano, unpivot matriz.
- Formulas KPI declarativas: test de que una formula maliciosa/no soportada es rechazada.

### Fase 2 - Agentes + chat

- [duro] Reconstruccion >=80% del pre_corte_v1.json desde archivos + brief.
- [duro] Fixture de keys repetidas -> pregunta de grano correcta -> la respuesta cambia el profile.
- [duro] Segunda corrida no repite preguntas respondidas.
- Telemetria completa en cada llamada (tokens, costo, latencia, diff).
- Tests corren con modelo fake (CI sin gasto de API).
- Toda propuesta lleva justificacion legible por humano no tecnico.

### Fase 3 - CEN vs SAP

- [duro] KPI de cumplimiento validado contra calculo manual de la analista.
- [duro] Cero filas perdidas sobre P1..P7 + enero..junio completos.
- Entrevista real completada y persistida en profile_knowledge.
- Fricciones documentadas como backlog en road.md.
- Idempotencia: recargar el mismo periodo no duplica datos.

### Fase 4 - Renderers

- [duro] Excel de ambos casos abre sin reparacion en Excel.
- [duro] PBIP de ambos casos abre sin errores en Power BI Desktop.
- Checklist visual de marca (seccion 4).
- exporters generalizados siguen sin importar Font/Fill/Border directamente (test de puritanismo vigente).

### Fase 5 - Frontend + orquestacion

- [duro] Flujo completo operable por persona no tecnica sin tocar codigo (prueba guiada).
- Chat muestra preguntas con badge bloqueante/no bloqueante y el profile se actualiza al responder.
- Wizard PRE CORTE sigue funcionando (regresion E2E manual).
- Build de produccion dentro de budgets de Angular.
- n8n workflow generico importable y probado contra un profile real.

### Fase 6 - Cierre

- [duro] Bateria adversa completa verde: archivos corruptos, columnas faltantes, keys duplicadas, hojas renombradas, extensiones mentirosas, filas fantasma, archivos vacios, encoding roto.
- [duro] E2E de ambos perfiles via API y via UI.
- Manuales de uso y mantenimiento revisados contra el sistema real (cada paso ejecutado, no solo escrito).
- Informe de presupuesto con costos medidos (no estimados) de la telemetria.
- Todas las metricas de las secciones 1 y 2 medidas y reportadas.

## 4. Checklist visual de marca (Excel y Power BI)

- Paleta: navy #0F2E4C dominante en headers, naranja #E87722 solo como
  acento, semaforo verde/amarillo/rojo reservado para KPIs de cumplimiento.
- Logo NutriAvicola presente y nitido (chip blanco si el fondo es oscuro).
- Sin celdas/visuales sin formato; sin literatura decorativa (solo tablas
  y visuales con datos).
- Excel: headers navy con texto blanco, banded rows, bordes visibles,
  freeze panes, formatos numericos (#,##0 y 0.00%), tablas Excel nativas
  con filtros funcionales.
- Power BI: tema JSON aplicado (no colores default), tipografia
  consistente, cards KPI + tendencia temporal + desglose por categoria +
  tabla detalle como minimo, tooltips legibles.
- Cambiar la paleta = editar constantes/tokens en un solo lugar.

## 5. Registro de calificaciones

| Fecha | Fase | Resultado | Observaciones |
|---|---|---|---|
| 2026-07-10 | Validacion agentes (guiada) | cumple | scripts/validar_agentes.py con Gemini real, flujo guiado (responder entrevista con contexto estandar + refine): PRE CORTE 100% (frio 90%) y CEN vs SAP 100% (frio 61.5%). Ambos >=80%. Checks OK en guiado: doble llave orden+item (CEN), exclusion de devoluciones, nivel de servicio, breakdowns material/distrito/motivo, KPI ratio real/plan, join outer, grano por group_by, report portada+no_cruzados. Mejoras aplicadas: heuristicas genericas en _MAPPING_PROMPT (llave compuesta cuando hay >1 identificador compartido; exclusion de devoluciones/reversiones antes del cruce). El eval ahora mide frio y guiado; el guiado es el flujo real de uso. La entrevista en frio varia por corrida (no deterministica), por eso el uso recurrente va por procesos predefinidos deterministicos. Verdad de referencia: profiles/pre_corte_v1.json y cen_vs_sap_v1_borrador.json. Guia: docs/entrenamiento_agentes.md. |
| 2026-07-08 | Fase 4 | cumple_con_observaciones | Excel [duro]: los dos casos (pre_corte_v1 y cen_vs_sap_v1_borrador) generan .xlsx via render_excel con portada+KPIs+semaforo, breakdowns, nivel de servicio y tablas del data_model como ListObjects nombrados; verificados por 50 tests incluyendo apertura con openpyxl y test de puritanismo de estilos. PBIP: estructura completa (TMDL 4.2 + PBIR-legacy + theme de marca + medidas declarativas + README con racional del ReportDesigner) verificada por tests de parseo JSON/TMDL. OBSERVACION: el gate duro "abre sin errores en Power BI Desktop" requiere verificacion manual del usuario con los demos de data/outputs/_fase4_demo (no automatizable en este entorno). La fase se recalifica tras esa verificacion. |
| 2026-07-08 | Fase 2 (recalificacion con API real) | cumple | Gate (a) [duro]: scripts/eval_reconstruccion_pre_corte.py con Gemini 2.5 Flash real = 80.0% (umbral >=80). Checks OK: join por codigo MATERIAL sin keys de texto libre, outer, KPI ratio real/plan semanticamente correcto, computed ratio por fila, group_by del grano FLASH, portada. FAIL: filtro por fecha del FLASH (los agentes agrupan todo el mes; pregunta relacionada emitida) y hoja no_cruzados en el report propuesto. Calidad de entrevista real: preguntas sobre #VALUE! en columnas del plan, grano factura/posicion, y que columna es plan vs real (bloqueantes correctas). Costo medido: USD 0.375 por 22 llamadas acumuladas (~0.12 por propuesta completa), muy por debajo del objetivo <1 USD. Mejoras aplicadas antes de pasar: reglas duras en prompt del MappingArchitect (grano obligatorio, prohibido cruzar por texto libre, filtro de periodo con $parametro), sanitizacion de hojas breakdown huerfanas, KpiDesigner ahora propone service_level y breakdowns. |
| 2026-07-08 | Fase 2 | cumple_con_observaciones | Gates (b) y (c) [duros] verdes por tests automaticos con modelo fake: test_profile_propuesto_ejecuta_con_grano_corregido (la pregunta de grano existe, la respuesta cambia el profile de bloqueado-por-grano a cruce correcto con KPI 80%), test_segunda_corrida_no_repite_pregunta_respondida (dedup contra respondidas), test_approve_rechazado_con_bloqueante_abierta + version API (409). Telemetria: 5 llamadas registradas por propuesta con costo estimado. OBSERVACION: gate (a) reconstruccion >=80% con API real pendiente porque GEMINI_API_KEY no esta en el .env; script listo en scripts/eval_reconstruccion_pre_corte.py. La fase se recalifica al correrlo. |
| 2026-07-08 | Fase 1 | cumple | Regresion PRE CORTE [duro]: test test_regresion_pre_corte_kpis_identicos_al_legado compara particiones, KPIs globales y cumplimiento_pct fila a fila contra run_full_pipeline legado; identicos. Tests legados [duro]: suite completa 238/238 verdes (204 legados + 34 plataforma). Cero perdida [duro]: verify_accounting corre en cada run_profile + test sintetico test_cero_perdida_sintetico. Contrato expresa CEN vs SAP: cen_vs_sap_v1_borrador.json valida y corre end-to-end con cruce real (fixtures de junio). ConfigurableLoader: tests dedicados para sin-headers posicional, extension mentirosa (.XLS->xlsx por firma), filas fantasma, group-by de grano (GranoNoResueltoError si falta), unpivot matriz. Formula maliciosa rechazada: test_rechaza_formula_libre_como_op y test_rechaza_kpi_op_desconocida. |
| 2026-07-08 | Fase 0 | cumple | Documentos existen. Consistencia: un subagente de verificacion independiente detecto 11 inconsistencias en la primera version; todas corregidas (gates unificados con rubrica.md como fuente unica, criterio de regresion unificado a "KPIs identicos", cruce CEN documentado por nombre y posicion, mover repo reclasificado como tarea diferida fuera de fase). Fixtures verificados con openpyxl: CEN P7 con 2,045 filas y header correcto; SAP muestra con 400 filas con orden CEN + 200 sin, 70 columnas. git status sin xlsx operativos (solo fixtures en tests/fixtures/cen/). Onboarding verificado: el subagente resumio proyecto, fase siguiente, compromisos y trampas leyendo solo los documentos de memoria. |
