export const DIRECT_TOKEN_KEY = 'vicoa.direct.token';
export const DIRECT_API_BASE_KEY = 'vicoa.direct.apiBase';
export const DIRECT_WS_URL_KEY = 'vicoa.direct.wsUrl';

/** Normalize a user-entered direct server URL to its http(s) origin. */
export function normalizeServerUrl(input: string): string {
  const value = input.trim();
  if (!value) throw new Error('Server URL is required');
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error('Enter an absolute http(s) URL');
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('Server URL must use http or https');
  }
  return url.origin;
}

/** `https://host` -> `wss://host/ws`; `http://host` -> `ws://host/ws`. */
export function deriveWsUrl(serverUrl: string): string {
  const url = new URL(normalizeServerUrl(serverUrl));
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = '/ws';
  url.search = '';
  url.hash = '';
  return url.toString();
}

/**
 * Parse a pairing link (`https://host/#token=…` or `?token=…`) into its
 * server origin and token. Returns null when the input is not a pairing link.
 */
export function parsePairLink(
  input: string,
): { serverUrl: string; token: string } | null {
  const value = input.trim();
  if (!value) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  const hash = url.hash.replace(/^#/, '');
  const token =
    new URLSearchParams(hash).get('token') ?? url.searchParams.get('token');
  if (!token) return null;
  return { serverUrl: url.origin, token };
}
