import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () =>
      import('./features/auth/login.component').then(m => m.LoginComponent),
    title: "Business Intelligen't — Login",
  },

  // ─── LANDING PÚBLICA ────────────────────────────────────────────────────────
  {
    path: '',
    loadComponent: () =>
      import('./features/landing/landing.component').then(m => m.LandingComponent),
    title: "Business Intelligen't",
  },

  // ─── APP PRIVADA (con sidebar) ───────────────────────────────────────────────
  {
    path: 'app',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./features/shell/app-shell.component').then(m => m.AppShellComponent),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
      {
        path: 'dashboard',
        loadComponent: () =>
          import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent),
        title: "Business Intelligen't — Dashboard",
      },
      {
        path: 'batches/nuevo',
        loadComponent: () =>
          import('./features/batches/batch-wizard.component').then(m => m.BatchWizardComponent),
        title: "Business Intelligen't — Nuevo batch",
      },
      {
        path: 'batches/:id',
        loadComponent: () =>
          import('./features/batches/batch-detail.component').then(m => m.BatchDetailComponent),
        title: "Business Intelligen't — Detalle del batch",
      },
      {
        path: 'batches/:id/descargas',
        loadComponent: () =>
          import('./features/batches/downloads.component').then(m => m.DownloadsComponent),
        title: "Business Intelligen't — Descargas",
      },
      {
        path: 'catalogo',
        loadComponent: () =>
          import('./features/catalogo/catalogo-list.component').then(m => m.CatalogoListComponent),
        title: "Business Intelligen't — Procesos predefinidos",
      },
      {
        path: 'catalogo/:skillId',
        loadComponent: () =>
          import('./features/catalogo/catalogo-run.component').then(m => m.CatalogoRunComponent),
        title: "Business Intelligen't — Ejecutar proceso",
      },
      {
        path: 'procesos',
        loadComponent: () =>
          import('./features/profiles/profiles-list.component').then(m => m.ProfilesListComponent),
        title: "Business Intelligen't — Procesos",
      },
      {
        path: 'procesos/nuevo',
        loadComponent: () =>
          import('./features/profiles/profile-new.component').then(m => m.ProfileNewComponent),
        title: "Business Intelligen't — Nuevo proceso",
      },
      {
        path: 'procesos/:id/repetir',
        loadComponent: () =>
          import('./features/profiles/profile-rerun.component').then(m => m.ProfileRerunComponent),
        title: "Business Intelligen't — Repetir proceso",
      },
      {
        path: 'procesos/:id',
        loadComponent: () =>
          import('./features/profiles/profile-detail.component').then(m => m.ProfileDetailComponent),
        title: "Business Intelligen't — Detalle del proceso",
      },
    ],
  },

  // ─── REDIRECCIONES LEGACY ────────────────────────────────────────────────────
  { path: 'dashboard', redirectTo: 'app/dashboard', pathMatch: 'full' },
  { path: 'batches/nuevo', redirectTo: 'app/batches/nuevo', pathMatch: 'full' },
  { path: 'batches/:id', redirectTo: 'app/batches/:id', pathMatch: 'full' },
  { path: 'batches/:id/descargas', redirectTo: 'app/batches/:id/descargas', pathMatch: 'full' },
  { path: 'procesos', redirectTo: 'app/procesos', pathMatch: 'full' },
  { path: 'procesos/nuevo', redirectTo: 'app/procesos/nuevo', pathMatch: 'full' },
  { path: 'procesos/:id', redirectTo: 'app/procesos/:id', pathMatch: 'full' },
  { path: '**', redirectTo: '' },
];
