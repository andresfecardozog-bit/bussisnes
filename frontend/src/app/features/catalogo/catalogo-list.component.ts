import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { CatalogoItem, CatalogoService } from '../../core/catalogo.service';

@Component({
  selector: 'app-catalogo-list',
  standalone: true,
  imports: [CommonModule, RouterLink, MatButtonModule, MatIconModule, MatProgressSpinnerModule],
  templateUrl: './catalogo-list.component.html',
  styleUrl: './catalogo-run.component.scss',
})
export class CatalogoListComponent {
  private readonly svc = inject(CatalogoService);
  loading = signal(true);
  error = signal<string | null>(null);
  items = signal<CatalogoItem[]>([]);

  constructor() {
    this.svc.list().subscribe({
      next: (r) => { this.items.set(r.items); this.loading.set(false); },
      error: (e) => {
        this.error.set(String(e?.error?.detail ?? e?.message ?? e));
        this.loading.set(false);
      },
    });
  }
}
