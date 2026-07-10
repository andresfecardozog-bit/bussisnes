// Config runtime en produccion (build de Vercel).
// El archivo `environment.ts` (dev) se reemplaza por este en el build
// via `angular.json > fileReplacements`.
//
// IMPORTANTE: `apiBaseUrl` DEBE incluir el esquema `https://`. Sin el,
// Angular HttpClient trata la URL como path relativo y todas las llamadas
// terminan resolviendose contra el propio dominio Vercel, con lo que
// devuelven el index.html en vez de JSON y el frontend explota en runtime.
// NOTA: Railway se pauso por creditos. Mientras tanto el backend corre local
// expuesto por un tunel Cloudflare (URL temporal). Se puede sobreescribir en
// runtime sin reconstruir con:  localStorage.setItem('apiBaseUrl', '<url>')
export const environment = {
  production: true,
  apiBaseUrl: 'https://athletic-exceed-commercial-exceed.trycloudflare.com',
  n8nWebhookMatch: '',
};
