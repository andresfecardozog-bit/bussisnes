import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';

import { routes } from './app.routes';

// Angular 22: modo zoneless por default. `zone.js` no esta en dependencies
// (fue removido del paquete estandar); usar `provideZoneChangeDetection`
// aca produciria NG0908 en runtime porque NgZone factory no puede
// instanciar Zone. Con `provideZonelessChangeDetection` Angular usa signals
// + OnPush automatico y hace change detection sin depender de zone.js.
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(),
    provideAnimationsAsync(),
  ],
};
