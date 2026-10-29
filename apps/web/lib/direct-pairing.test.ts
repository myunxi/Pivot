import { describe, expect, it } from 'vitest';
import {
  deriveWsUrl,
  normalizeServerUrl,
  parsePairLink,
} from './direct-pairing';

describe('direct pairing helpers', () => {
  it('normalizes a server URL to its origin', () => {
    expect(normalizeServerUrl(' https://mbp-home.tail964174.ts.net/ ')).toBe(
      'https://mbp-home.tail964174.ts.net',
    );
    expect(normalizeServerUrl('http://127.0.0.1:8765/path')).toBe(
      'http://127.0.0.1:8765',
    );
    expect(() => normalizeServerUrl('not-a-url')).toThrow();
    expect(() => normalizeServerUrl('ftp://host')).toThrow();
  });

  it('derives the websocket URL', () => {
    expect(deriveWsUrl('https://mbp-home.tail964174.ts.net')).toBe(
      'wss://mbp-home.tail964174.ts.net/ws',
    );
    expect(deriveWsUrl('http://127.0.0.1:8765')).toBe(
      'ws://127.0.0.1:8765/ws',
    );
  });

  it('parses hash and query pairing links', () => {
    expect(
      parsePairLink('https://mbp-home.tail964174.ts.net/#token=abc'),
    ).toEqual({
      serverUrl: 'https://mbp-home.tail964174.ts.net',
      token: 'abc',
    });
    expect(parsePairLink('http://127.0.0.1:8765/?token=xyz')).toEqual({
      serverUrl: 'http://127.0.0.1:8765',
      token: 'xyz',
    });
    expect(parsePairLink('https://mbp-home.tail964174.ts.net/')).toBeNull();
  });
});
