# road.md - Roadmap vivo de la Plataforma Multiagente de Cruces

Documento de coordinacion. Todo agente (principal o subagente) que termine
un bloque de trabajo actualiza aqui: estado, fecha, y entradas al decision
log. Las reglas del proyecto viven en [AGENTS.md](AGENTS.md); las metricas
de calificacion en [rubrica.md](rubrica.md).

Estados posibles: `pendiente` | `en_progreso` | `bloqueada` | `terminada`.
Una fase solo pasa a `terminada` cuando su gate de rubrica esta calificado.

**Fuente unica de los gates:** los criterios de salida de cada fase son los
de la seccion correspondiente de [rubrica.md](rubrica.md). Lo que aparece
aqui como "Gate de salida" es un resumen; ante cualquier diferencia, manda
rubrica.md. Los titulos de fase usados aqui son los canonicos; AGENTS.md y
rubrica.md los referencian en forma abreviada.

---

## Fase 0 - Gobernanza y memoria

**Estado:** terminada (2026-07-08)
**Depende de:** nada (bloqueante de todo lo demas)

Entregables:

- [x] AGENTS.md nuevo (system_role, contexto, MatchProfile, principio de
  entrevista, compromisos). Historico preservado en docs/AGENTS_LEGACY.md.
- [x] road.md (este archivo).
- [x] rubrica.md.
- [x] Exploracion de datos reales CEN/SAP y registro de hallazgos
  (estructura, grano, trampas) en AGENTS.md.
- [x] Fixtures en tests/fixtures/cen/ (CEN P7 completo + SAP junio
  recortado 600 filas).
- [x] data_nivel_cumplimiento/ agregado a .gitignore.

Tarea diferida (fuera del alcance de esta fase, no bloquea nada): mover el
repo a una ruta corporativa limpia (propuesta C:\dev\nutri-cruces).
Requiere que el usuario cierre y reabra Cursor; se coordina al final de la
sesion que el usuario elija.

**Gate de salida (resumen; detalle en rubrica.md seccion Fase 0):** los 3
documentos de memoria existen y son consistentes entre si, un agente nuevo
puede arrancar leyendolos, fixtures verificados con openpyxl (con filas con
y sin key de cruce), datos reales excluidos de git.

---

## Fase 1 - Contrato MatchProfile + motor generico (sin IA)

**Estado:** terminada (2026-07-08)
**Depende de:** Fase 0

Entregables:

- [x] `app/platform/profile.py`: schemas Pydantic del MatchProfile
  (sources, loaders, transforms, join, kpis, report), versionado,
  serializable a JSON. Whitelists cerradas: el LLM no puede expresar
  operaciones fuera del contrato.
- [x] `app/platform/loader.py`: ConfigurableLoader. Maneja: auto-deteccion
  de hoja por headers (evita hojas pivote), header_row=None con columnas
  posicionales (SAP), extension mentirosa via firma binaria, filas
  fantasma/vacias contabilizadas, dtypes con limpieza numerica, codigos
  enteros que Excel entrega como float ('30018.0' -> '30018'). Registry de
  loaders custom en codigo (`pre_corte_resumen`, `flash_sap`) para reusar
  parsers legados validados.
- [x] `app/platform/engine.py`: outer-join N keys con normalizadores
  declarativos, particiones matched/solo_left/solo_right, computed columns
  y KPIs declarativos (sin eval). `verify_accounting()` corre SIEMPRE
  (cero perdida). `GranoNoResueltoError` si las keys se repiten sin
  group_by previo (proteccion del grano). Transform `unpivot` para
  layouts matriz.
- [x] Validacion cero-perdida integrada en el motor (verify_accounting) +
  contabilidad de descartes en el loader (meta['descartes']).
- [x] `app/platform/store.py`: tablas `profiles` (versionadas, con status
  draft/proposed/approved/archived), `profile_runs` (idempotencia por
  profile+version+hashes+params, reemplazo transaccional), `cruce_generico`,
  `no_cruzados_generico`, y las tablas de Fase 2 `profile_knowledge` y
  `profile_questions` (schema listo, se pueblan en Fase 2).
- [x] `profiles/pre_corte_v1.json` reproduce el pipeline legado.
- [x] `profiles/cen_vs_sap_v1_borrador.json` valida contra el schema y
  corre end-to-end sobre fixtures de junio (13 lineas cruzadas reales).

**Gate de salida:** calificado `cumple` en rubrica.md (34 tests nuevos de
plataforma; suite completa 238/238 con regresion legado intacta).

**Hallazgos de datos reales (insumo para la entrevista de Fase 3):**

- La col 56 del SAP (numero de pedido) NO trae solo ordenes CEN formato
  `NNN-NNNNNNN`: mezcla pedidos de otros origenes ('261357', '4503551545',
  '0020016263'...). Pregunta obligada para la analista: se filtra por
  formato de orden CEN, por canal, o por lista de clientes?
- Los codigos de item llegan como numero en CEN (30018.0) y como texto en
  SAP (30018): el loader ya normaliza, pero es ejemplo canonico de por que
  las keys necesitan normalizadores declarados.
- En la orden de prueba 003-0023901: CEN pide 12 items (uno sin codigo),
  SAP entrega 10; hay un item entregado (40135) que no aparece pedido con
  ese codigo. El manejo de items sin codigo es otra pregunta de entrevista.

---

## Fase 2 - Capa de agentes IA + chat de entrevista

**Estado:** terminada (2026-07-08; gate (a) con API real = 80.0%, ver rubrica)
**Depende de:** Fase 1 (contrato congelado)

Entregables:

- [x] `app/agents/`: file_probe.py (sondeo deterministico: hojas, tipos,
  distinct_ratio como senal de grano, muestras; detecta extension
  mentirosa y dimension inflada), schemas.py (outputs tipados con
  OpenQuestion y GranoAssessment obligatorio), crew.py (4 agentes
  Pydantic AI + system prompts de rol + principio de entrevista),
  orchestrator.py (propose/refine/answer/assume/approve + dedup de
  preguntas), telemetry.py (tabla llm_telemetry con costo estimado).
- [x] SchemaScout con GranoAssessment obligatorio; el motor ademas
  protege con GranoNoResueltoError si el mapeo ignora el grano.
- [x] Tablas `profile_knowledge` y `profile_questions` pobladas por el
  orquestador; el contexto acumulado se inyecta a los prompts.
- [x] Endpoints /profiles: draft, proposal, chat (GET hilo + POST
  respuesta/nota), questions/{id}/assume, refine, approve (409 con
  bloqueantes), PUT edicion manual, run (409 si no aprobado, motor
  deterministico), runs, telemetry.
- [x] Telemetria por llamada (tokens, costo estimado, latencia, ok/error).
- [x] 17 tests con FunctionModel fake (test_agents_fake.py +
  test_api_profiles.py); CI sin gasto de API.
- [x] Script del gate (a): `scripts/eval_reconstruccion_pre_corte.py`
  (corre con la API real y reporta el score de reconstruccion).

**Gate de salida:** (b) y (c) verificados por tests automaticos [verde].
(a) CUMPLIDO 2026-07-08: `scripts/eval_reconstruccion_pre_corte.py` con
Gemini real = 80.0% (umbral >=80). Registrado en rubrica.md. Costo medido
USD ~0.12 por propuesta completa.

---

## Fase 3 - Caso CEN vs SAP end-to-end

**Estado:** en_progreso (2026-07-09: corrida API real P6/junio + homologacion
ejecutada; falta validacion manual con analista y cierre UI)
**Depende de:** Fase 2
**Requiere al usuario:** responder la entrevista real (grano de ordenes,
keys con nombre distinto, ordenes parcialmente entregadas, que hacer con
ventas SAP sin orden CEN).

Entregables:

- [ ] Flujo completo con archivos reales: brief -> analisis -> preguntas
  -> respuestas -> propuesta -> aprobacion -> cruce -> reportes.
- [x] E2E API real ejecutado con Gemini + archivos reales
  (`scripts/e2e_flujo_cen.py` parametrizado): P6 CEN vs junio SAP +
  homologacion, con generacion y descarga de Excel/PBIP.
- [ ] Homologaciones en entrevista: si hay codigos sin semantica
  (material/cliente/distrito), los agentes deben pedir archivo de
  homologacion antes de cerrar propuesta; registrar la decision en
  profile_knowledge.
- [ ] Carga acumulada de P1..P7 CEN + enero..junio SAP en la base.
- [ ] `profiles/cen_vs_sap_v1.json` aprobado y persistido.
- [ ] Registro de fricciones (normalizadores faltantes, tipos de loader
  nuevos) como backlog de mejoras del motor.

**Gate de salida:** cumplimiento CEN vs SAP validado contra un calculo
manual de la analista; cero filas perdidas (contabilidad completa en
no_cruzados_generico); memoria del proceso poblada.

**Exploracion previa ya corrida** (`scripts/explorar_cen_vs_sap.py`,
borrador a mano, sin persistir, cifras para la entrevista):

| Periodo | matched | solo CEN | solo SAP | tasa cruce CEN | cumplimiento |
|---|---|---|---|---|---|
| P1 vs enero | 5,789 | 2,505 | 9,935 | 69.8% | 110.8% |
| P2 vs febrero | 5,420 | 3,054 | 8,550 | 64.0% | 108.7% |
| P3 vs marzo | 4,916 | 4,026 | 9,315 | 55.0% | 108.5% |
| P4 vs abril | 5,282 | 4,266 | 8,673 | 55.3% | 107.5% |
| P5 vs mayo | 5,596 | 3,124 | 9,330 | 64.2% | 110.1% |
| P6 vs junio | 5,533 | 3,637 | 10,000 | 60.3% | 109.9% |

**Nivel de servicio por pedido** (borrador v2, excluyendo DEVOLUCIONES):

| Periodo | pedidos | completos | parciales | no entregados |
|---|---|---|---|---|
| P1 | 2,610 | 38.2% | 36.4% | 25.4% |
| P2 | 2,544 | 37.5% | 33.4% | 29.1% |
| P3 | 2,476 | 31.7% | 33.6% | 34.7% |
| P4 | 2,787 | 33.8% | 30.6% | 35.6% |
| P5 | 2,577 | 37.0% | 35.7% | 27.4% |
| P6 | 2,708 | 36.3% | 35.4% | 28.3% |

**Devoluciones por motivo**: el motivo dominante en unidades es VACIO
(sin motivo registrado en col 62: 67K-132K unidades/mes), seguido de
ROTURA GENERADA EN SUPERMERCADO-MERCADEO (~4K-13K) y ROTURA
TRANSPORTE-LOGISTICA / FECHA PROXIMA A VENCER.

Preguntas para la analista que salen de estos numeros (ademas de las que
generen los agentes): (1) cumplimiento >100% en todos los meses: SAP
registra entregas de ordenes de meses anteriores, o hay devoluciones/
anulaciones que hay que restar (columnas de clase de documento ZBVN/ZNTT/
ZNAH)? (2) ~35-45% de lineas CEN sin entrega el mismo periodo: es fuga
real o desfase de fechas entre orden y factura? (3) el "periodo" P
del CEN se define por F. Documento O/C o por fecha de entrega? (4) las
filas SAP con pedido en otros formatos ('00001054', '*', vacio) son
ventas no-CEN que deben excluirse del denominador? (5) la mayoria de las
unidades devueltas NO tienen motivo registrado: se reportan como "sin
motivo" o hay otra columna que lo explique?

---

## Fase 4 - Renderers: Excel declarativo + Power BI (PBIP)

**Estado:** en_progreso (base lista; extension a breakdowns/data_model/
measures en curso por subagente)
**Depende de:** Fase 1 (la parte Excel), Fase 2 (specs del ReportDesigner)

Entregables:

- [x] Excel base (subagente renderers, 2026-07-08):
  `app/platform/render_excel.py` consume report.excel.sheets (portada con
  logo + KPI con semaforo del profile, kpi_resumen, tablas como Excel
  Tables). Estilo 100% via excel_style.py (test de puritanismo incluido).
  `excel_style.py` gano `KpiRow.rangos` para semaforo por profile.
- [x] PBIP base: `app/platform/render_pbip.py` genera proyecto completo
  (.pbip + Report PBIR-legacy + SemanticModel TMDL 4.2 + theme de marca
  NutriAvicola + medidas DAX por KPI + CSVs con parametro M `RutaDatos`).
  Decision: PBIR-legacy porque el enhanced exige feature flag en Desktop.
- [x] EXTENSION completada (2026-07-08): hojas breakdown con semaforo en
  columnas *_pct, tablas "Nivel de servicio" y "Pedidos" en portada,
  hojas del data_model como ListObjects con nombre exacto y fact sin
  truncar (la "base de datos en Excel"), CSVs+tablas TMDL de todo el
  data_model + breakdowns referenciados por visuales, medidas desde
  PowerBIMeasureSpec (DISTINCTCOUNT, porcentaje, moneda COP), visuales
  donut/matriz con binding por tabla, README con "Diseno propuesto por
  ReportDesigner" (proposito + justificacion por visual). 50 tests de
  render. Demos PRE CORTE y CEN en data/outputs/_fase4_demo.
- [x] Integracion API (orquestador, 2026-07-08): POST
  /profiles/{id}/generate (run + persist + render_excel + PBIP zipeado a
  data/outputs/profiles/{id}/), GET /profiles/{id}/downloads y
  /downloads/{filename} con proteccion path traversal. Suite 320/320.
- [ ] Verificacion manual: abrir el PBIP demo en Power BI Desktop
  (data/outputs/_fase4_demo, gitignored) y confirmar theme aplicado +
  refresh OK. Requiere al usuario (no automatizable desde aqui).
- [ ] Backlog: relationships.tmdl pre-armadas entre fact y dimensiones
  (hoy Desktop las autodetecta o el BI las crea manualmente); matriz con
  segunda dimension en Columns cuando el contrato la soporte.

**Gate de salida:** ambos casos (PRE CORTE y CEN) producen Excel + PBIP
que abren sin errores en Excel/Power BI Desktop y pasan el checklist
visual de marca de rubrica.md.

### Fase 4B - PBIP corporativo (referencia `Nivel de servicio.pbix`)

**Estado:** en_progreso (2026-07-09, tras revision manual del usuario)
**Depende de:** Fase 4 base, perfil CEN aprobado con data_model

**Diagnostico verificado contra motor + fact (fixtures junio + profile
e2e_cen_1783571809 v2):**

- El 3,33% del card "Nivel de servicio" **es matematicamente correcto**
  para la medida actual `cumplimiento_global_pct` = entregadas /
  pedidas en universo CEN (cruzado + solo_plan). Con fixtures: 154 /
  4005 = 3,85%. Con datos completos del usuario: ~1540 / 46K = 3,33%.
  No es un bug de suma: refleja que la gran mayoria de unidades pedidas
  en CEN no tienen entrega en SAP.
- El 60,16% del grafico por distrito es **otra metrica** (solo lineas
  cruzadas, matched). Mezclar ambas en la misma pagina confunde.
- "Pedidos completos" en blanco al filtrar: DAX `sl_pedidos_completos_pct`
  + slicers sobre columnas crudas del fact (no dimensiones) rompen el
  contexto de filtro. Con datos sin filtro deberia ser 0%, no blank.
- Filtros entre charts "rompen" visuales: slicers en `id_punto_venta` y
  `nivel_servicio` del fact; la mayoria de filas solo_plan tienen NULL
  en columnas SAP; interseccion vacia -> cards en blanco.
- Labels tecnicos (`no_entregado`, `sin_pedido`) sin traduccion humana.
- Sin branding: fondo blanco plano, sin logo, sin banda de encabezado
  como el PBIX de referencia de la empresa.

**Referencia de diseno:** `Nivel de servicio.pbix` (5 paginas, 51
visuales). Patrones replicables: logo top-left, titulo en textbox,
slicers horizontales tipo tile por distrito, KPI cards por area,
donut pedidos entregados/no, pivot resumen distrito, linea temporal,
barras por causal, funnel por material. Tema Classroom en el PBIX de
referencia; nosotros mantenemos NutriAvicola (navy + naranja) con logo
`resources/image_720508810_0.jpg`.

**Bloques de trabajo (orden de ejecucion):**

| Bloque | Que | Archivos | Gate |
|--------|-----|----------|------|
| 4B-A | Semantica de medidas: separar NS unidades %, NS pedidos %, NS lineas cruzadas %; corregir DAX pedidos completos; alinear cards con motor `service_level` | render_pbip.py, profile contract, ReportDesigner prompt | Numeros del PBIP = motor en fixtures (tolerancia 0,01) |
| 4B-B | Filtros robustos: slicers solo via dimensiones (DimDistrito.nombre, DimCliente...); relaciones FK obligatorias; evitar slicers en columnas del fact con muchos NULL | render_pbip.py, data_model | Filtrar un distrito no deja cards en blanco |
| 4B-C | Labels humanos: mapa `nivel_servicio` -> Completo/Parcial/No entregado/Sin pedido; `displayName` en TMDL y columnProperties; orden de leyenda fijo | render_pbip.py | Cero underscores visibles al usuario |
| 4B-D | Branding + layout referencia: logo RegisteredResources, textbox titulo, banda header 1280x720, slicers tile horizontales, fondo no-blanco plano | render_pbip.py, resources/ | Logo visible; checklist marca rubrica |
| 4B-E | Paginas extra (drill): Logistica, Venta, Destinatario x causal - pivots + back button | render_pbip.py, ReportDesigner template | >=4 paginas con proposito declarado |
| 4B-F | Gate de verificacion: script `scripts/verificar_pbip_numeros.py` cruza Excel fuente vs fact CSV vs medidas | scripts/, tests/ | CI verde + informe para humano |

- [x] 4B-A Semantica medidas (parcial 2026-07-09: medidas NS unidades %, lineas cruzadas %, DAX pedidos completos corregido)
- [x] 4B-B Filtros via dimensiones (parcial 2026-07-09: slicers por Dim* + estado_entrega, modo tile)
- [x] 4B-C Labels humanos (estado_entrega en fact + displayName)
- [x] 4B-D Branding + header (parcial 2026-07-09: logo RegisteredResources, textbox titulo, fondo navy-50, slicers Basic)
- [x] 4B-E Paginas drill (2026-07-09: Logistica, Venta, Destinatario x causal + boton "Volver a Nivel de servicio")
- [x] 4B-F Script verificacion numeros (2026-07-09: `scripts/verificar_pbip_numeros.py` + tests)

---

## Fase 5 - Frontend generalizado + orquestacion

**Estado:** en_progreso (construido; falta E2E manual UI+backend del gate)
**Depende de:** Fase 2 (endpoints de chat/propuesta)

Entregables:

- [x] Flujo Angular "Nuevo proceso de cruce" (subagente frontend,
  2026-07-08): `/procesos` (lista con status chips), `/procesos/nuevo`
  (brief + 2 archivos + spinner narrativo de agentes), `/procesos/:id`
  (chat de entrevista + panel del MatchProfile + aprobar/re-proponer/
  ejecutar/telemetria). profiles.service.ts cablea los 13 endpoints.
  Build de produccion verde: initial 414.5 kB raw / 102 kB transfer,
  chunks lazy (detalle 48.9 kB).
- [x] Chat: preguntas como cards con borde rojo (bloqueante) / azul
  (info), hipotesis e impacto colapsables, respuesta inline, boton
  "asumir hipotesis" solo en no bloqueantes, respondidas marcadas.
- [x] Wizard PRE CORTE intacto como caso particular (no se toco).
- [x] n8n: `n8n/n8n_workflow_run_profile.json` (webhook -> verifica
  bloqueantes via /proposal -> /run -> email exito/error/bloqueado).
- [ ] E2E manual: UI contra backend con GEMINI_API_KEY real (junto con
  el gate (a) de Fase 2 y la entrevista de Fase 3).
- [x] Boton "Generar entregables" + descargas Excel/PBIP en detalle del
  proceso (profiles.service generate/downloads, 2026-07-09).
- [x] Adjuntar homologacion desde UI (opcional en `/procesos/nuevo` y
  tambien en `/procesos/:id`) y enviarla al backend para import al
  catalogo antes de generar entregables (2026-07-09).
- [x] El panel de resumen del profile en la UI ya renderiza secciones
  `service_level`, `breakdowns` y `data_model` en formato legible (2026-07-09).

**Gate de salida:** una persona no tecnica configura un cruce nuevo sin
tocar codigo, guiada por la UI.

---

## Fase 6 - Verificacion intensiva + informes de cierre

**Estado:** en_progreso
**Depende de:** todas las anteriores

Entregables:

- [ ] Bateria sistematica: regresion completa, E2E de ambos perfiles via
  API y via UI, casos adversos (archivos corruptos, columnas faltantes,
  keys duplicadas, hojas renombradas, extensiones mentirosas, filas
  fantasma), verificacion visual de reportes, medicion de rubrica.md
  completa.
- [x] Runner base de verificacion ejecutado (`scripts/verificacion_fase6.py`)
  en entorno local con dependencias; checks automatizados en verde
  (pytest clave + verificacion PBIP numerica + build frontend).
- [ ] docs/manual_uso_analistas.md (borrador inicial creado 2026-07-09; falta validacion paso a paso en sistema real)
- [ ] docs/manual_mantenimiento.md (borrador inicial creado 2026-07-09; falta corrida completa de checklist operativo)
- [ ] docs/informe_presupuesto.md (plantilla creada 2026-07-09; falta completar con costos medidos finales por periodo)
  (costos Gemini medidos por telemetria,
  no estimados, por perfil/mes; Railway, Vercel, Supabase; proyeccion a N
  procesos) + necesidades futuras (roles, permisos, SSO, fuentes API
  directas).

**Gate de salida:** todo funcional verificado, rubrica calificada, informes
entregados. SOLO al cerrar esta fase se informa al usuario que el proyecto
esta terminado.

---

## Decision log

| # | Fecha | Decision | Alternativas evaluadas | Razon |
|---|-------|----------|------------------------|-------|
| 1 | 2026-07-08 | LLM como interprete de estructura; motor deterministico ejecuta | LLM en el core; hibrido fila-a-fila | Auditabilidad y cero perdida son innegociables; el LLM propone configuracion, el humano aprueba |
| 2 | 2026-07-08 | Evolucionar este repo, no greenfield | Repo nuevo desde cero | ~55-65% del esfuerzo arquitectonico transfiere (storage, excel_style, ciclo batch, API, design system, tests); PRE CORTE queda como regresion permanente |
| 3 | 2026-07-08 | Pydantic AI + maquina de estados propia en FastAPI | CrewAI, AutoGen, LangGraph | Pipeline lineal con gates humanos, no conversacion autonoma; outputs estructurados nativos contra schemas Pydantic; testeable con modelos fake |
| 4 | 2026-07-08 | Intake conversacional con cola de preguntas tipadas y memoria por proceso | Formulario estatico | Los agentes deben preguntar como personas (grano de fila, keys con nombres distintos); lo respondido no se vuelve a preguntar |
| 5 | 2026-07-08 | Privacidad: API de pago sin restriccion, pero por diseno solo metadatos + muestras | Enviar archivos completos siempre | Mas barato y rapido; archivo completo solo como escalacion explicita |
| 6 | 2026-07-08 | Fixtures recortados en repo; datos reales gitignored | Commitear datos reales | Datos de negocio no viven en el repo; 600 filas de SAP bastan para tests |
| 7 | 2026-07-08 | Mover repo a ruta limpia se difiere al final de sesion | Mover ya | Mover la carpeta rompe el workspace abierto de Cursor; requiere cierre/reapertura coordinada con el usuario |
| 8 | 2026-07-08 | Requisitos de negocio CEN ampliados: service_level + breakdowns + devoluciones por motivo, todo declarativo en el contrato | Hardcodear en un exporter especifico | El usuario pidio nivel de servicio en % y unidades, KPIs de motivos de rechazo y clasificacion por material/distrito/cliente; hacerlo declarativo beneficia a todos los procesos futuros, no solo CEN |
| 10 | 2026-07-09 | PBIP alineado al dashboard corporativo `Nivel de servicio.pbix` como plantilla de layout (no copia del dataset cloud); medidas separadas unidades vs pedidos; slicers por dimension | Replicar Classroom theme del PBIX de referencia | El 3% del card actual es correcto para unidades pero no coincide con el 85% de pedidos del referente; hay que mostrar ambas metricas con etiquetas claras y no mezclar universos |
| 11 | 2026-07-09 | Homologacion entra como insumo de proceso (no post-proceso manual): upload por perfil + import al catalogo + enriquecimiento automatico al ejecutar/generar | Mantener homologacion fuera del flujo y traducir codigos manualmente en BI | El usuario pidio que el sistema pida/reciba homologaciones en entrevista y que los entregables ya salgan legibles; se implementa trazabilidad por profile_knowledge y aplicacion deterministica sobre resultados |

---

## Registro de avance

- 2026-07-09 (11): Bloque de seguridad y credenciales implementado siguiendo
  `docs/seguridad_plan.md` (Bloques 0-5 en una sola entrega tecnica):
  (a) auth global "secure by default" en FastAPI con whitelist minima
  (`/auth/login`, `/health`, `/auth/*`), RBAC por permisos y ownership sobre
  `profiles`, `batches`, `runs` y `cargas`; (b) sesiones server-side con
  cookie `HttpOnly+Secure+SameSite`, CSRF token en mutaciones, expiracion
  sliding+absoluta, bloqueo por intentos fallidos y cambio forzado de
  contrasena al primer login; (c) bootstrap idempotente de admin via
  `ADMIN_EMAIL` + `ADMIN_INITIAL_PASSWORD`, seed de roles/permisos
  (`admin`, `analista_todos`, `analista_propios`, `sin_historial`), tabla de
  tokens de servicio para M2M futuro; (d) hardening inmediato: CORS sin `*`,
  `/docs` y `/openapi.json` cerrables en prod, headers de seguridad, upload
  sanitizado y con limites (tamano, cantidad, ZIP ratio/entries/uncompressed),
  logging en fallos de mirror storage; (e) eliminacion de `pickle` en cache de
  DataFrames (parquet/csv seguro). Frontend: login + cambio de contrasena,
  guard de rutas privadas, interceptor con `withCredentials` + `X-CSRF-Token`,
  logout en shell. Verificacion: `pytest` completo 366/366 verde y
  `npm run build` Angular verde.

- 2026-07-09 (10): Rediseño completo del frontend aprobado. Stack: Angular
  22 (sin migración). Estructura: landing pública en `/`, app privada en
  `/app/*` con AppShellComponent (sidebar navy nuevo). Cambios principales:
  (a) tokens v2 en styles.scss: azul rey (#123C7A), dorado ampliado (50-900),
  fondo cáscara (#FBF8F2), duración y ease de motion; (b) app.html/ts
  simplificado a solo router-outlet; (c) AppShellComponent (sidebar navy
  con brillo dorado, nav items con indicator dorado al activo, responsive);
  (d) Landing reimaginada: navbar sticky, hero con huevo dorado animado
  (gradiente oro + animación float), 3 pasos con íconos SVG propios, fila
  de métricas sobre fondo navy con underlines dorados y contadores animados,
  footer con contacto; (e) Dashboard rediseñado fiel al mockup aprobado:
  4 KPI cards (la de dinero ahorrado resaltada con acento dorado y huevo SVG
  inline), 2 charts SVG (cumplimiento por periodo + ahorro acumulado, ambos
  con punto dorado en el pico), tabla de procesos recientes con barra de
  progreso y chips de estado. Salario analista BI = COP $3.5M / 160h.
  Build Angular: 0 errores, 0 warnings.

- 2026-07-09 (9): PREVENCION SISTEMATICA de errores en el flujo del usuario
  (requisito: "en el flujo del usuario nunca debe haber esta clase de
  errores"). El "smoke run" del orquestador solo corria `run_profile`, por
  eso los 500 de los renderers llegaban a /generate. Ahora
  `_full_generate_dry_run` ejecuta el pipeline COMPLETO (run_profile +
  render_excel + render_pbip) contra un temp dir durante propose/refine
  (draft y refine); cualquier error se convierte en pregunta bloqueante del
  Motor + memoria, para que los agentes iteren antes de que el humano
  apruebe. Defensa en profundidad: `POST /generate` envuelve el render y
  devuelve 422 accionable ("usa refine") en vez de 500 opaco. Con esto, las
  6 clases de error de la sesion quedan: PREVENIDAS (renderers/motor
  endurecidos), IDENTIFICADAS (dry-run completo) e ITERABLES (pregunta
  bloqueante -> refine). Test `test_dry_run_completo_cubre_render`.

- 2026-07-09 (8): E2E LLM COMPLETO OK de punta a punta (draft -> chat ->
  refine -> approve -> generate -> descargas) con Gemini real + archivos
  reales P6/junio + homologacion. Profile e2e_final5: matched 5690, OTIF
  unidades 109.73%, pedidos completo 51.93% / parcial 25.69% /
  no_entregado 22.38%; Excel 1.6MB + PBIP zip 361KB descargados; telemetria
  12 llamadas / USD 0.126. Se resolvio el bloqueo del gate atacando 5 causas
  raiz encadenadas (todas fixes del motor/renderer, aplican a cualquier
  profile del LLM), cada una con test de regresion:
  1. GRANO 422 (el original): normalizadores de join no idempotentes
     (lstrip_zeros + digits_only) colapsaban llaves DESPUES del group_by.
     Fix: `_prenormalize_join_keys` pre-normaliza las llaves a PUNTO FIJO
     antes de los transforms, para que el group_by agrupe sobre la misma
     llave que produce el join. Test
     `test_grano_consistente_con_normalizadores_no_idempotentes`.
  2. MODELO retirado por Google: `gemini-2.5-flash` empezo a dar 404 ("no
     longer available") a mitad de sesion. Fix: `GEMINI_MODEL` configurable
     por env, default alias rolling `gemini-flash-latest` (config.py +
     crew.py). Probado con llamada real.
  3. data_model 500: fact con columnas de nombre duplicado rompia
     sort_values. Fix: dedup de columnas en build_data_model. Test
     `test_fact_con_columnas_duplicadas_no_rompe`.
  4. data_model 500 (variante): key de dimension y un atributo resolvian a
     la MISMA columna -> subset duplicado. Fix: `_build_dimension` excluye
     key y duplicados de attr_cols. Test
     `test_dimension_key_igual_a_atributo_no_rompe`.
  5. breakdown 422: dimension/metrica referenciaba una columna que el merge
     desdoblo con sufijo `_left`/`_right`. Fix: `_run_breakdown` resuelve
     columnas tolerando sufijos (`_resolve_col`).
  6. render 500: medida PowerBI del LLM malformada (op sum sin column). Fix:
     `_build_measures` omite medidas invalidas sin tumbar el render.
  LECCION: cada corrida LLM genera un profile distinto que destapa un hueco
  de robustez en la capa determinista; el principio "motor robusto, LLM
  propone" exige que generate NUNCA reviente por un profile propuesto.

- 2026-07-09 (7): Polish visual del PBIP por feedback del usuario (capturas
  Power BI). Corregido en el RENDERER (aplica a cualquier profile): (1) KPIs
  duplicados -> el enriquecimiento ya no inserta la card "Nivel servicio
  unidades (%)" si la pagina ya tiene una card de nivel de servicio; cada
  card muestra una llave de valor distinta (NS %, pedidos completos %,
  unidades pedidas/entregadas/sin pedido). (2) Donut vacio -> se quitaba el
  binding al poner la categoria tambien como 'series'; ahora el donut usa
  solo Category (estado_entrega) + Y, y pinta los slices. (3) Labels con
  underscore ("Total unidades_devueltas") -> los totales implicitos se
  nombran humanizados ("Total unidades devueltas"). (4) Valores ilegibles
  ("0K") -> data labels con labelDisplayUnits=0 (sin forzar K/M) y fontSize
  12; textClasses label 10->12 y header 12->13 (tablas/ejes mas legibles).
  Prompt del ReportDesigner reforzado: prohibido repetir el mismo KPI en una
  pagina, cada visual una llave de valor distinta, titulos legibles sin
  underscores, verificar que la categoria exista. Tests: nuevo assert de
  label sin underscore; 56 verdes.

- 2026-07-09 (6): BUG PBIP corregido: Power BI no abria con
  "Could not add Measure 'Total unidades_devueltas' ... already exists in
  the Model". Causa: las medidas en Power BI son GLOBALES al modelo (no por
  tabla) y `_MeasureRegistry.ensure_column_total` generaba "Total {columna}"
  con el nombre de columna del breakdown; varios breakdowns comparten
  columnas (unidades_devueltas, lineas, unidades_pedidas...), asi que dos
  totales implicitos colisionaban. Fix en `render_pbip.py`: el registry
  ahora garantiza nombres de medida unicos en todo el modelo (set global
  `used_names` + desambiguacion por tabla). Es un fix del RENDERER (aplica a
  cualquier profile, incluidos los generados por agentes), no solo a este
  caso. Test de regresion nuevo
  `test_nombres_de_medida_unicos_en_todo_el_modelo`. Regenerado: 16 medidas,
  0 duplicadas; verificacion numerica PBIP OK; 49 tests de render verdes.

- 2026-07-09 (5): Capacidad de investigacion deterministica en los agentes
  (feedback usuario: "debe haber un agente capaz de hacer todos estos
  calculos" y preguntar por celdas incoherentes). Se agrego a
  `app/agents/file_probe.py` deteccion de anomalias por cross-tab: cuando
  una columna identificadora esta parcialmente vacia (3-85%), el probe
  (motor, no LLM) cruza las filas vacias contra las columnas categoricas de
  negocio (2-15 categorias) y entrega las distribuciones para que SchemaScout
  interprete y pregunte. Arquitectura correcta: el motor calcula, el LLM
  interpreta/pregunta. Verificado en SAP junio: detecta "col 56 vacia 56.2%;
  entre vacias col 7 = TAT 56%, PUNTOS PROPIOS 44%" (mismo hallazgo que el
  analisis manual). Ademas se limpio el canal en blanco incoherente
  (4 filas, 19M unidades, filas totales) del breakdown ventas_por_canal via
  require_non_null. `probe_to_prompt` ahora emite lineas "ANOMALIA A
  INVESTIGAR". Metodo generalizable reflejado en `_METODO_ANALISIS` (los 4
  agentes) y en AGENTS.md. Suite 77 tests verdes; dashboard regenerado.

- 2026-07-09 (4): Enriquecimiento del caso CEN vs SAP con hallazgos reales
  y correccion de interpretacion (feedback del usuario sobre canal TAT).
  Verificado con datos junio (37,052 filas): el ~57% de SAP sin codigo de
  orden NO son errores, son canales de venta directa que no pasan por CEN
  (TAT 99.4%, PUNTOS PROPIOS 99.9%, EMPLEADOS 99.3%); solo placeholders de
  texto ('*', 'SIN DC', 1,652 filas) pueden ser errores humanos. Ordenes
  CEN reales llegan con guion y numericas (SUPERINDEPENDIENTES, CADENAS);
  no filtrar por un solo formato. Motivo de rechazo (col 62) aparece en
  devoluciones formales (4,965 lineas) Y en ventas no-devolucion (2,001
  lineas). Cambios: (a) AGENTS.md con 5 hallazgos nuevos; (b) prompts de
  crew.py (SchemaScout/MappingArchitect/KpiDesigner) reforzados para
  anticipar canales sin orden, no filtrar keys a un formato, y proponer
  breakdowns de devoluciones + rechazos + canal; (c) schema BreakdownSpec
  extendido con filter_not_equals y require_non_null (engine.py); (d)
  perfil borrador con 3 breakdowns nuevos (devoluciones_por_distrito,
  rechazos_no_devolucion, ventas_por_canal) + hojas Excel + pagina PBIP
  "Rechazos y canales". Regenerado con `scripts/regenerar_dashboard_p6.py`:
  7 paginas, 11 tipos de visual (donut, barras, columnas apiladas, funnel,
  linea, matriz, tabla, cards). Verificacion numerica PBIP OK (motor==fact,
  DAX ok). Suite 113 + 22 tests verdes. Backlog vigilado: canal en blanco
  en ventas_por_canal (4 filas, 19M unidades) parece filas totales de SAP;
  preguntar al usuario. El fix del agente Motor (normalizadores de join que
  colapsan grano -> 422) queda en el bloque de mejora de agentes.

- 2026-07-09 (3): POSTMORTEM de la corrida real P6/junio que reporto 5% de
  entrega y PBIP sin las mejoras 4B. Dos causas raiz verificadas:
  (1) BACKEND VIEJO otra vez: uvicorn en 8000 arranco 5:25 AM y
  render_pbip.py/crew.py/profile.py se editaron 7:37-7:46 AM; el PBIP salio
  sin drill pages ni theme con dropShadow. Reincidencia de la leccion
  operativa ya documentada: SIEMPRE reiniciar backend tras editar codigo.
  (2) FILTRO DE ORDENES INCORRECTO en la respuesta simulada de la
  entrevista: se instruyo filtrar SAP col56 a formato 'NNN-...', pero el
  CEN P6 trae ordenes en varios formatos (con guion '003-0023901' y
  numericas '0020018102'). Auditoria matematica independiente
  (`scripts/auditoria_p6_junio.py`, pandas puro): con el filtro regex
  matched=2,026 (tasa 24.5%, no_entregado 82.7%); sin el filtro (perfil
  borrador de referencia) matched=5,533 (tasa 60.3%, completos 36.3%,
  parciales 35.4%, no_entregados 28.3%, cumplimiento unidades ~110%).
  Los 3,507 matches perdidos eran ordenes numericas. Extra: CEN P6 trae
  40 filas de mayo y 24 de julio en 'F. Documento O/C' (0.5%, no driver
  del error, pero los agentes deben reconocerlo). KPI corrupto de esa
  corrida (devoluciones = entregadas) tambien venia de la entrevista mal
  guiada.   Correccion: respuesta del script reescrita (no filtrar por
  formato de orden, solo placeholders; devoluciones solo tipo operacion
  DEVOLUCIONES), backend reiniciado con codigo fresco y corrida
  relanzada (`e2e_real_p6_v2_*`). La corrida por LLM volvio a fallar en
  `/generate` con 422 (order de transforms vs normalizadores del join
  genera 3 keys duplicadas post-normalizacion, bug estructural distinto).
  Camino deterministico usado para cerrar el gate de dashboard:
  `scripts/regenerar_dashboard_p6.py` corre el borrador ya validado
  (`profiles/cen_vs_sap_v1_borrador.json`) con los renderers actuales.
  Resultado: matched=5,533, cumplimiento_unidades=109.85%, pedidos
  completos=36.34%, parciales=35.38%, no_entregados=28.29% (identicos a
  la auditoria pandas). PBIP nuevo trae paginas 'Nivel de servicio',
  'Clientes y materiales', 'Devoluciones', 'Logistica', 'Venta',
  'Destinatario x causal', theme con dropShadow y variedad de charts
  (card, clusteredBarChart, donutChart, funnel,
  hundredPercentStackedColumnChart, lineChart, pivotTable, slicer,
  tableEx). Backlog abierto: agente Motor debe evitar generar
  normalizadores de join incompatibles con el group_by previo.

- 2026-07-09: Ejecucion E2E real CEN vs SAP por API (Gemini real) con
  `scripts/e2e_flujo_cen.py` ya parametrizable (`--left/--right/--homologacion`).
  Corridas relevantes:
  `e2e_real_cen_1783609644` (P7/junio, diagnostico de periodo no equivalente),
  `e2e_real_cen_p6_1783610547` (P6/junio, cruce exitoso) y
  `e2e_real_cen_p6_fix_1783610938` (P6/junio, respuestas reforzadas para
  mapping SAP col56/40/42 y nivel de servicio). Resultado final validado:
  `POST /generate` 200, archivos descargables
  `Reporte Nivel de Servicio CEN-SAP_v2.xlsx` +
  `pbip_e2e_real_cen_p6_fix_1783610938_v2.zip`, con metrica de pedidos
  no triviales (completo/parcial/no_entregado) y telemetria registrada.

- 2026-07-09: Bloque "diseno elegible por el usuario + dashboard mas
  profesional" (feedback del usuario con capturas). Contrato:
  `PowerBIDesignPrefs` (theme corporativo/claro/oscuro, max_paginas,
  max_charts_por_pagina, tipos_preferidos, incluir_paginas_drill,
  notas_usuario) en `report.powerbi.design`, y nuevos tipos de visual
  `funnel`, `area`, `columnas_apiladas` (patrones del PBIX de referencia).
  Renderer: `build_theme(variant)` con 3 variantes, sombra suave
  (dropShadow), esquinas radius 8, callout de cards 30pt, estilo de
  slicers, y `_apply_design_prefs` que respeta las preferencias del
  usuario de forma deterministica. Agentes: ReportDesigner ahora propone
  `design` y emite SIEMPRE una pregunta (no bloqueante) sobre hojas,
  cantidad de graficos, tipos y tema. UI: nueva seccion "Diseno del
  reporte Power BI" en el detalle del proceso: muestra la propuesta por
  pagina y permite elegir paginas/graficos/tipos/tema con chips y
  steppers o escribir respuesta libre; se envia al chat para el refine.
  Suite clave 119/119 verde; build Angular limpio (budget
  anyComponentStyle 6->8kB por crecimiento legitimo del detalle).

- 2026-07-09: Ajuste UX PBIP por feedback visual del usuario (post 4B-E):
  slicers de paginas drill ampliados (alto/ancho) para mejor legibilidad,
  normalizacion de categorias vacias en charts (evita "(Blank)" en motivos),
  y mayor variedad de visuales en drill pages (cards + line + donut +
  barras + matriz + tabla) con enfasis en lectura grafica del nivel de
  servicio ademas del valor numerico. Validado con `test_render_pbip.py`
  verde y `scripts/verificacion_fase6.py` OK.

- 2026-07-09: Ejecucion real de `scripts/verificacion_fase6.py` completada.
  Resultado automatizado: OK (94 tests clave verdes, verificacion numerica
  PBIP CEN/SAP en tolerancia 0.01, build frontend verde). Se detecto y
  corrigio un fallo de regresion en `test_cen_vs_sap_end_to_end_pbip`
  relacionado con la presencia de la pagina drill "Logistica" en PBIP.
  Pendientes siguen siendo los manuales de rubrica (apertura Desktop,
  E2E UI no tecnico, bateria adversa manual y cierre de costos por periodo).

- 2026-07-09: Inicio de Fase 6 (documentacion de cierre). Se crearon
  borradores operativos en `docs/manual_uso_analistas.md`,
  `docs/manual_mantenimiento.md` y `docs/informe_presupuesto.md`, alineados
  con rubrica (gates de uso no tecnico, mantenimiento y costos medidos por
  telemetria). Pendiente: validar cada paso en ejecucion real y completar
  cifras finales por perfil/mes.

- 2026-07-09: Se agrego `scripts/verificacion_fase6.py` para ejecutar en
  una corrida los checks automatizables de cierre (pytest clave, verificacion
  PBIP numerica y build frontend) y listar pendientes manuales de rubrica.

- 2026-07-09: Fase 4B-F completada. Nuevo script
  `scripts/verificar_pbip_numeros.py` valida numeros del PBIP contra el
  motor deterministico (unidades pedidas/entregadas/sin pedido, NS en %,
  pedidos completos %) y ademas verifica fragmentos DAX esperados en el
  TMDL del fact. Soporta validar PBIP existente o generar temporal para
  chequeo. Se agrego `tests/test_verificar_pbip_numeros.py` (unitarios de
  calculo + E2E con fixtures CEN/SAP).

- 2026-07-09: Fase 5 (pendiente menor) cerrada en UI de detalle:
  `profile-detail` ahora muestra resumen legible de `service_level`,
  `breakdowns` y `data_model` (ya no solo JSON crudo), con formulas y
  dimensiones visibles para analista no tecnica.

- 2026-07-09: Fase 4B-E completada en renderer PBIP. Se agrego extension
  automatica del spec para paginas drill de negocio cuando el profile trae
  service_level + data_model: "Logistica", "Venta" y "Destinatario x
  causal" (sin duplicar si ya existen). Todas las paginas secundarias
  incluyen boton visual "Volver a Nivel de servicio" en el header para
  navegacion consistente. Tests de PBIP actualizados para validar presencia
  de las nuevas paginas y del boton de retorno.

- 2026-07-09: Bloque "ingesta y aplicacion de homologacion en pipeline +
  PBIP/Excel" implementado. Backend: `POST /profiles/{id}/homologacion`
  para adjuntar catalogos durante la entrevista; `POST /profiles/draft`
  ahora acepta `homologacion_file` opcional; ambos registran evidencia en
  `profile_knowledge`, importan a `sku_catalog` via
  `import_from_homologacion`, y en `run/generate` se enriquece el resultado
  con columnas `*_homologado` (matched/solo/breakdowns) priorizando fuentes
  manual > aprendido_pair > homologacion. Frontend: alta de proceso con
  slot opcional de homologacion y boton "Adjuntar homologacion" en detalle
  del proceso para no reiniciar el flujo cuando el agente la pide en chat.
  Build Angular verde.

- 2026-07-09: Robustez UI + entrevista de homologaciones. Frontend:
  `profiles.service.ts` ahora normaliza `apiBaseUrl` para evitar choque
  localhost vs 127.0.0.1 (error de red status=0 en Generate), y
  `profile-detail.component.ts` muestra mensaje accionable cuando no hay
  conexion al backend. Agentes: prompts en `app/agents/crew.py` y memoria
  en `AGENTS.md` reforzados para pedir archivo de homologacion cuando se
  detecten codigos de material/cliente/distrito sin semantica clara.

- 2026-07-09: E2E completo API con Gemini real EXITOSO (profile
  e2e_cen_1783571809 v2: draft -> 17 preguntas -> respuestas -> refine ->
  approve -> generate 200 -> Excel + PBIP zip descargables). Fixes que lo
  desbloquearon: `_auto_fix_grano` en orchestrator + `_ejecutar_aprobado`
  (agentes ponian columnas descriptivas en group_by.by dejando keys
  repetidas -> 422), `_match_breakdown` tolerante a nombres semanticos
  (ReportDesigner declaraba medidas sobre `FactDevoluciones` cuando el
  breakdown real se llama distinto -> 500), medidas sobre tablas
  inexistentes se omiten sin tumbar el render. Fase 4B avanzada:
  4B-A/B/C/D parciales (medidas NS separadas, slicers via dimensiones,
  labels humanos estado_entrega, logo + textbox titulo + fondo navy-50).
  Fase 5: boton "Generar entregables" + lista de descargas en el detalle.
  Suite 344/344. LECCION OPERATIVA: el backend uvicorn en 8000 quedo
  corriendo con codigo viejo ~12h (los PBIP generados via API salian sin
  los fixes 4B); verificar SIEMPRE que la fecha de inicio del proceso sea
  posterior al ultimo edit de render_pbip.py antes de validar salidas.

- 2026-07-08 (2): Requisitos de negocio del caso CEN incorporados al
  contrato y al motor: ServiceLevelSpec (clasificacion completo/parcial/
  no_entregado/sin_pedido por linea Y por pedido, en unidades y %),
  BreakdownSpec (por_material, por_distrito, por_cliente,
  por_cliente_material, devoluciones_por_motivo con universo right_source
  + filtro DEVOLUCIONES), FilterNotEquals, DataModelSpec (fact +
  dimensiones con ids/FKs via app/platform/data_model.py), y
  PowerBIMeasureSpec/pages con proposito/visuals con justificacion.
  Semantica de columnas SAP descubierta y documentada en AGENTS.md
  (distrito col 11, cliente col 29, tipo operacion col 13 con
  DEVOLUCIONES ~10%, motivo devolucion col 62). Borrador
  cen_vs_sap_v1_borrador.json v2 con todo lo anterior. Suite 301/301.
  PENDIENTE: extender render_excel/render_pbip (Fase 4) para consumir
  breakdowns + data_model + measures cuando el subagente de renderers
  reporte su version base.

- 2026-07-08: Fase 0 ejecutada y gate calificado como `cumple` en
  rubrica.md. Exploracion CEN/SAP completada (hallazgos en AGENTS.md
  seccion "Caso de validacion #2"). Fixtures creados y verificados.
  Documentos de memoria escritos, auditados por subagente de verificacion
  independiente (11 inconsistencias detectadas y corregidas antes de
  cerrar el gate). Tarea diferida: mover el repo a ruta limpia
  (coordinacion con el usuario, no bloquea Fase 1).
