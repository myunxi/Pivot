import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  DIRECT_IDENTITY,
  getBrowserAccessToken,
  readBrowserIdentity,
} from './browser-token';

function stubDirect(token: string) {
  vi.stubGlobal('window', {
    __VICOA_DESKTOP__: {
      mode: 'local',
      client: 'web',
      direct: true,
      wsUrl: 'ws://127.0.0.1:8765/ws',
      apiBase: 'http://127.0.0.1:8765',
      token,
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('direct web auth', () => {
  it('uses the injected direct-mode token', async () => {
    stubDirect('direct-token');
    await expect(getBrowserAccessToken()).resolves.toBe('direct-token');
  });

  it('returns the synthetic local identity', async () => {
    stubDirect('direct-token');
    await expect(readBrowserIdentity()).resolves.toEqual(DIRECT_IDENTITY);
    expect(DIRECT_IDENTITY).toEqual({
      id: '88c01271-bc99-5497-9359-9c9dbae1e104',
      email: 'local@vicoa.direct',
      display_name: 'Local User',
    });
  });
});
