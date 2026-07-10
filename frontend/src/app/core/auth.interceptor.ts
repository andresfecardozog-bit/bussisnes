import {
  HttpErrorResponse,
  HttpHandlerFn,
  HttpInterceptorFn,
  HttpRequest,
} from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';

function readCookie(name: string): string {
  if (typeof document === 'undefined') return '';
  const prefix = `${encodeURIComponent(name)}=`;
  const items = document.cookie ? document.cookie.split('; ') : [];
  for (const item of items) {
    if (item.startsWith(prefix)) {
      return decodeURIComponent(item.slice(prefix.length));
    }
  }
  return '';
}

function withSecurityHeaders(
  req: HttpRequest<unknown>,
  csrfInMemory: string | null,
): HttpRequest<unknown> {
  let next = req.clone({ withCredentials: true });
  const method = req.method.toUpperCase();
  const isMutating = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
  if (isMutating) {
    // Preferir el token en memoria (funciona cross-site); la cookie solo es
    // legible por JS en desarrollo same-site (localhost).
    const csrf = csrfInMemory || readCookie('nutri_csrf');
    if (csrf) {
      next = next.clone({
        setHeaders: {
          'X-CSRF-Token': csrf,
        },
      });
    }
  }
  return next;
}

export const authInterceptor: HttpInterceptorFn = (
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
) => {
  const router = inject(Router);
  const auth = inject(AuthService);
  const isAuthEndpoint = req.url.includes('/auth/login') || req.url.includes('/auth/me');

  return next(withSecurityHeaders(req, auth.csrfToken())).pipe(
    catchError((error: unknown) => {
      if (error instanceof HttpErrorResponse && error.status === 401) {
        auth.clearLocalSession();
        const alreadyOnLogin = router.url.startsWith('/login');
        if (!alreadyOnLogin && !isAuthEndpoint) {
          void router.navigate(['/login']);
        }
      }
      return throwError(() => error);
    }),
  );
};
