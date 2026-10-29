/**
 * Routes that only exist for the cloud dashboard. Direct mode serves a local
 * daemon with sessions and chat only, so these pages redirect to New Session
 * instead of rendering empty/404 screens.
 */
export const DIRECT_CLOUD_PATH_PREFIXES = [
  '/dashboard/tasks',
  '/dashboard/kanban',
  '/dashboard/automation',
  '/dashboard/agents',
  '/dashboard/skills',
  '/dashboard/settings',
  '/dashboard/seats',
  '/dashboard/upgrade',
  '/dashboard/mobile',
  '/dashboard/notifications',
  '/dashboard/api-keys',
  '/dashboard/shared',
  '/dashboard/join',
] as const;

export const DIRECT_FALLBACK_PATH = '/dashboard/sessions/new';

export function isDirectCloudPath(pathname: string): boolean {
  return DIRECT_CLOUD_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
