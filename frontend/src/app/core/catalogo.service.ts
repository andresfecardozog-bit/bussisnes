import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, switchMap } from 'rxjs';
import { environment } from '../../environments/environment';
import { runtimeApiBaseUrl } from './api-base';
import { pollJob } from './poll-job';

export interface CatalogoItem {
  skill_id: string;
  nombre: string;
  descripcion: string;
  left_label: string;
  right_label: string;
}

export interface CatalogoResultado {
  etiqueta: string;
  summary: { matched: number; solo_left: number; solo_right: number; no_cruzados: number };
  kpis: Record<string, unknown>;
  archivos: string[];
}

export interface EjecutarResponse {
  ok: boolean;
  skill_id: string;
  modo: string;
  run_token: string;
  resultados: CatalogoResultado[];
}

export interface DescargaItem {
  path: string;
  size_bytes: number;
  kind: string;
}

@Injectable({ providedIn: 'root' })
export class CatalogoService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = runtimeApiBaseUrl(environment.apiBaseUrl);
  private readonly base = `${this.apiBase}/catalogo`;

  list(): Observable<{ items: CatalogoItem[] }> {
    return this.http.get<{ items: CatalogoItem[] }>(this.base);
  }

  ejecutar(
    skillId: string, lefts: File[], rights: File[], modo: 'consolidado' | 'individual',
    homologacion?: File | null,
  ): Observable<EjecutarResponse> {
    const fd = new FormData();
    for (const f of lefts) fd.append('left_files', f, f.name);
    for (const f of rights) fd.append('right_files', f, f.name);
    if (homologacion) fd.append('homologacion_file', homologacion, homologacion.name);
    fd.append('modo', modo);
    return this.http
      .post<{ job_id: string }>(`${this.base}/${skillId}/ejecutar`, fd)
      .pipe(switchMap((r) => pollJob<EjecutarResponse>(this.http, this.apiBase, r.job_id)));
  }

  descargas(runToken: string): Observable<{ run_token: string; archivos: DescargaItem[] }> {
    return this.http.get<{ run_token: string; archivos: DescargaItem[] }>(
      `${this.base}/descargas/${runToken}`,
    );
  }

  downloadUrl(runToken: string, path: string): string {
    const parts = path.split('/').map(encodeURIComponent).join('/');
    return `${this.base}/descargas/${runToken}/${parts}`;
  }
}
