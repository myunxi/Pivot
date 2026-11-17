import { describe, expect, test } from 'vitest';
import { manifestMetadata } from './direct-installability';

describe('manifestMetadata', () => {
  test('omits the manifest in a direct build', () => {
    expect(manifestMetadata(true)).toEqual({});
  });

  test('keeps the manifest for cloud and desktop builds', () => {
    expect(manifestMetadata(false)).toEqual({ manifest: '/manifest.json' });
  });
});
