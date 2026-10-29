import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  getAttachmentUploadHeaders,
  getAttachmentUploadUrl,
  getAttachmentUrl,
} from './attachment-url';

function stubDirect(apiBase = 'http://127.0.0.1:8765', token = 'direct-token') {
  vi.stubGlobal('window', {
    __VICOA_DESKTOP__: {
      mode: 'local',
      client: 'web',
      direct: true,
      apiBase,
      wsUrl: 'ws://127.0.0.1:8765/ws',
      token,
    },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('direct attachment URLs', () => {
  it('uploads and downloads through the local daemon', () => {
    stubDirect();

    expect(getAttachmentUploadUrl()).toBe('http://127.0.0.1:8765/api/v1/attachments');
    expect(getAttachmentUploadHeaders()).toEqual({
      Authorization: 'Bearer direct-token',
    });
    expect(getAttachmentUrl('abc-123')).toBe(
      'http://127.0.0.1:8765/api/v1/attachments/abc-123?token=direct-token',
    );
  });

  it('keeps the cloud proxy on plain web', () => {
    vi.stubGlobal('window', {});

    expect(getAttachmentUploadUrl()).toBe('/api/attachments');
    expect(getAttachmentUploadHeaders()).toEqual({});
    expect(getAttachmentUrl('abc-123')).toBe('/api/attachments/abc-123');
  });
});
