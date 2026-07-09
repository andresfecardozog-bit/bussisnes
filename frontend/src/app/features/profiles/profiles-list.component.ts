import { CommonModule } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { ProfilesService } from '../../core/profiles.service';
import {
  ProfileStatus,
  ProfileSummary,
  profileStatusChipClass,
  profileStatusLabel,
} from '../../core/profile-models';

@Component({
  selector: 'app-profiles-list',
  standalone: true,
  imports: [
    CommonModule, RouterLink, MatButtonModule, MatIconModule,
    MatProgressSpinnerModule, MatTooltipModule,
  ],
  templateUrl: './profiles-list.component.html',
  styleUrl: './profiles-list.component.scss',
})
export class ProfilesListComponent {
  private readonly svc = inject(ProfilesService);

  loading = signal(true);
  error = signal<string | null>(null);
  profiles = signal<ProfileSummary[]>([]);

  constructor() {
    this.reload();
  }

  reload(): void {
    this.loading.set(true);
    this.error.set(null);
    this.svc.list().subscribe({
      next: (ps) => { this.profiles.set(ps); this.loading.set(false); },
      error: (e) => {
        this.error.set(String(e?.error?.detail ?? e?.message ?? e));
        this.loading.set(false);
      },
    });
  }

  statusLabel(s: ProfileStatus): string { return profileStatusLabel(s); }
  statusClass(s: ProfileStatus): string { return profileStatusChipClass(s); }
}
