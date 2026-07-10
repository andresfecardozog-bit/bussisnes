/**
 * Resuelve la URL base del backend.
 *
 * Permite sobreescribir el backend en runtime (sin reconstruir el frontend)
 * via `localStorage['apiBaseUrl']` o `window.__API_BASE__`. Es util cuando el
 * backend vive detras de un tunel temporal (Cloudflare Tunnel) cuya URL cambia
 * en cada arranque: basta con
 *     localStorage.setItem('apiBaseUrl', 'https://xxxx.trycloudflare.com')
 * y recargar. Si no hay override, usa el valor de build (environment).
 *
 * Ademas normaliza localhost vs 127.0.0.1 para que las cookies de sesion no se
 * pierdan por mezclar ambos hosts en desarrollo.
 */
export function runtimeApiBaseUrl(fallback: string): string {
  let raw = fallback;
  try {
    if (typeof window !== 'undefined') {
      const override =
        window.localStorage?.getItem('apiBaseUrl') ||
        (window as unknown as { __API_BASE__?: string }).__API_BASE__ ||
        '';
      if (override) raw = override;
    }
  } catch {
    /* localStorage puede no estar disponible: se ignora */
  }

  const fb = raw.replace(/\/+$/, '');
  try {
    const url = new URL(raw);
    const currentHost =
      typeof window !== 'undefined' ? window.location.hostname : '';
    const localHosts = new Set(['localhost', '127.0.0.1']);
    if (
      currentHost &&
      localHosts.has(url.hostname) &&
      localHosts.has(currentHost) &&
      url.hostname !== currentHost
    ) {
      url.hostname = currentHost;
    }
    return url.toString().replace(/\/+$/, '');
  } catch {
    return fb;
  }
}
