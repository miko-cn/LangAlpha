/**
 * Deployment mode: "oss" (self-hosted, no auth), "local" (single-account
 * login), or "platform" (hosted Supabase auth).
 *
 * Resolved once from the VITE_HOST_MODE build-time env var.
 * Import this instead of checking VITE_SUPABASE_URL for mode detection.
 * `isPlatformMode` stays platform-only — do not treat local as platform.
 */
export type HostMode = 'oss' | 'platform' | 'local';

export const HOST_MODE: HostMode = (import.meta.env.VITE_HOST_MODE ?? 'oss') as HostMode;
export const isPlatformMode = HOST_MODE === 'platform';
export const isLocalMode = HOST_MODE === 'local';

/**
 * The app entry URL for the current mode. In platform mode `/` is served
 * externally (marketing landing via nginx) and the SPA owns `/app`; in OSS mode
 * the SPA owns `/` directly. On a dedicated app subdomain set
 * VITE_APP_ENTRY_PATH=/ so the entry mounts at the host root instead.
 * Route unauthenticated redirects through this.
 */
export const APP_ENTRY_PATH: string =
  import.meta.env.VITE_APP_ENTRY_PATH || (isPlatformMode ? '/app' : '/');
