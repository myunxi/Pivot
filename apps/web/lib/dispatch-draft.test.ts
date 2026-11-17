import { describe, expect, test } from 'vitest';
import {
  DISPATCH_DRAFT_KEY,
  EMPTY_DISPATCH_DRAFT,
  isDraftEmpty,
  loadDispatchDraft,
  restoredEventType,
  saveDispatchDraft,
  type DispatchDraft,
  type DraftStorage,
} from './dispatch-draft';

function memoryStorage(initial: Record<string, string> = {}): DraftStorage & {
  entries: Map<string, string>;
} {
  const entries = new Map(Object.entries(initial));
  return {
    entries,
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => {
      entries.set(key, value);
    },
    removeItem: (key) => {
      entries.delete(key);
    },
  };
}

const draft: DispatchDraft = {
  selectedRule: 'demo-rule',
  eventType: 'demo',
  subject: 'Investigate',
  detail: 'Body',
  link: 'https://example.com',
  dedupeKey: 'key-1',
};

describe('loadDispatchDraft', () => {
  test('round-trips a saved draft', () => {
    const storage = memoryStorage();

    saveDispatchDraft(draft, storage);

    expect(storage.entries.get(DISPATCH_DRAFT_KEY)).toBe(JSON.stringify(draft));
    expect(loadDispatchDraft(storage)).toEqual(draft);
  });

  test('returns null for a missing key', () => {
    expect(loadDispatchDraft(memoryStorage())).toBeNull();
  });

  test('returns null for corrupt JSON', () => {
    const storage = memoryStorage({ [DISPATCH_DRAFT_KEY]: '{not json' });

    expect(loadDispatchDraft(storage)).toBeNull();
  });

  test('returns null when a field is missing', () => {
    const storage = memoryStorage({
      [DISPATCH_DRAFT_KEY]: JSON.stringify({ selectedRule: 'r', eventType: 'e' }),
    });

    expect(loadDispatchDraft(storage)).toBeNull();
  });

  test('returns null when a field has the wrong type', () => {
    const storage = memoryStorage({
      [DISPATCH_DRAFT_KEY]: JSON.stringify({ ...draft, subject: 42 }),
    });

    expect(loadDispatchDraft(storage)).toBeNull();
  });

  test('returns null when storage access throws', () => {
    expect(
      loadDispatchDraft({
        getItem: () => {
          throw new Error('denied');
        },
        setItem: () => {},
        removeItem: () => {},
      }),
    ).toBeNull();
  });
});

describe('saveDispatchDraft', () => {
  test('removes the key instead of storing an all-empty draft', () => {
    const storage = memoryStorage({ [DISPATCH_DRAFT_KEY]: JSON.stringify(draft) });

    saveDispatchDraft(EMPTY_DISPATCH_DRAFT, storage);

    expect(storage.entries.has(DISPATCH_DRAFT_KEY)).toBe(false);
  });

  test('does not throw when storage access fails', () => {
    expect(() =>
      saveDispatchDraft(draft, {
        getItem: () => null,
        setItem: () => {
          throw new Error('quota');
        },
        removeItem: () => {},
      }),
    ).not.toThrow();
  });
});

describe('isDraftEmpty', () => {
  test('is true only when every field is empty', () => {
    expect(isDraftEmpty(EMPTY_DISPATCH_DRAFT)).toBe(true);
    expect(isDraftEmpty({ ...EMPTY_DISPATCH_DRAFT, subject: 'x' })).toBe(false);
  });
});

describe('restoredEventType', () => {
  test('returns the fallback without a draft', () => {
    expect(restoredEventType(['a'], 'a', null)).toBe('a');
  });

  test('restores an accepted draft type', () => {
    expect(restoredEventType(['a', 'b'], 'a', { ...EMPTY_DISPATCH_DRAFT, eventType: 'b' })).toBe('b');
  });

  test('rejects a draft type the rule no longer accepts', () => {
    expect(restoredEventType(['a'], 'a', { ...EMPTY_DISPATCH_DRAFT, eventType: 'gone' })).toBe('a');
  });

  test('keeps any typed value for a rule with no declared types', () => {
    expect(restoredEventType([], 'free-form', { ...EMPTY_DISPATCH_DRAFT, eventType: 'anything' })).toBe(
      'anything',
    );
  });

  test('falls back when a free-form draft type is empty', () => {
    expect(restoredEventType([], 'free-form', EMPTY_DISPATCH_DRAFT)).toBe('free-form');
  });
});
