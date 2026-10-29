import { describe, expect, it } from 'vitest';
import {
  DIRECT_CLOUD_PATH_PREFIXES,
  DIRECT_FALLBACK_PATH,
  isDirectCloudPath,
} from './direct-mode-routes';

describe('isDirectCloudPath', () => {
  it('matches every cloud-only route and its subpaths', () => {
    for (const prefix of DIRECT_CLOUD_PATH_PREFIXES) {
      expect(isDirectCloudPath(prefix)).toBe(true);
      expect(isDirectCloudPath(`${prefix}/`)).toBe(true);
      expect(isDirectCloudPath(`${prefix}/child`)).toBe(true);
    }
  });

  it('keeps the session routes available', () => {
    expect(isDirectCloudPath('/dashboard')).toBe(false);
    expect(isDirectCloudPath('/dashboard/sessions/new')).toBe(false);
    expect(isDirectCloudPath('/dashboard/sessions/abc')).toBe(false);
  });

  it('does not match lookalike prefixes', () => {
    expect(isDirectCloudPath('/dashboard/tasksomething')).toBe(false);
    expect(isDirectCloudPath('/dashboard/settings-backup')).toBe(false);
    expect(isDirectCloudPath('/dashboard/shared-with-me')).toBe(false);
  });

  it('falls back to New Session', () => {
    expect(DIRECT_FALLBACK_PATH).toBe('/dashboard/sessions/new');
  });
});
