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

function withSecurityHeaders(req: HttpRequest<unknown>): HttpRequest<unknown> {
  let next = req.clone({ withCredentials: true });
  const method = req.method.toUpperCase();
  const isMutating = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);
  if (isMutating) {
    const csrf = readCookie('nutri_csrf');
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

  return next(withSecurityHeaders(req)).pipe(
    catchError((error: unknown) => {
      if (error instanceof HttpErrorResponse && error.status === 401) {
        auth.clearLocalSession();
        void router.navigate(['/login']);
      }
      return throwError(() => error);
    }),
  );
};
