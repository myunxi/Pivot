import type { Metadata } from 'next';

/**
 * Installability gates for the direct build.
 *
 * Direct mode is a local dashboard; keep the browser from offering to install
 * it. The manifest is what makes a page installable, so the direct build omits
 * it. The offline service worker (`/sw.js?direct=1`) is intentionally kept —
 * it provides the offline shell without advertising an install entry.
 */
export function manifestMetadata(
  direct: boolean,
): Pick<Metadata, 'manifest'> {
  return direct ? {} : { manifest: '/manifest.json' };
}
