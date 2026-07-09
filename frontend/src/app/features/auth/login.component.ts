import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly email = signal('admin@nutriavicola.local');
  protected readonly password = signal('');
  protected readonly currentPassword = signal('');
  protected readonly newPassword = signal('');
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly info = signal<string | null>(null);
  protected readonly forcePasswordChange = signal(false);
  protected readonly redirectUrl = computed(() => this.route.snapshot.queryParamMap.get('redirect') || '/app/dashboard');

  constructor() {
    this.auth.me().subscribe(user => {
      if (!user) return;
      if (user.must_change_password) {
        this.forcePasswordChange.set(true);
        this.info.set('Debes cambiar la contraseña temporal antes de usar la plataforma.');
        return;
      }
      void this.router.navigateByUrl(this.redirectUrl());
    });
  }

  protected submitLogin(): void {
    this.loading.set(true);
    this.error.set(null);
    this.info.set(null);
    this.auth.login(this.email().trim(), this.password()).subscribe({
      next: resp => {
        this.loading.set(false);
        if (resp.user.must_change_password) {
          this.currentPassword.set(this.password());
          this.newPassword.set('');
          this.forcePasswordChange.set(true);
          this.info.set('Tu usuario requiere cambio de contraseña en el primer ingreso.');
          return;
        }
        void this.router.navigateByUrl(this.redirectUrl());
      },
      error: err => {
        this.loading.set(false);
        this.error.set(err?.error?.detail || 'No fue posible iniciar sesión.');
      },
    });
  }

  protected submitPasswordChange(): void {
    this.loading.set(true);
    this.error.set(null);
    this.info.set(null);
    this.auth.changePassword(this.currentPassword(), this.newPassword()).subscribe({
      next: () => {
        this.loading.set(false);
        this.forcePasswordChange.set(false);
        void this.router.navigateByUrl(this.redirectUrl());
      },
      error: err => {
        this.loading.set(false);
        this.error.set(err?.error?.detail || 'No fue posible cambiar la contraseña.');
      },
    });
  }
}
