import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, ViewChild, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatStepper, MatStepperModule } from '@angular/material/stepper';
import { MatTooltipModule } from '@angular/material/tooltip';
import { STEPPER_GLOBAL_OPTIONS, StepperSelectionEvent } from '@angular/cdk/stepper';
import { BatchesService } from '../../core/batches.service';
import {
  BatchDetailResponse, BatchSummary, PreviewResponse, ZipUploadResponse,
} from '../../core/models';

@Component({
  selector: 'app-batch-wizard',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule, MatButtonModule, MatCardModule,
    MatFormFieldModule, MatIconModule, MatInputModule, MatProgressBarModule,
    MatProgressSpinnerModule, MatSelectModule, MatStepperModule,
    MatTooltipModule,
  ],
  providers: [{ provide: STEPPER_GLOBAL_OPTIONS, useValue: { displayDefaultIndicatorType: false } }],
  templateUrl: './batch-wizard.component.html',
  styleUrl: './batch-wizard.component.scss',
})
export class BatchWizardComponent {
  private readonly svc = inject(BatchesService);
  private readonly fb = inject(FormBuilder);
  private readonly router = inject(Router);
  private readonly snack = inject(MatSnackBar);
  private readonly cdr = inject(ChangeDetectorRef);

  @ViewChild(MatStepper) stepper!: MatStepper;

  /**
   * Handler del `(selectionChange)` del `<mat-stepper>`. Cuando el usuario
   * navega al paso 4 (Preview, index 3), disparamos `loadPreview()` para
   * que la tabla se renderice sin depender del boton "Continuar al preview"
   * del paso anterior. Sin esto, saltar al paso 4 haciendo click en la
   * label deja la pantalla en blanco.
   */
  onStepChange(event: StepperSelectionEvent): void {
    if (event.selectedIndex === 3 && !this.preview() && !this.busy()) {
      this.loadPreview();
    }
  }

  batch = signal<BatchDetailResponse | null>(null);
  preview = signal<PreviewResponse | null>(null);
  zipReport = signal<ZipUploadResponse | null>(null);
  busy = signal(false);
  generateResult = signal<{
    consolidado: string; zip: string; batch_id: string;
  } | null>(null);

  step1 = this.fb.group({
    nombre: ['', Validators.required],
  });
  step3 = this.fb.group({
    year: [new Date().getFullYear(), Validators.required],
    month: [new Date().getMonth() + 1, Validators.required],
  });

  yearsOpt = Array.from({ length: 7 }, (_, i) => 2024 + i);
  monthsOpt = [
    { v: 1, l: 'Enero' }, { v: 2, l: 'Febrero' }, { v: 3, l: 'Marzo' },
    { v: 4, l: 'Abril' }, { v: 5, l: 'Mayo' }, { v: 6, l: 'Junio' },
    { v: 7, l: 'Julio' }, { v: 8, l: 'Agosto' }, { v: 9, l: 'Septiembre' },
    { v: 10, l: 'Octubre' }, { v: 11, l: 'Noviembre' }, { v: 12, l: 'Diciembre' },
  ];

  // ---------- Step 1: crear batch ----------
  createBatch(): void {
    if (this.step1.invalid) return;
    this.busy.set(true);
    this.svc.create(this.step1.value.nombre ?? undefined).subscribe({
      next: (b) => {
        this.svc.get(b.id).subscribe(d => {
          this.batch.set(d);
          this.busy.set(false);
          this.stepper.next();
        });
      },
      error: (e) => { this.busy.set(false); this.showError(e); },
    });
  }

  // ---------- Step 2: pre_cortes ----------
  onFilesSelected(evt: Event): void {
    const input = evt.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    if (!files.length || !this.batch()) return;
    const zips = files.filter(f => f.name.toLowerCase().endsWith('.zip'));
    const xlsxs = files.filter(f => /\.xlsx?$/i.test(f.name));
    const id = this.batch()!.id;
    this.busy.set(true);

    if (zips.length > 0) {
      this.svc.uploadPreCortesFromZip(id, zips[0]).subscribe({
        next: (res) => {
          this.zipReport.set(res);
          this.svc.get(id).subscribe(d => { this.batch.set(d); this.busy.set(false); });
        },
        error: (e) => { this.busy.set(false); this.showError(e); },
      });
    } else if (xlsxs.length > 0) {
      this.svc.uploadPreCortes(id, xlsxs).subscribe({
        next: (d) => { this.batch.set(d); this.busy.set(false); },
        error: (e) => { this.busy.set(false); this.showError(e); },
      });
    } else {
      this.busy.set(false);
    }
    input.value = '';
  }

  removePreCorte(cargaId: number): void {
    if (!this.batch()) return;
    this.busy.set(true);
    this.svc.removePreCorte(this.batch()!.id, cargaId).subscribe({
      next: (d) => { this.batch.set(d); this.busy.set(false); },
      error: (e) => { this.busy.set(false); this.showError(e); },
    });
  }

  // ---------- Step 3: flash ----------
  onFlashSelected(evt: Event): void {
    const input = evt.target as HTMLInputElement;
    const file = (input.files ?? [])[0];
    if (!file || !this.batch()) return;
    const y = this.step3.value.year!;
    const m = this.step3.value.month!;
    this.busy.set(true);
    this.svc.uploadFlash(this.batch()!.id, file, y, m).subscribe({
      next: (d) => { this.batch.set(d); this.busy.set(false); this.stepper.next(); },
      error: (e) => { this.busy.set(false); this.showError(e); },
    });
    input.value = '';
  }

  // ---------- Step 4: preview ----------
  loadPreview(): void {
    if (!this.batch()) return;
    this.busy.set(true);
    this.preview.set(null); // limpiar preview previo para forzar rerender
    this.svc.preview(this.batch()!.id).subscribe({
      next: (p) => {
        this.preview.set(p);
        this.busy.set(false);
        // Blindaje para zoneless: forzar change detection por si el
        // subscribe callback no la dispara en algun edge case.
        this.cdr.markForCheck();
      },
      error: (e) => {
        this.busy.set(false);
        this.cdr.markForCheck();
        this.showError(e);
      },
    });
  }

  cumplTier(pct: number): 'good' | 'warn' | 'bad' {
    if (pct >= 0.95 && pct <= 1.05) return 'good';
    if (pct >= 0.85 && pct <= 1.15) return 'warn';
    return 'bad';
  }

  // ---------- Step 5: confirmar + generar ----------
  confirmAndGenerate(): void {
    if (!this.batch()) return;
    const id = this.batch()!.id;
    this.busy.set(true);
    this.svc.confirm(id).subscribe({
      next: () => {
        this.svc.generate(id).subscribe({
          next: (res) => {
            this.generateResult.set({
              consolidado: res.consolidado_filename,
              zip: res.zip_filename,
              batch_id: id,
            });
            this.busy.set(false);
            this.snack.open('Reportes generados exitosamente', 'OK', { duration: 4000 });
          },
          error: (e) => { this.busy.set(false); this.showError(e); },
        });
      },
      error: (e) => { this.busy.set(false); this.showError(e); },
    });
  }

  goToDownloads(): void {
    const id = this.generateResult()?.batch_id;
    if (id) this.router.navigate(['/batches', id, 'descargas']);
  }

  private showError(err: any): void {
    const msg = err?.error?.detail ?? err?.message ?? String(err);
    this.snack.open(`Error: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`,
                    'Cerrar', { duration: 8000 });
  }
}
