import { HttpClient } from '@angular/common/http';
import { Observable, last, of, switchMap, takeWhile, throwError, timer } from 'rxjs';

export interface JobStatus<T> {
  job_id: string;
  kind: string;
  status: 'running' | 'done' | 'error';
  result: T | null;
  error: { status: number; detail: string } | null;
}

/**
 * Hace polling a GET /jobs/{id} hasta que el trabajo termina y emite su
 * resultado (o lanza un error con la misma forma que un HttpErrorResponse:
 * `{ error: { detail }, status }`). Permite que las operaciones pesadas
 * (agentes, consolidados) sobrevivan al limite de tiempo de un tunel/proxy:
 * cada peticion de estado es corta.
 */
export function pollJob<T>(
  http: HttpClient,
  apiBase: string,
  jobId: string,
  intervalMs = 2500,
  timeoutMs = 900000,
): Observable<T> {
  const deadline = Date.now() + timeoutMs;
  return timer(0, intervalMs).pipe(
    switchMap(() => http.get<JobStatus<T>>(`${apiBase}/jobs/${jobId}`)),
    takeWhile((j) => j.status === 'running' && Date.now() < deadline, true),
    last(),
    switchMap((j) => {
      if (j.status === 'done') return of(j.result as T);
      if (j.status === 'error') {
        return throwError(() => ({
          error: { detail: j.error?.detail ?? 'Error en el proceso' },
          status: j.error?.status ?? 500,
        }));
      }
      return throwError(() => ({
        error: { detail: 'El proceso tardo demasiado. Intenta de nuevo.' },
        status: 0,
      }));
    }),
  );
}
