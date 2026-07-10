import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  BatchDetailResponse,
  BatchSummary,
  DownloadsResponse,
  FileUploadResponse,
  GenerateResponse,
  PreviewResponse,
  ZipUploadResponse,
} from './models';

import { runtimeApiBaseUrl } from './api-base';

@Injectable({ providedIn: 'root' })
export class BatchesService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = runtimeApiBaseUrl(environment.apiBaseUrl);
  private readonly base = `${this.apiBase}/batches`;

  list(includeArchived = false): Observable<BatchSummary[]> {
    let params = new HttpParams().set('include_archived', String(includeArchived));
    return this.http.get<BatchSummary[]>(this.base, { params });
  }

  create(nombre?: string, notas?: string): Observable<BatchSummary> {
    return this.http.post<BatchSummary>(this.base, { nombre, notas });
  }

  get(id: string): Observable<BatchDetailResponse> {
    return this.http.get<BatchDetailResponse>(`${this.base}/${id}`);
  }

  patch(id: string, nombre?: string, notas?: string): Observable<BatchSummary> {
    return this.http.patch<BatchSummary>(`${this.base}/${id}`, { nombre, notas });
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }

  archive(id: string): Observable<BatchSummary> {
    return this.http.post<BatchSummary>(`${this.base}/${id}/archive`, {});
  }

  uploadPreCortes(id: string, files: File[]): Observable<BatchDetailResponse> {
    const fd = new FormData();
    files.forEach(f => fd.append('files', f, f.name));
    return this.http.post<BatchDetailResponse>(`${this.base}/${id}/pre-cortes`, fd);
  }

  uploadPreCortesFromZip(id: string, zip: File): Observable<ZipUploadResponse> {
    const fd = new FormData();
    fd.append('file', zip, zip.name);
    return this.http.post<ZipUploadResponse>(
      `${this.base}/${id}/pre-cortes/from-zip`, fd,
    );
  }

  removePreCorte(id: string, cargaId: number): Observable<BatchDetailResponse> {
    return this.http.delete<BatchDetailResponse>(
      `${this.base}/${id}/pre-cortes/${cargaId}`,
    );
  }

  uploadFlash(id: string, file: File, year: number, month: number): Observable<BatchDetailResponse> {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const params = new HttpParams()
      .set('year', String(year))
      .set('month', String(month));
    return this.http.post<BatchDetailResponse>(
      `${this.base}/${id}/flash`, fd, { params },
    );
  }

  detachFlash(id: string): Observable<BatchDetailResponse> {
    return this.http.delete<BatchDetailResponse>(`${this.base}/${id}/flash`);
  }

  preview(id: string): Observable<PreviewResponse> {
    return this.http.get<PreviewResponse>(`${this.base}/${id}/preview`);
  }

  confirm(id: string): Observable<BatchSummary> {
    return this.http.post<BatchSummary>(`${this.base}/${id}/confirm`, {});
  }

  generate(id: string): Observable<GenerateResponse> {
    return this.http.post<GenerateResponse>(`${this.base}/${id}/generate`, {});
  }

  downloads(id: string): Observable<DownloadsResponse> {
    return this.http.get<DownloadsResponse>(`${this.base}/${id}/downloads`);
  }

  downloadUrl(id: string, filename: string): string {
    return `${this.base}/${id}/downloads/${encodeURIComponent(filename)}`;
  }
}
