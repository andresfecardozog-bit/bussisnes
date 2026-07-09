import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { BatchesService } from '../../core/batches.service';
import { BatchSummary } from '../../core/models';
import { CountUpDirective } from '../../shared/directives/count-up.directive';
import { RevealOnScrollDirective } from '../../shared/directives/reveal-on-scroll.directive';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    MatProgressSpinnerModule,
    CountUpDirective,
    RevealOnScrollDirective,
  ],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent {
  private readonly svc = inject(BatchesService);
  // Salario COP $3.5M / 160h = ~COP $21,875/h
  private readonly hourlyRate = 3_500_000 / 160;
  private readonly hoursSavedPerBatch = 4.5;

  loading = signal(true);
  error = signal<string | null>(null);
  batches = signal<BatchSummary[]>([]);

  readonly currencyFormat = new Intl.NumberFormat('es-CO', {
    style: 'currency',
    currency: 'COP',
    maximumFractionDigits: 0,
    notation: 'compact',
    compactDisplay: 'short',
  });

  readonly totalBatches = computed(() => this.batches().length);
  readonly matchedBatches = computed(() => this.countByStatus('matched'));
  readonly readyBatches = computed(() => this.countByStatus('ready_to_match'));

  readonly automationPct = computed(() => {
    const total = this.totalBatches();
    if (total === 0) return 0;
    return Math.round((this.matchedBatches() / total) * 100);
  });

  readonly estimatedHoursSaved = computed(() =>
    Math.round(this.matchedBatches() * this.hoursSavedPerBatch),
  );

  readonly estimatedMoneySaved = computed(() =>
    Math.round(this.estimatedHoursSaved() * this.hourlyRate),
  );

  readonly throughputPath = computed(() => {
    const values = this.lastRunsSeries();
    if (values.length === 0) return '';
    const width = 340;
    const height = 130;
    const padding = 10;
    const max = Math.max(...values, 1);
    const step = values.length > 1 ? (width - padding * 2) / (values.length - 1) : 0;
    return values
      .map((value, index) => {
        const x = padding + index * step;
        const y = height - padding - ((height - padding * 2) * value) / max;
        return `${index === 0 ? 'M' : 'L'} ${x} ${y}`;
      })
      .join(' ');
  });

  readonly throughputAreaPath = computed(() => {
    const line = this.throughputPath();
    if (!line) return '';
    return `${line} L 330 130 L 10 130 Z`;
  });

  constructor() { this.reload(); }

  reload(): void {
    this.loading.set(true);
    this.error.set(null);
    this.svc.list(false).subscribe({
      next: (bs) => { this.batches.set(bs); this.loading.set(false); },
      error: (e) => { this.error.set(String(e.message ?? e)); this.loading.set(false); },
    });
  }

  countByStatus(status: string): number {
    return this.batches().filter(b => b.status === status).length;
  }

  formatCop(value: number): string {
    return this.currencyFormat.format(value);
  }

  private lastRunsSeries(): number[] {
    const runs = [...this.batches()]
      .sort((a, b) => (a.updated_at ?? '').localeCompare(b.updated_at ?? ''))
      .slice(-8);
    if (runs.length === 0) return [];
    let cumulativeMatched = 0;
    return runs.map(run => {
      if (run.status === 'matched') cumulativeMatched += 1;
      return cumulativeMatched;
    });
  }
}
