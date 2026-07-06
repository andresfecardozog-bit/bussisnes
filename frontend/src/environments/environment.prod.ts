// Config runtime en produccion (build de Vercel).
// El archivo `environment.ts` (dev) se reemplaza por este en el build
// via `angular.json > fileReplacements`.
//
// IMPORTANTE: `apiBaseUrl` DEBE incluir el esquema `https://`. Sin el,
// Angular HttpClient trata la URL como path relativo y todas las llamadas
// terminan resolviendose contra el propio dominio Vercel, con lo que
// devuelven el index.html en vez de JSON y el frontend explota en runtime.
export const environment = {
  production: true,
  apiBaseUrl: 'https://bussisnes-intelligen-t-production.up.railway.app',
  n8nWebhookMatch: '',
};
