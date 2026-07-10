import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, catchError, map, of, tap } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthLoginResponse, AuthMeResponse, AuthUser } from './auth-models';

function resolveApiBaseUrl(raw: string): string {
  const fallback = raw.replace(/\/+$/, '');
  try {
    const url = new URL(raw);
    const currentHost = typeof window !== 'undefined' ? window.location.hostname : '';
    const localHosts = new Set(['localhost', '127.0.0.1']);
    if (currentHost && localHosts.has(url.hostname) && localHosts.has(currentHost)) {
      url.hostname = currentHost;
    }
    return url.toString().replace(/\/+$/, '');
  } catch {
    return fallback;
  }
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = resolveApiBaseUrl(environment.apiBaseUrl);
  private readonly userState = signal<AuthUser | null>(null);
  private readonly loadedState = signal(false);
  // Token CSRF en memoria: imprescindible en deploy cross-site (Vercel +
  // Railway), donde el JS no puede leer la cookie CSRF de otro dominio.
  private readonly csrfState = signal<string | null>(null);

  readonly user = computed(() => this.userState());
  readonly loaded = computed(() => this.loadedState());
  readonly isAuthenticated = computed(() => this.userState() !== null);
  readonly mustChangePassword = computed(() => this.userState()?.must_change_password ?? false);
  readonly csrfToken = computed(() => this.csrfState());

  login(email: string, password: string): Observable<AuthLoginResponse> {
    return this.http.post<AuthLoginResponse>(`${this.apiBase}/auth/login`, { email, password }).pipe(
      tap(resp => {
        this.userState.set(resp.user);
        if (resp.csrf_token) this.csrfState.set(resp.csrf_token);
        this.loadedState.set(true);
      }),
    );
  }

  logout(): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(`${this.apiBase}/auth/logout`, {}).pipe(
      tap(() => {
        this.userState.set(null);
        this.loadedState.set(true);
      }),
      catchError(() => {
        this.userState.set(null);
        this.loadedState.set(true);
        return of({ ok: true });
      }),
    );
  }

  me(): Observable<AuthUser | null> {
    return this.http.get<AuthMeResponse>(`${this.apiBase}/auth/me`).pipe(
      tap(resp => {
        if (resp.csrf_token) this.csrfState.set(resp.csrf_token);
      }),
      map(resp => resp.user),
      tap(user => {
        this.userState.set(user);
        this.loadedState.set(true);
      }),
      catchError(() => {
        this.userState.set(null);
        this.loadedState.set(true);
        return of(null);
      }),
    );
  }

  changePassword(currentPassword: string, newPassword: string): Observable<AuthLoginResponse> {
    return this.http
      .post<AuthLoginResponse>(`${this.apiBase}/auth/change-password`, {
        current_password: currentPassword,
        new_password: newPassword,
      })
      .pipe(
        tap(resp => {
          this.userState.set(resp.user);
          if (resp.csrf_token) this.csrfState.set(resp.csrf_token);
          this.loadedState.set(true);
        }),
      );
  }

  clearLocalSession(): void {
    this.userState.set(null);
    this.csrfState.set(null);
    this.loadedState.set(true);
  }
}
