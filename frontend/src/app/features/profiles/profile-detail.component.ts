import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ProfilesService } from '../../core/profiles.service';
import {
  BreakdownSpec,
  DataModelSpec,
  DimensionSpec,
  ChatMessage,
  GenerateResponse,
  DownloadItem,
  KpiSpec,
  LoaderSpec,
  PowerBIDesignPrefs,
  PowerBIPageSpec,
  PowerBIReportSpec,
  PowerBIThemeVariant,
  PowerBIVisualKind,
  ProfileRun,
  ProfileStatus,
  ProposalResponse,
  RunResponse,
  ServiceLevelSpec,
  TelemetrySummary,
  TransformSpec,
  kpiFormulaLegible,
  profileStatusChipClass,
  profileStatusLabel,
} from '../../core/profile-models';

const AGENT_ICONS: Record<string, string> = {
  SchemaScout: 'travel_explore',
  MappingArchitect: 'account_tree',
  KpiDesigner: 'analytics',
  ReportDesigner: 'dashboard_customize',
};

export const VISUAL_KIND_OPTIONS: { kind: PowerBIVisualKind; label: string; icon: string }[] = [
  { kind: 'card_kpi', label: 'Tarjetas KPI', icon: 'crop_16_9' },
  { kind: 'barras_categoria', label: 'Barras', icon: 'bar_chart' },
  { kind: 'donut', label: 'Donut', icon: 'donut_large' },
  { kind: 'tendencia', label: 'Lineas', icon: 'show_chart' },
  { kind: 'area', label: 'Area', icon: 'area_chart' },
  { kind: 'columnas_apiladas', label: 'Columnas apiladas', icon: 'stacked_bar_chart' },
  { kind: 'funnel', label: 'Funnel', icon: 'filter_alt' },
  { kind: 'matriz', label: 'Matriz', icon: 'pivot_table_chart' },
  { kind: 'tabla_detalle', label: 'Tabla detalle', icon: 'table_chart' },
];

export const THEME_OPTIONS: { value: PowerBIThemeVariant; label: string }[] = [
  { value: 'nutriavicola', label: 'Corporativo (navy + fondo suave)' },
  { value: 'nutriavicola_claro', label: 'Claro (fondo blanco)' },
  { value: 'nutriavicola_oscuro', label: 'Oscuro (fondo navy)' },
];

@Component({
  selector: 'app-profile-detail',
  standalone: true,
  imports: [
    CommonModule, RouterLink, MatButtonModule, MatExpansionModule,
    MatIconModule, MatProgressSpinnerModule, MatTooltipModule,
  ],
  templateUrl: './profile-detail.component.html',
  styleUrl: './profile-detail.component.scss',
})
export class ProfileDetailComponent {
  private readonly svc = inject(ProfilesService);
  private readonly route = inject(ActivatedRoute);
  private readonly snack = inject(MatSnackBar);

  readonly profileId = this.route.snapshot.paramMap.get('id') ?? '';

  loading = signal(true);
  chat = signal<ChatMessage[]>([]);
  proposal = signal<ProposalResponse | null>(null);
  profileStatus = signal<ProfileStatus | null>(null);
  runs = signal<ProfileRun[]>([]);
  runResult = signal<RunResponse | null>(null);
  generateResult = signal<GenerateResponse | null>(null);
  downloads = signal<DownloadItem[]>([]);
  telemetry = signal<TelemetrySummary | null>(null);
  showTelemetry = signal(false);
  acting = signal(false);
  refining = signal(false);
  running = signal(false);
  generating = signal(false);
  uploadingHomologacion = signal(false);

  // Seccion "Diseno del reporte": el usuario elige o escribe su respuesta.
  readonly visualKindOptions = VISUAL_KIND_OPTIONS;
  readonly themeOptions = THEME_OPTIONS;
  designTheme = signal<PowerBIThemeVariant>('nutriavicola');
  designPaginas = signal(4);
  designCharts = signal(3);
  designTipos = signal<PowerBIVisualKind[]>([]);
  sendingDesign = signal(false);

  bloqueantes = computed(() => this.proposal()?.status?.preguntas_bloqueantes ?? 0);
  aprobado = computed(() => this.profileStatus() === 'approved');
  powerbiSpec = computed<PowerBIReportSpec | null>(
    () => this.proposal()?.profile?.report?.powerbi ?? null,
  );

  constructor() {
    this.loadAll();
  }

  loadAll(): void {
    this.loading.set(true);
    this.reloadChat();
    this.reloadProposal();
    this.reloadStatus();
    this.reloadRuns();
    if (this.aprobado()) this.reloadDownloads();
    if (this.showTelemetry()) this.reloadTelemetry();
  }

  reloadChat(): void {
    this.svc.chat(this.profileId).subscribe({
      next: (msgs) => { this.chat.set(msgs); this.loading.set(false); },
      error: (e) => { this.loading.set(false); this.showError(e); },
    });
  }

  reloadProposal(): void {
    this.svc.proposal(this.profileId).subscribe({
      next: (p) => {
        this.proposal.set(p);
        this.initDesignForm(p.profile?.report?.powerbi ?? null);
      },
      // 404 = aun no hay profile persistido (draft fallo a mitad); el chat
      // sigue siendo visible.
      error: () => this.proposal.set(null),
    });
  }

  private initDesignForm(spec: PowerBIReportSpec | null): void {
    if (!spec) return;
    const design = spec.design;
    const paginas = design?.max_paginas ?? Math.min(spec.pages?.length ?? 4, 10);
    const charts = design?.max_charts_por_pagina
      ?? Math.max(1, ...(spec.pages ?? []).map(p =>
        p.visuals.filter(v => this.esChart(v.kind)).length));
    const tipos = design?.tipos_preferidos?.length
      ? design.tipos_preferidos
      : [...new Set((spec.pages ?? []).flatMap(p => p.visuals.map(v => v.kind)))];
    this.designTheme.set(design?.theme ?? 'nutriavicola');
    this.designPaginas.set(Math.min(Math.max(paginas, 1), 10));
    this.designCharts.set(Math.min(Math.max(charts, 1), 8));
    this.designTipos.set(tipos);
  }

  reloadStatus(): void {
    this.svc.list().subscribe({
      next: (all) => {
        const mine = all.filter(p => p.profile_id === this.profileId);
        if (mine.length === 0) { this.profileStatus.set(null); return; }
        const latest = mine.reduce((a, b) => (b.version > a.version ? b : a));
        this.profileStatus.set(latest.status);
      },
      error: () => this.profileStatus.set(null),
    });
  }

  reloadRuns(): void {
    this.svc.runs(this.profileId).subscribe({
      next: (rs) => this.runs.set(rs),
      error: () => this.runs.set([]),
    });
  }

  reloadTelemetry(): void {
    this.svc.telemetry(this.profileId).subscribe({
      next: (t) => this.telemetry.set(t),
      error: (e) => this.showError(e),
    });
  }

  // ---------- Chat ----------

  answerQuestion(questionId: number, texto: string): void {
    const mensaje = texto.trim();
    if (!mensaje || this.acting()) return;
    this.acting.set(true);
    this.svc.sendChat(this.profileId, mensaje, questionId).subscribe({
      next: () => {
        this.acting.set(false);
        this.reloadChat();
        this.reloadProposal();
      },
      error: (e) => { this.acting.set(false); this.showError(e); },
    });
  }

  assumeQuestion(questionId: number): void {
    if (this.acting()) return;
    this.acting.set(true);
    this.svc.assumeQuestion(this.profileId, questionId).subscribe({
      next: () => {
        this.acting.set(false);
        this.reloadChat();
        this.reloadProposal();
      },
      error: (e) => { this.acting.set(false); this.showError(e); },
    });
  }

  sendFreeMessage(input: HTMLTextAreaElement): void {
    const mensaje = input.value.trim();
    if (!mensaje || this.acting()) return;
    this.acting.set(true);
    this.svc.sendChat(this.profileId, mensaje).subscribe({
      next: () => {
        this.acting.set(false);
        input.value = '';
        this.reloadChat();
      },
      error: (e) => { this.acting.set(false); this.showError(e); },
    });
  }

  // ---------- Acciones del profile ----------

  refine(): void {
    if (this.refining()) return;
    this.refining.set(true);
    this.svc.refine(this.profileId).subscribe({
      next: (r) => {
        this.refining.set(false);
        this.snack.open(
          `Nueva propuesta v${r.version} lista (${r.preguntas_nuevas} pregunta(s) nueva(s))`,
          'OK', { duration: 5000 },
        );
        this.loadAll();
      },
      error: (e) => { this.refining.set(false); this.showError(e); },
    });
  }

  approve(): void {
    if (this.acting() || this.bloqueantes() > 0) return;
    this.acting.set(true);
    this.svc.approve(this.profileId).subscribe({
      next: (r) => {
        this.acting.set(false);
        this.profileStatus.set('approved');
        this.snack.open(`Profile v${r.version} aprobado`, 'OK', { duration: 4000 });
      },
      error: (e) => { this.acting.set(false); this.showError(e); },
    });
  }

  runCruce(): void {
    if (this.running() || !this.aprobado()) return;
    this.running.set(true);
    this.svc.run(this.profileId).subscribe({
      next: (r) => {
        this.running.set(false);
        this.runResult.set(r);
        this.reloadRuns();
        this.snack.open('Cruce ejecutado', 'OK', { duration: 4000 });
      },
      error: (e) => { this.running.set(false); this.showError(e); },
    });
  }

  generateEntregables(): void {
    if (this.generating() || !this.aprobado()) return;
    this.generating.set(true);
    this.svc.generate(this.profileId).subscribe({
      next: (r) => {
        this.generating.set(false);
        this.generateResult.set(r);
        this.runResult.set(r);
        this.reloadRuns();
        this.reloadDownloads();
        this.snack.open('Excel y Power BI generados', 'OK', { duration: 5000 });
      },
      error: (e) => { this.generating.set(false); this.showError(e); },
    });
  }

  onHomologacionSelected(evt: Event): void {
    const input = evt.target as HTMLInputElement;
    const file = (input.files ?? [])[0] ?? null;
    input.value = '';
    if (!file || this.uploadingHomologacion()) return;
    this.uploadingHomologacion.set(true);
    this.svc.uploadHomologacion(this.profileId, file).subscribe({
      next: () => {
        this.uploadingHomologacion.set(false);
        this.reloadChat();
        this.snack.open(
          'Homologacion adjuntada. Los entregables usaran ese catalogo.',
          'OK',
          { duration: 5000 },
        );
      },
      error: (e) => {
        this.uploadingHomologacion.set(false);
        this.showError(e);
      },
    });
  }

  // ---------- Diseno del reporte ----------

  esChart(kind: PowerBIVisualKind): boolean {
    return kind !== 'card_kpi' && kind !== 'tabla_detalle' && kind !== 'matriz';
  }

  isTipoSelected(kind: PowerBIVisualKind): boolean {
    return this.designTipos().includes(kind);
  }

  toggleTipo(kind: PowerBIVisualKind): void {
    this.designTipos.update(tipos =>
      tipos.includes(kind) ? tipos.filter(t => t !== kind) : [...tipos, kind],
    );
  }

  stepPaginas(delta: number): void {
    this.designPaginas.update(v => Math.min(Math.max(v + delta, 1), 10));
  }

  stepCharts(delta: number): void {
    this.designCharts.update(v => Math.min(Math.max(v + delta, 1), 8));
  }

  visualKindLabel(kind: PowerBIVisualKind): string {
    return VISUAL_KIND_OPTIONS.find(o => o.kind === kind)?.label ?? kind;
  }

  themeLabel(value: PowerBIThemeVariant): string {
    return THEME_OPTIONS.find(o => o.value === value)?.label ?? value;
  }

  pageVisualsResumen(page: PowerBIPageSpec): string {
    const counts = new Map<string, number>();
    for (const v of page.visuals) {
      const label = this.visualKindLabel(v.kind);
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([label, n]) => (n > 1 ? `${n} ${label.toLowerCase()}` : label.toLowerCase()))
      .join(', ');
  }

  submitDesign(notas: HTMLTextAreaElement): void {
    if (this.sendingDesign()) return;
    const tipos = this.designTipos();
    const tiposTxt = tipos.length
      ? tipos.map(t => this.visualKindLabel(t)).join(', ')
      : 'los que el ReportDesigner considere mejores';
    const libre = notas.value.trim();
    const mensaje = [
      'PREFERENCIAS DE DISENO DEL TABLERO (elegidas en la UI):',
      `- Hojas/paginas: maximo ${this.designPaginas()}`,
      `- Graficos por pagina: maximo ${this.designCharts()}`,
      `- Tipos de grafico preferidos: ${tiposTxt}`,
      `- Tema de color: ${this.themeLabel(this.designTheme())} (${this.designTheme()})`,
      libre ? `- Indicaciones adicionales: ${libre}` : '',
      'Aplicar estas preferencias en report.powerbi.design al re-proponer.',
    ].filter(Boolean).join('\n');
    this.sendingDesign.set(true);
    this.svc.sendChat(this.profileId, mensaje).subscribe({
      next: () => {
        this.sendingDesign.set(false);
        notas.value = '';
        this.reloadChat();
        this.snack.open(
          'Preferencias de diseno enviadas. Usa "Re-proponer" para que los agentes las apliquen.',
          'OK', { duration: 6000 },
        );
      },
      error: (e) => { this.sendingDesign.set(false); this.showError(e); },
    });
  }

  reloadDownloads(): void {
    this.svc.downloads(this.profileId).subscribe({
      next: (items) => this.downloads.set(items),
    });
  }

  downloadHref(filename: string): string {
    return this.svc.downloadUrl(this.profileId, filename);
  }

  toggleTelemetry(): void {
    this.showTelemetry.update(v => !v);
    if (this.showTelemetry() && !this.telemetry()) this.reloadTelemetry();
  }

  // ---------- Helpers de presentacion ----------

  agentIcon(autor: string): string {
    return AGENT_ICONS[autor] ?? 'smart_toy';
  }

  statusLabel(s: ProfileStatus): string { return profileStatusLabel(s); }
  statusClass(s: ProfileStatus): string { return profileStatusChipClass(s); }
  kpiFormula(k: KpiSpec): string { return kpiFormulaLegible(k); }

  loaderResumen(loader: LoaderSpec): string {
    if (loader.type === 'registered') {
      return `Loader registrado: ${loader.name}`;
    }
    const hoja = loader.sheet == null
      ? 'hoja auto-detectada'
      : `hoja ${loader.sheet}`;
    const header = loader.header_row == null
      ? 'sin encabezados (columnas posicionales)'
      : `header en fila ${loader.header_row}`;
    const cols = loader.columns?.length ?? 0;
    return `Tabular, ${hoja}, ${header}, ${cols} columna(s)`;
  }

  transformResumen(t: TransformSpec): string {
    switch (t.op) {
      case 'filter_equals':
        return `Filtrar: ${t.column} = ${t.value}`;
      case 'filter_not_equals':
        return `Filtrar: ${t.column} != ${t.value}`;
      case 'filter_regex_match':
        return `Regex en ${t.column}: ${t.pattern} (${t.keep ?? 'matched'})`;
      case 'group_by_aggregate':
        return `Agrupar por ${(t.by ?? []).join(', ')} (${(t.aggregations ?? []).length} agregacion(es))`;
      case 'select_rename':
        return `Renombrar ${Object.keys(t.mapping ?? {}).length} columna(s)`;
      case 'unpivot':
        return `Unpivot sobre ${(t.id_vars ?? []).join(', ')}`;
    }
  }

  serviceLevelResumen(sl: ServiceLevelSpec): string {
    const pedido = sl.pedido_key ? `, pedido: ${sl.pedido_key}` : '';
    return `Plan: ${sl.plan_column} vs real: ${sl.real_column}${pedido}, tolerancia ${sl.tolerancia_pct ?? 0}%`;
  }

  breakdownResumen(bd: BreakdownSpec): string {
    const dims = bd.dimensions.join(', ') || 'sin dimensiones';
    const universe = bd.universe ?? 'matched';
    return `${bd.label} (${bd.id}) - dims: ${dims}, universo: ${universe}`;
  }

  breakdownMetricResumen(bd: BreakdownSpec): string[] {
    return (bd.metrics ?? []).map((m) => {
      if (m.op === 'sum') return `${m.id}: suma(${m.column})`;
      if (m.op === 'count') return `${m.id}: conteo`;
      return `${m.id}: suma(${m.numerator}) / suma(${m.denominator}) x 100`;
    });
  }

  dataModelResumen(dm: DataModelSpec): string {
    return `Fact: ${dm.fact_name}, include_unmatched: ${dm.include_unmatched ?? true}`;
  }

  dimResumen(dim: DimensionSpec): string {
    const attrs = (dim.attributes ?? []).join(', ');
    return attrs ? `${dim.name}: key=${dim.key}, attrs=${attrs}` : `${dim.name}: key=${dim.key}`;
  }

  kpiList(kpis: Record<string, unknown>): { id: string; value: string }[] {
    return Object.entries(kpis)
      .filter(([, v]) => this.isPrimitiveKpi(v))
      .map(([id, v]) => ({ id, value: this.fmtKpi(v as number | string | null) }));
  }

  private isPrimitiveKpi(v: unknown): boolean {
    return v == null || typeof v === 'number' || typeof v === 'string';
  }

  fmtKpi(v: number | string | null): string {
    if (v == null) return '—';
    const n = typeof v === 'number' ? v : Number(v);
    if (Number.isNaN(n)) return String(v);
    return n.toLocaleString('es-CO', { maximumFractionDigits: 2 });
  }

  confianzaPct(): string {
    const c = this.proposal()?.status?.confianza_global ?? 0;
    return `${Math.round(c * 100)}%`;
  }

  profileJson(): string {
    const p = this.proposal()?.profile;
    return p ? JSON.stringify(p, null, 2) : '';
  }

  private showError(err: any): void {
    let msg = err?.error?.detail ?? err?.message ?? String(err);
    if (err?.status === 0) {
      msg = (
        `No se pudo conectar con el backend (${this.svc.apiBaseUrl()}). ` +
        'Verifica que FastAPI este corriendo y vuelve a intentar.'
      );
    }
    this.snack.open(
      `Error: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`,
      'Cerrar', { duration: 8000 },
    );
  }
}
