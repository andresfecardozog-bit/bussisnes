import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  ApproveResponse,
  ChatMessage,
  ChatPostResponse,
  DraftResponse,
  GenerateResponse,
  DownloadItem,
  MatchProfile,
  ProfileRun,
  ProfileSummary,
  ProposalResponse,
  RefineResponse,
  RunResponse,
  TelemetrySummary,
  UpdateProfileResponse,
} from './profile-models';

import { runtimeApiBaseUrl } from './api-base';

@Injectable({ providedIn: 'root' })
export class ProfilesService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = runtimeApiBaseUrl(environment.apiBaseUrl);
  private readonly base = `${this.apiBase}/profiles`;

  apiBaseUrl(): string {
    return this.apiBase;
  }

  list(): Observable<ProfileSummary[]> {
    return this.http.get<ProfileSummary[]>(this.base);
  }

  createDraft(
    profileId: string,
    brief: string,
    leftFile: File,
    rightFile: File,
    homologacionFile?: File | null,
  ): Observable<DraftResponse> {
    const fd = new FormData();
    fd.append('profile_id', profileId);
    fd.append('brief', brief);
    fd.append('left_file', leftFile, leftFile.name);
    fd.append('right_file', rightFile, rightFile.name);
    if (homologacionFile) {
      fd.append('homologacion_file', homologacionFile, homologacionFile.name);
    }
    return this.http.post<DraftResponse>(`${this.base}/draft`, fd);
  }

  uploadHomologacion(id: string, file: File): Observable<ChatPostResponse> {
    const fd = new FormData();
    fd.append('homologacion_file', file, file.name);
    return this.http.post<ChatPostResponse>(`${this.base}/${id}/homologacion`, fd);
  }

  proposal(id: string, version?: number): Observable<ProposalResponse> {
    let params = new HttpParams();
    if (version != null) params = params.set('version', String(version));
    return this.http.get<ProposalResponse>(`${this.base}/${id}/proposal`, { params });
  }

  chat(id: string): Observable<ChatMessage[]> {
    return this.http.get<ChatMessage[]>(`${this.base}/${id}/chat`);
  }

  sendChat(id: string, mensaje: string, questionId?: number): Observable<ChatPostResponse> {
    return this.http.post<ChatPostResponse>(`${this.base}/${id}/chat`, {
      mensaje,
      question_id: questionId ?? null,
    });
  }

  assumeQuestion(id: string, questionId: number): Observable<ChatPostResponse> {
    return this.http.post<ChatPostResponse>(
      `${this.base}/${id}/questions/${questionId}/assume`, {},
    );
  }

  refine(id: string): Observable<RefineResponse> {
    return this.http.post<RefineResponse>(`${this.base}/${id}/refine`, {});
  }

  approve(id: string, aprobadoPor = 'usuario', version?: number): Observable<ApproveResponse> {
    return this.http.post<ApproveResponse>(`${this.base}/${id}/approve`, {
      aprobado_por: aprobadoPor,
      version: version ?? null,
    });
  }

  update(id: string, profile: MatchProfile): Observable<UpdateProfileResponse> {
    return this.http.put<UpdateProfileResponse>(`${this.base}/${id}`, profile);
  }

  run(
    id: string, version?: number, parameters?: Record<string, unknown>,
  ): Observable<RunResponse> {
    return this.http.post<RunResponse>(`${this.base}/${id}/run`, {
      version: version ?? null,
      parameters: parameters ?? {},
    });
  }

  generate(
    id: string, version?: number, parameters?: Record<string, unknown>,
  ): Observable<GenerateResponse> {
    return this.http.post<GenerateResponse>(`${this.base}/${id}/generate`, {
      version: version ?? null,
      parameters: parameters ?? {},
    });
  }

  reejecutar(
    id: string,
    leftFile: File,
    rightFile: File,
    homologacionFile?: File | null,
    version?: number,
  ): Observable<GenerateResponse> {
    const fd = new FormData();
    fd.append('left_file', leftFile, leftFile.name);
    fd.append('right_file', rightFile, rightFile.name);
    if (homologacionFile) {
      fd.append('homologacion_file', homologacionFile, homologacionFile.name);
    }
    if (version != null) fd.append('version', String(version));
    return this.http.post<GenerateResponse>(`${this.base}/${id}/reejecutar`, fd);
  }

  downloads(id: string): Observable<DownloadItem[]> {
    return this.http.get<DownloadItem[]>(`${this.base}/${id}/downloads`);
  }

  downloadUrl(id: string, filename: string): string {
    return `${this.base}/${id}/downloads/${encodeURIComponent(filename)}`;
  }

  runs(id: string): Observable<ProfileRun[]> {
    return this.http.get<ProfileRun[]>(`${this.base}/${id}/runs`);
  }

  telemetry(id: string): Observable<TelemetrySummary> {
    return this.http.get<TelemetrySummary>(`${this.base}/${id}/telemetry`);
  }
}
