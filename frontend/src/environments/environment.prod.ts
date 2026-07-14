// Config runtime en produccion (build de Vercel).
// El archivo `environment.ts` (dev) se reemplaza por este en el build
// via `angular.json > fileReplacements`.
//
// IMPORTANTE: `apiBaseUrl` DEBE incluir el esquema `https://`. Sin el,
// Angular HttpClient trata la URL como path relativo y todas las llamadas
// terminan resolviendose contra el propio dominio Vercel, con lo que
// devuelven el index.html en vez de JSON y el frontend explota en runtime.
// NOTA: backend desplegado en Railway (cuenta nueva: andresfecardozog-bit).
// Si algun dia vuelve a cambiar el host, se puede sobreescribir en runtime con:
//     localStorage.setItem('apiBaseUrl', '<url>'); location.reload();
export const environment = {
  production: true,
  apiBaseUrl: 'https://bussisnes-production.up.railway.app',
  n8nWebhookMatch: '',
};
