import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar } from '@angular/material/snack-bar';
import { ProfilesService } from '../../core/profiles.service';

@Component({
  selector: 'app-profile-new',
  standalone: true,
  imports: [
    CommonModule, ReactiveFormsModule, RouterLink, MatButtonModule,
    MatFormFieldModule, MatIconModule, MatInputModule,
    MatProgressSpinnerModule,
  ],
  templateUrl: './profile-new.component.html',
  styleUrl: './profile-new.component.scss',
})
export class ProfileNewComponent {
  private readonly svc = inject(ProfilesService);
  private readonly fb = inject(FormBuilder);
  private readonly router = inject(Router);
  private readonly snack = inject(MatSnackBar);

  busy = signal(false);
  error = signal<string | null>(null);
  leftFile = signal<File | null>(null);
  rightFile = signal<File | null>(null);
  homologacionFile = signal<File | null>(null);

  form = this.fb.group({
    profile_id: ['', [Validators.required, Validators.pattern(/^[a-z][a-z0-9_]*$/)]],
    brief: ['', [Validators.required, Validators.minLength(20)]],
  });

  onFileSelected(side: 'left' | 'right' | 'homologacion', evt: Event): void {
    const input = evt.target as HTMLInputElement;
    const file = (input.files ?? [])[0] ?? null;
    if (side === 'left') this.leftFile.set(file);
    else if (side === 'right') this.rightFile.set(file);
    else this.homologacionFile.set(file);
    input.value = '';
  }

  get canSubmit(): boolean {
    return this.form.valid && !!this.leftFile() && !!this.rightFile() && !this.busy();
  }

  submit(): void {
    if (!this.canSubmit) return;
    this.busy.set(true);
    this.error.set(null);
    const id = this.form.value.profile_id!;
    this.svc.createDraft(
      id, this.form.value.brief!, this.leftFile()!, this.rightFile()!,
      this.homologacionFile(),
    ).subscribe({
      next: () => {
        this.busy.set(false);
        this.snack.open('Propuesta lista: revisa el chat de entrevista', 'OK', { duration: 5000 });
        this.router.navigate(['/procesos', id]);
      },
      error: (e) => {
        this.busy.set(false);
        const detail = e?.error?.detail ?? e?.message ?? String(e);
        if (e?.status === 503) {
          this.error.set(
            'El servicio de agentes no esta disponible (falta configurar la '
            + 'API key de Gemini en el servidor). Detalle: ' + detail,
          );
        } else {
          this.error.set(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
      },
    });
  }
}
