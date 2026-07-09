# AGENTS.md - Memoria persistente de la Plataforma Multiagente de Cruces

Guia de contexto que TODO agente (humano o AI, principal o subagente) debe
leer completa antes de trabajar en este repositorio. Este documento es la
fuente de verdad del proyecto actual. La historia completa del MVP anterior
(PRE CORTE vs FLASH, Fases 0 a 7E) vive en
[docs/AGENTS_LEGACY.md](docs/AGENTS_LEGACY.md) y sigue siendo valida como
referencia tecnica de los modulos existentes.

Documentos hermanos obligatorios:

- [road.md](road.md) - roadmap por fases, estados, criterios de salida,
  decision log. Se actualiza al terminar cada bloque de trabajo.
- [rubrica.md](rubrica.md) - metricas de autoevaluacion por fase y del
  producto. Cada gate de fase se califica contra ella.

---

## system_role

Analista de inteligencia de negocio y datos, especializado en automatizacion
de procesos BI por medio de agentes, flujos (n8n, Power Automate, Copilot
Studio), modelos de lenguaje y machine learning. El desarrollo se enfoca en
desarrollo agentico web, procesos y plataformas para automatizar y disminuir
tiempos de trabajo y aumentar eficiencia de BI. No se enfoca en programar
por programar: entiende los procesos detras de cada tarea para brindar la
mejor solucion calidad/precio, justa y perfecta a lo que se requiere.

Experiencia en escalabilidad de automatizacion de procesos: se evita tener
80 automatizaciones diferentes para procesos similares; se evita parchear
repetidamente un problema que puede resolverse cambiando el sistema en su
totalidad.

## Contexto de negocio

Empresa avicola en Colombia (NutriAvicola, +2000 empleados). El equipo de
BI cruza tablas de distintas fuentes (Excel, exports SAP, plataformas B2B)
para medir cumplimiento de ordenes de compra, produccion, etc. Ese cruce
manual (archivo por archivo, fecha por fecha, key por key) es lento y
repetitivo.

**Lo que ya existe (MVP, 100% funcional):** el flujo PRE CORTE vs FLASH,
que mide cumplimiento de produccion de huevo. Pipeline deterministico
FastAPI + SQLite + Angular + n8n + export Excel corporativo. Ver
[docs/AGENTS_LEGACY.md](docs/AGENTS_LEGACY.md). Su limitacion: es un parche
mono-proceso. Cada analista del pais tiene archivos con estructuras
distintas; el MVP solo entiende UNA estructura exacta.

**Lo que estamos construyendo (proyecto actual):** la plataforma definitiva
y generica de cruces de datos. Un equipo de agentes LLM (Gemini 2.5 Flash)
lee los archivos que suba cualquier analista, entrevista al usuario como lo
haria un analista humano, propone el mapeo completo (estructura, keys,
transformaciones, KPIs, diseno de reporte) como un contrato JSON llamado
**MatchProfile**, y un motor deterministico auditable ejecuta el cruce.
Salidas: base de datos acumulada + Excel corporativo + proyecto Power BI
(PBIP) con visuales de marca disenadas por nosotros.

## Compromisos no negociables (heredados y vigentes)

- **Cero perdida de datos**: cada fila se contabiliza; si no cruza, va a
  `no_cruzados` con motivo. Hash SHA256 del binario original.
- **LLM propone, nunca calcula**: Gemini interpreta estructura, propone
  mapeos y KPIs, y conversa con el usuario. El cruce, las agregaciones y
  los KPIs los ejecuta SIEMPRE el motor deterministico a partir del
  MatchProfile aprobado. Sin eval libre de formulas: solo operaciones
  declarativas validadas.
- **Puntos de control humanos explicitos**: nada se persiste ni se ejecuta
  sin aprobacion visual del MatchProfile. Preguntas bloqueantes de los
  agentes detienen la propuesta hasta ser respondidas.
- **Todo inferido para el usuario** (reforzado por el usuario 2026-07-08):
  la persona operativa SOLO sube archivos, describe que quiere medir en
  lenguaje natural y responde preguntas. Nunca configura columnas, hojas,
  formatos, parametros tecnicos ni rutas. Esto aplica tambien a los
  entregables: el Excel abre listo, y el PBIP debe cargar datos sin que
  el usuario entienda parametros de Power Query (instrucciones de 2 pasos
  maximo). Si un paso requiere conocimiento tecnico, es un bug de diseno.
- **Principio de entrevista**: los agentes preguntan como personas, no
  asumen. Mientras analizan, anotan dudas en una cola tipada
  (`open_questions`). Ejemplos canonicos: detectar el grano de fila
  ("encontre numeros de orden repetidos en varias filas: cada fila es un
  producto de la orden y debo agrupar por numero de orden para reconstruir
  la orden completa?"), keys que se llaman distinto en cada archivo,
  significado de filas sin key, y solicitud de archivos de homologacion
  (material/cliente/distrito) cuando existan codigos sin semantica clara.
  Cada pregunta lleva la hipotesis del
  agente, el impacto de no responderla y si es bloqueante. Lo respondido
  se persiste en la memoria del proceso y NUNCA se vuelve a preguntar.

## Concepto central: MatchProfile

Contrato JSON versionado (Pydantic) que describe UN proceso de cruce
completo: fuentes y sus loaders (hoja, fila de header, column_map,
normalizaciones, agrupacion previa por grano), join (keys izquierda/derecha
con normalizadores), KPIs declarativos con semaforo, y spec del reporte
(hojas Excel + paginas/visuales Power BI). Los agentes lo proponen, el
humano lo aprueba (con ediciones), el motor lo ejecuta. El caso PRE CORTE
vs FLASH se re-expresa como `pre_corte_v1.json` y es el test de regresion
permanente de la generalizacion.

## Equipo de agentes (Gemini 2.5 Flash via Pydantic AI)

| Agente | Rol humano equivalente | Produce |
|---|---|---|
| SchemaScout | Ingeniero de datos | Estructura inferida por fuente: hojas, header row, tipos, grano de fila (obligatorio detectarlo), calidad, anomalias |
| MappingArchitect | Ingeniero ETL | column_map, keys de cruce, normalizaciones, agrupaciones previas |
| KpiDesigner | Analista de datos | KPIs, formulas declarativas, agregaciones, semaforos |
| ReportDesigner | Analista BI | Estructura y diseno visual del Excel y del PBIP con marca |

Todos reciben: metadatos + muestras de los archivos, el brief del usuario
en el chat, y la memoria acumulada del proceso (`profile_knowledge`).
Todos emiten: fragmento tipado del MatchProfile + `open_questions` +
score de confianza + justificacion legible.

Privacidad: API Gemini de pago (sin retencion de datos para entrenamiento).
Por diseno se envian metadatos + muestras; archivo completo solo como
escalacion explicita.

## Caso de validacion #2: CEN vs SAP (nivel de cumplimiento de entregas)

Proceso real de una analista: las ordenes de compra B2B llegan por la
plataforma CEN (ej. cadenas de supermercados pidiendo huevo); lo entregado
queda registrado en SAP. CEN tiene TODAS las ordenes (entregadas o no);
SAP solo lo facturado/entregado. KPI: nivel de cumplimiento de entregas.

Datos reales en `data_nivel_cumplimiento/` (gitignored, NO commitear):

- **CEN**: `2026/Acumulado CEN P{1..7} 2026.xlsx`. P = periodo (~mes; P1
  cubre 2026-01-02 a 2026-02-01). Tabla relacional de 30 columnas, header
  en fila 1. **Grano: fila = linea de producto de la orden** (P1: 10,624
  filas, 2,619 ordenes unicas; `Numero de la Orden de compra` se repite,
  ej. `004-0018849`). Columnas clave: `Numero de la Orden de compra`,
  `Codigo item proveedor` (material SAP, ej. `30049`), `Cantidad Total`,
  `F. Documento O/C`, fechas min/max/solicitada de entrega. Trampas
  conocidas: el nombre de la hoja varia (`Hoja2` en P1, `Hoja1` en P2-P7,
  y P2 trae ademas una hoja pivote `Hoja3` que NO es data), P4 declara
  1,048,576 filas fantasma (dimension inflada; hay que cortar en la ultima
  fila real), strings con mojibake (`Ca?averal`).
- **SAP**: `data meses/{enero..junio}.XLS`. **La extension miente: son
  xlsx renombrados** (firma PK zip; openpyxl los abre si se copian con
  extension .xlsx). Hoja `Sheet1`, **sin fila de encabezados** (fila 1
  vacia), 70 columnas anonimas, ~39,000 filas/mes. Contiene TODAS las
  ventas, no solo CEN: col 56 trae el numero de orden CEN solo en filas
  originadas alli; col 40 = codigo material (cruza con `Codigo item
  proveedor` del CEN); col 41 = descripcion; col 42 = cantidad; col 43 =
  unidad (UN/PAN); cols 22-23 y 60-61 = fechas; cols 6-7 = canal (PUNTOS
  PROPIOS, TAT, HARD DISCOUNT...); col 19/57 = clase de documento (ZBVN,
  ZNTT, ZNAH...).
- **Cruce esperado** (a confirmar por la entrevista de los agentes, no
  hardcodear): `Numero de la Orden de compra` del CEN (col 6, con header)
  <-> col 56 del SAP (posicional, sin header); y a nivel item ademas
  `Codigo item proveedor` del CEN (col 19) <-> col 40 del SAP. Las keys se
  llaman distinto (o ni siquiera tienen nombre) en cada archivo: ese es
  precisamente el problema que la plataforma resuelve. OJO: la col 56 del
  SAP ademas de ordenes CEN trae pedidos de otros origenes ('261357',
  '4503551545') y placeholders escritos a mano ('SIN DC', 'SIN ORDEN',
  'sin oc', '*') - filtrado fino es tema de entrevista.
- **Semantica de columnas SAP descubierta con datos reales** (2026-07-08,
  perfil de 8,000 filas de enero): col 6/7 = canal (TAT,
  SUPERINDEPENDIENTES, PUNTOS PROPIOS, HARD DISCOUNT...); col 10/11 =
  DISTRITO/regional (NUTRIAVICOLA CALI, TULUA, BOGOTA, IBAGUE...);
  col 12/13 = tipo de operacion, incluye 'DEVOLUCIONES' (~10% de filas);
  col 19/57 = clase de documento (ZNAH, ZNTT, ZBVN...); col 24/25 =
  condicion de pago; col 28/29/30 = CLIENTE (codigo, razon social, NIT);
  col 31/32 = segmento del cliente (CADENAS, SUPERINDEPENDIENTES1...);
  col 40/41 = material y descripcion; col 42/43 = cantidad y unidad
  (UN/PAN/LB); col 44/45 = categoria de huevo (Huevo AA, AAA, Sucio...);
  col 47-53 = precios/valores COP; col 62/63 = MOTIVO DE RECHAZO/DEVOLUCION
  ('ROTURA GENERADA EN SUPERMERCADO', 'ROTURA TRANSPORTE-LOGISTICA',
  'FECHA PROXIMA A VENCER-MERCANCIA'...).
- **Ventas SIN codigo de orden = canales de venta directa, NO errores**
  (2026-07-09, verificado sobre junio real, 37,052 filas): ~57% de las
  filas SAP no tienen numero de orden CEN en col 56 porque son canales que
  NO pasan por la plataforma CEN, se venden manual/directo. Concentracion
  por canal (col 7): TAT 99.4% sin codigo (11,001 de 11,064), PUNTOS
  PROPIOS 99.9% (8,443), EMPLEADOS 99.3%, INSTITUCIONALES 48%, SUPERETES
  43%. Estas ventas quedan como solo_right (universo right) y son legitimas:
  reportarlas como "ventas directas por canal", nunca contarlas como
  faltante de CEN ni como error. SOLO los placeholders de texto escritos a
  mano ('*', 'SIN DC', 'SIN ORDEN', 1,652 filas) pueden ser errores humanos.
- **Formatos del numero de orden CEN (col 56) varian; NO filtrar por un
  unico formato** (2026-07-09): las ordenes CEN reales llegan con guion
  ('003-0023901') o numericas ('261357', '4241151659'), e incluso con
  prefijos ('011 OC-009306', 'CT16332727'). Vienen sobre todo de
  SUPERINDEPENDIENTES (guion+numerico) y CADENAS (numerico). Cruzar por
  codigo con normalizacion (strip, lstrip_zeros); el outer join descarta lo
  que no coincide. Filtrar SAP col 56 solo a formato con guion PIERDE ~3,500
  lineas numericas validas y hunde el nivel de servicio a un falso ~5%
  (error real cometido y corregido 2026-07-09).
- **Motivo de rechazo (col 62) aparece en dos contextos** (2026-07-09):
  (a) en DEVOLUCIONES formales (tipo_operacion=DEVOLUCIONES, col 13; junio:
  4,965 lineas, ~129k unidades devueltas) y (b) en ventas que NO son
  devolucion pero traen un motivo operativo (junio: 2,001 lineas: ROTURA,
  ERROR ALISTAMIENTO, INCUMPLIMIENTO HORARIO...). Reportar AMBOS por
  separado: "devoluciones por motivo" y "rechazos en ventas (no devolucion)
  por motivo". El motivo esta en blanco en la gran mayoria de ventas
  normales (no es un error, simplemente no hubo incidencia).
- **tipo_operacion (col 13) NO es binaria**: junio trae 'HUEVO EMPACADO'
  (10,527), 'DEVOLUCIONES' (4,965), 'PUNTO DE VENTA', 'PUNTO VTA RETORN',
  'PTO VENTA CALI', etc. DEVOLUCIONES es un valor especifico entre muchos;
  no asumir que "no DEVOLUCIONES" = venta normal a supermercado.
- **Fechas en el CEN mensual pueden desbordar el mes** (2026-07-09): el
  Acumulado CEN P6 trae 40 filas de mayo y 24 de julio en 'F. Documento
  O/C' ademas de junio. El periodo es aproximado; se cruza el archivo
  completo sin filtrar por fecha, pero los agentes deben RECONOCER y
  MENCIONAR estos dias de borde al usuario.

**Requisitos de negocio del caso CEN vs SAP** (usuario, 2026-07-08):

1. La salida debe ser data usable y visible como en el MVP pero mejor
   estructurada y con mas valor de insight (Excel + Power BI + historico
   en base de datos).
2. **Nivel de servicio**: cuantos pedidos hay, cuales se entregaron
   (completos/parciales) y cuales no, expresado EN PORCENTAJE Y EN
   UNIDADES.
3. **KPIs de motivos de rechazo/devoluciones**: unidades devueltas y
   conteo por motivo (col 62 del SAP) y por tipo de operacion DEVOLUCIONES.
4. **Clasificacion dimensional de los pedidos**: por material, por
   distrito, por cliente, y por cliente+material.
5. **Formato final = base de datos en Excel** (aplica al caso CEN y a la
   mayoria): tablas completamente funcionales y planas (nunca cross-tab),
   con ids y foreign keys, listas para que el equipo de BI las importe a
   Power BI via Power Query si quieren modificar algo. ADEMAS se entrega
   el Power BI ya construido con buena estructura: tablas definidas,
   medidas, paginas con proposito y cada visual con justificacion. Todo
   lo propone el ReportDesigner (agente de BI) y lo aprueba el humano.

Soporte en el contrato: `MatchProfile.service_level` (clasificacion
declarativa completo/parcial/no_entregado por linea y por pedido),
`MatchProfile.breakdowns` (desgloses por dimensiones con metricas
declarativas, universos matched/left_full/right_source),
`MatchProfile.data_model` (fact + dimensiones con ids/FKs, construido por
`app/platform/data_model.py::build_data_model`) y
`report.powerbi.measures` + `pages[].proposito` + `visuals[].justificacion`
(diseno declarativo del tablero, sin DAX libre).
- Fixtures recortados para tests (estos SI van al repo):
  `tests/fixtures/cen/Acumulado CEN P7 2026.xlsx` (CEN completo mas
  liviano) y `tests/fixtures/cen/sap_junio_muestra.xlsx` (600 filas de
  junio: 400 con orden CEN + 200 sin).
- Todo se acumula en base de datos (como el historico SQLite del MVP)
  para tener mayor juego de datos, y luego se renderiza a Excel/Power BI.
  Que agrupar, como interpretar periodos sin fecha en el nombre, y que
  renderizar es exactamente lo que el multiagente debe inferir/preguntar,
  no una regla fija de este documento.

## Convenciones tecnicas

- Python >= 3.11, `pandas>=2`, `openpyxl`, `fastapi`, `pydantic-ai`,
  `streamlit` no (el frontend es Angular), `pytest`.
- Codigo de plataforma nueva en `app/platform/` (contrato, loader, motor)
  y `app/agents/` (capa LLM). El core legado en `app/core/` se generaliza
  sin romper sus tests (90+ verdes siempre).
- Nombres en espanol para dominio (cumplimiento_pct, no_cruzados), ingles
  para infraestructura (profile_id, run_id, hash_sha256).
- Sin emojis en codigo, comentarios ni docs. Sin comentarios que narran
  obviedades.
- Funciones puras en el core; efectos (I/O, DB, LLM) en modulos de borde.
- Telemetria LLM obligatoria: cada llamada registra tokens, costo,
  latencia, propuesta original vs aprobada. Alimenta [rubrica.md](rubrica.md).
- Los agentes constructores (subagentes de este proyecto) actualizan
  [road.md](road.md) al terminar su bloque y NO cambian tecnologias a
  mitad de construccion por practicidad o velocidad.

## Metodo de analisis generalizable (todo agente debe pensar asi)

Los hechos concretos del caso CEN vs SAP (canal TAT sin orden, motivo en
col 62, formatos de orden) son INSTANCIAS de principios generales. Ningun
agente debe memorizar solo el caso: debe interiorizar el metodo para
reproducir, en archivos nunca vistos, la misma calidad de analisis que un
analista humano experto. Estos principios viven tambien en el prompt
compartido `_METODO_ANALISIS` de `app/agents/crew.py` y aplican a los cuatro
agentes:

1. Verificar, no asumir: toda hipotesis se sostiene con evidencia del dato
   (distinct_ratio, muestras, conteos, cruce columna-contra-columna); lo no
   confirmado es `open_question`, no supuesto silencioso.
2. Las ausencias tienen significado: una key vacia/rara suele ser un
   subconjunto legitimo (otro canal, venta directa, otro sistema), no un
   error. Cruzarla contra columnas de contexto (canal, tipo, segmento) para
   explicarla y reportarla aparte.
3. Un campo puede tener varios formatos validos: no reducir una key a un
   unico patron sin evidencia; normalizar y dejar que el join descarte.
4. Una columna de estado/motivo/flag puede aplicar a varios contextos:
   separarlos y reportar cada uno.
5. Los archivos "de un periodo" pueden desbordarlo: detectar filas de borde
   y decidir con el usuario.
6. Desconfiar de sumas que no cuadran: totales/subtotales/fantasma inflan
   agregados; marcar cifras imposibles para el negocio.
7. El resultado debe ser verificable: KPIs contrastables contra un calculo
   simple e independiente; un valor absurdo obliga a revisar supuestos antes
   de proponer.
8. Pensar en uso empresarial: cada tabla/visual responde una pregunta de
   negocio y es legible por una persona no tecnica.

Cuando un agente humano (o el orquestador) descubra un patron nuevo
generalizable, debe anadirlo aqui y al prompt compartido, no dejarlo como
conocimiento de un solo caso.

## Que evitar

- LLM calculando KPIs o decidiendo cruces en runtime. Solo propone
  configuracion que el humano aprueba.
- Asumir el grano de una tabla sin confirmarlo con datos o con el usuario.
- Repetir preguntas ya respondidas en `profile_knowledge`.
- Persistir o ejecutar un profile con preguntas bloqueantes abiertas.
- Romper la regresion PRE CORTE: `pre_corte_v1.json` debe reproducir
  KPIs identicos a los del pipeline legado (mismos valores numericos;
  criterio exacto en rubrica.md, que es la fuente unica de los gates).
- Commitear datos reales de negocio (`data_nivel_cumplimiento/`, FLASH,
  PRE CORTE operativos). Solo fixtures recortados en `tests/fixtures/`.
- Formatear Excel fuera de `excel_style.py` (regla heredada, sigue viva).

## Estado de fases del proyecto actual

Ver [road.md](road.md) para el detalle vivo. Resumen:

- [x] **Fase 0** - Gobernanza y memoria: AGENTS.md nuevo, road.md,
  rubrica.md, fixtures CEN/SAP, exploracion de estructura real (2026-07-08).
- [x] **Fase 1** - Contrato MatchProfile + ConfigurableLoader + motor
  generico. PRE CORTE como profile #1 con regresion verde (2026-07-08).
  Modulos: app/platform/{profile,loader,engine,store}.py; profiles en
  profiles/*.json; 34 tests nuevos, suite 238/238.
- [x] **Fase 2** - Agentes Pydantic AI + chat de entrevista con cola de
  preguntas + memoria por proceso + telemetria (gate API real cumplido).
- [ ] **Fase 3** - Caso CEN vs SAP end-to-end con entrevista real.
- [ ] **Fase 4** - Renderers: Excel declarativo por profile + PBIP Power
  BI con tema de marca.
- [ ] **Fase 5** - Frontend Angular generico con chat + n8n por profile.
- [ ] **Fase 6** - Verificacion intensiva + informes de uso, mantenimiento
  y presupuesto.
