// TypeScript interfaces equivalentes a los schemas del backend de la
// plataforma generica (app/api/routes/profiles.py, app/platform/profile.py,
// app/agents/schemas.py). Mantener sincronizado.

export type ProfileStatus = 'draft' | 'proposed' | 'approved' | 'archived';

export interface ProfileSummary {
  profile_id: string;
  version: number;
  status: ProfileStatus;
  creado_en: string | null;
  aprobado_en: string | null;
  aprobado_por: string | null;
}

export interface ProposalStatus {
  profile_id: string;
  listo_para_aprobar: boolean;
  preguntas_abiertas: number;
  preguntas_bloqueantes: number;
  confianza_global: number;
}

// ---------------------------------------------------------------------------
// MatchProfile (el backend serializa con exclude_none: campos opcionales
// pueden venir ausentes)
// ---------------------------------------------------------------------------

export interface ColumnSpec {
  name: string;
  source: string | number;
  dtype?: string;
  date_format?: string | null;
  required?: boolean;
}

export interface LoaderSpec {
  type: 'tabular' | 'registered';
  // tabular
  sheet?: string | number | null;
  header_row?: number | null;
  columns?: ColumnSpec[];
  drop_rows_where_null?: string[];
  // registered
  name?: string;
}

export interface AggregationSpec {
  target: string;
  source: string;
  fn: string;
}

export interface TransformSpec {
  op:
    | 'filter_equals'
    | 'filter_not_equals'
    | 'filter_regex_match'
    | 'group_by_aggregate'
    | 'select_rename'
    | 'unpivot';
  column?: string;
  value?: string | number;
  pattern?: string;
  keep?: 'matched' | 'not_matched';
  by?: string[];
  aggregations?: AggregationSpec[];
  mapping?: Record<string, string>;
  id_vars?: string[];
  value_vars?: string[] | null;
  var_name?: string;
  value_name?: string;
  drop_null_values?: boolean;
}

export interface SourceSpec {
  role: string;
  label: string;
  loader: LoaderSpec;
  transforms?: TransformSpec[];
}

export interface JoinKey {
  left: string;
  right: string;
  normalizers?: string[];
}

export interface JoinSpec {
  keys: JoinKey[];
  type?: 'outer';
}

export interface ComputedColumn {
  name: string;
  op: 'subtract' | 'ratio_pct';
  left: string;
  right: string;
  round?: number | null;
}

export interface SemaforoSpec {
  verde_min: number;
  verde_max?: number | null;
  amarillo_min: number;
  amarillo_max?: number | null;
}

export interface KpiSpec {
  id: string;
  label: string;
  op: 'ratio_pct_of_sums' | 'sum' | 'count' | 'distinct_count';
  numerator?: string | null;
  denominator?: string | null;
  semaforo?: SemaforoSpec | null;
}

export interface ParameterSpec {
  name: string;
  type: 'date' | 'str' | 'int' | 'float';
  description?: string;
  required?: boolean;
}

export interface ServiceLevelSpec {
  plan_column: string;
  real_column: string;
  pedido_key?: string | null;
  tolerancia_pct?: number;
}

export interface BreakdownMetricSpec {
  id: string;
  op: 'sum' | 'count' | 'ratio_pct_of_sums';
  column?: string;
  numerator?: string;
  denominator?: string;
}

export interface BreakdownSpec {
  id: string;
  label: string;
  dimensions: string[];
  metrics: BreakdownMetricSpec[];
  universe?: 'matched' | 'left_full' | 'right_source';
  filter_equals?: Record<string, string | number>;
  top_n?: number | null;
  sort_by_metric?: string | null;
}

export interface DimensionSpec {
  name: string;
  key: string;
  attributes?: string[];
}

export interface DataModelSpec {
  fact_name: string;
  dimensions: DimensionSpec[];
  include_unmatched?: boolean;
}

export type PowerBIVisualKind =
  | 'card_kpi'
  | 'tendencia'
  | 'barras_categoria'
  | 'tabla_detalle'
  | 'donut'
  | 'matriz'
  | 'funnel'
  | 'area'
  | 'columnas_apiladas';

export type PowerBIThemeVariant =
  | 'nutriavicola'
  | 'nutriavicola_claro'
  | 'nutriavicola_oscuro';

export interface PowerBIDesignPrefs {
  theme?: PowerBIThemeVariant;
  max_paginas?: number;
  max_charts_por_pagina?: number;
  tipos_preferidos?: PowerBIVisualKind[];
  incluir_paginas_drill?: boolean;
  notas_usuario?: string;
}

export interface PowerBIVisualSpec {
  kind: PowerBIVisualKind;
  title: string;
  measure?: string | null;
  category?: string | null;
  table?: string | null;
  justificacion?: string;
}

export interface PowerBIPageSpec {
  name: string;
  proposito?: string;
  visuals: PowerBIVisualSpec[];
}

export interface PowerBIReportSpec {
  theme?: string;
  design?: PowerBIDesignPrefs | null;
  measures?: unknown[];
  pages: PowerBIPageSpec[];
}

export interface ReportSpec {
  excel?: unknown;
  powerbi?: PowerBIReportSpec | null;
}

export interface MatchProfile {
  profile_id: string;
  version: number;
  schema_version?: number;
  descripcion?: string;
  parameters?: ParameterSpec[];
  left: SourceSpec;
  right: SourceSpec;
  join: JoinSpec;
  computed?: ComputedColumn[];
  kpis: KpiSpec[];
  service_level?: ServiceLevelSpec | null;
  breakdowns?: BreakdownSpec[];
  data_model?: DataModelSpec | null;
  unmatched_motivo_left?: string;
  unmatched_motivo_right?: string;
  report?: ReportSpec | null;
}

// ---------------------------------------------------------------------------
// Respuestas de endpoints
// ---------------------------------------------------------------------------

export interface DraftResponse {
  profile_id: string;
  status: ProposalStatus;
  preguntas_nuevas: number;
  profile: MatchProfile;
  justificaciones: { mapping: string; kpis: string; report: string };
  resumen_fuentes: { left: string; right: string };
}

export interface OpenQuestionRow {
  id: number;
  profile_id: string;
  agente: string;
  sobre: string | null;
  pregunta: string;
  hipotesis: string | null;
  impacto: string | null;
  bloqueante: number;
  estado: QuestionEstado;
  respuesta: string | null;
  creado_en: string | null;
  respondido_en: string | null;
}

export interface ProposalResponse {
  profile: MatchProfile;
  status: ProposalStatus;
  preguntas_abiertas: OpenQuestionRow[];
}

export type ChatTipo = 'brief' | 'qa' | 'correccion' | 'nota' | 'pregunta';
export type ChatRole = 'usuario' | 'agente' | 'sistema';
export type QuestionEstado = 'abierta' | 'respondida' | 'asumida';

export interface ChatMessage {
  tipo: ChatTipo;
  role: ChatRole;
  autor: string;
  contenido: string;
  timestamp: string | null;
  // Solo cuando tipo === 'pregunta':
  hipotesis?: string | null;
  impacto?: string | null;
  bloqueante?: boolean;
  estado?: QuestionEstado;
  respuesta?: string | null;
  question_id?: number;
}

export interface ChatPostResponse {
  ok: boolean;
  status: ProposalStatus;
}

export interface RefineResponse {
  profile_id: string;
  version: number;
  status: ProposalStatus;
  preguntas_nuevas: number;
  profile: MatchProfile;
}

export interface ApproveResponse {
  ok: boolean;
  profile_id: string;
  version: number;
}

export interface UpdateProfileResponse {
  ok: boolean;
  version: number;
}

export interface RunInfo {
  run_id: number;
  reemplazado: boolean;
  filas_cruce: number;
  filas_no_cruzados: number;
}

export interface RunSummary {
  matched: number;
  solo_left: number;
  solo_right: number;
  no_cruzados: number;
}

export interface RunResponse {
  ok: boolean;
  run: RunInfo;
  summary: RunSummary;
  kpis: Record<string, unknown>;
}

export interface GenerateResponse extends RunResponse {
  archivos: string[];
}

export interface DownloadItem {
  filename: string;
  kind: string;
  size_bytes: number;
}

export interface ProfileRun {
  id: number;
  profile_id: string;
  profile_version: number;
  left_hash: string;
  right_hash: string;
  left_filename: string | null;
  right_filename: string | null;
  ejecutado_en: string;
  kpis: Record<string, unknown>;
  summary: RunSummary;
  params: Record<string, string>;
}

export interface TelemetrySummary {
  llamadas: number;
  input_tokens: number;
  output_tokens: number;
  costo_usd: number;
  latencia_media_ms: number;
}

// ---------------------------------------------------------------------------
// Helpers de presentacion compartidos por los componentes de profiles
// ---------------------------------------------------------------------------

export function profileStatusLabel(status: ProfileStatus): string {
  switch (status) {
    case 'draft': return 'Borrador';
    case 'proposed': return 'Propuesto';
    case 'approved': return 'Aprobado';
    case 'archived': return 'Archivado';
  }
}

// Reusa las clases semanticas de .nutri-status definidas en styles.scss.
export function profileStatusChipClass(status: ProfileStatus): string {
  switch (status) {
    case 'draft': return 'draft';
    case 'proposed': return 'ready_to_match';
    case 'approved': return 'matched';
    case 'archived': return 'archived';
  }
}

export function kpiFormulaLegible(kpi: KpiSpec): string {
  switch (kpi.op) {
    case 'ratio_pct_of_sums':
      return `suma(${kpi.numerator}) / suma(${kpi.denominator}) x 100`;
    case 'sum':
      return `suma(${kpi.numerator})`;
    case 'distinct_count':
      return `conteo distinto(${kpi.numerator ?? 'columna'})`;
    case 'count':
      return 'conteo de filas cruzadas';
  }
}
