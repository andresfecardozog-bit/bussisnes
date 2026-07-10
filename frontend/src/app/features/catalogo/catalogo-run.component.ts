import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import {
  CatalogoItem, CatalogoService, DescargaItem, EjecutarResponse,
} from '../../core/catalogo.service';

@Component({
  selector: 'app-catalogo-run',
  standalone: true,
  imports: [CommonModule, RouterLink, MatButtonModule, MatIconModule, MatProgressSpinnerModule],
  templateUrl: './catalogo-run.component.html',
  styleUrl: './catalogo-run.component.scss',
})
export class CatalogoRunComponent {
  private readonly svc = inject(CatalogoService);
  private readonly route = inject(ActivatedRoute);

  readonly skillId = this.route.snapshot.paramMap.get('skillId') ?? '';
  skill = signal<CatalogoItem | null>(null);

  leftFiles = signal<File[]>([]);
  rightFiles = signal<File[]>([]);
  homologacionFile = signal<File | null>(null);
  modo = signal<'consolidado' | 'individual'>('consolidado');
  readonly esPreCorte = this.skillId === 'pre_corte';
  busy = signal(false);
  error = signal<string | null>(null);
  result = signal<EjecutarResponse | null>(null);
  downloads = signal<DescargaItem[]>([]);

  readonly canSubmit = computed(
    () => this.leftFiles().length > 0 && this.rightFiles().length > 0 && !this.busy(),
  );

  constructor() {
    this.svc.list().subscribe((r) => {
      this.skill.set(r.items.find((i) => i.skill_id === this.skillId) ?? null);
    });
  }

  onFiles(side: 'left' | 'right', evt: Event): void {
    const input = evt.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    if (side === 'left') this.leftFiles.set(files);
    else this.rightFiles.set(files);
  }

  onHomologacion(evt: Event): void {
    const input = evt.target as HTMLInputElement;
    this.homologacionFile.set((input.files ?? [])[0] ?? null);
    input.value = '';
  }

  setModo(m: 'consolidado' | 'individual'): void { this.modo.set(m); }

  submit(): void {
    if (!this.canSubmit()) return;
    this.busy.set(true);
    this.error.set(null);
    this.result.set(null);
    this.downloads.set([]);
    this.svc.ejecutar(
      this.skillId, this.leftFiles(), this.rightFiles(), this.modo(),
      this.homologacionFile(),
    ).subscribe({
      next: (res) => {
        this.result.set(res);
        this.svc.descargas(res.run_token).subscribe({
          next: (d) => { this.downloads.set(d.archivos); this.busy.set(false); },
          error: () => this.busy.set(false),
        });
      },
      error: (e) => {
        this.busy.set(false);
        const detail = e?.error?.detail ?? e?.message ?? String(e);
        this.error.set(typeof detail === 'string' ? detail : JSON.stringify(detail));
      },
    });
  }

  downloadHref(path: string): string {
    const token = this.result()?.run_token ?? '';
    return this.svc.downloadUrl(token, path);
  }

  kpiList(kpis: Record<string, unknown>): { id: string; value: string }[] {
    return Object.entries(kpis)
      .filter(([, v]) => v == null || typeof v !== 'object')
      .map(([id, v]) => ({ id, value: v == null ? '—' : String(v) }));
  }
}
