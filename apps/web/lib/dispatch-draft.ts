/**
 * The dispatch form's sessionStorage draft.
 *
 * Every access is guarded: private-mode browsers throw on `sessionStorage`,
 * and a draft must never block a dispatch. An all-empty draft is removed
 * instead of being stored so a fresh tab starts clean.
 */

export interface DispatchDraft {
  selectedRule: string;
  eventType: string;
  subject: string;
  detail: string;
}

export interface DraftStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
  removeItem: (key: string) => void;
}

export const DISPATCH_DRAFT_KEY = 'vicoa:dispatch:draft:v1';

export const EMPTY_DISPATCH_DRAFT: DispatchDraft = {
  selectedRule: '',
  eventType: '',
  subject: '',
  detail: '',
};

const DRAFT_FIELDS = [
  'selectedRule',
  'eventType',
  'subject',
  'detail',
] as const;

function browserSessionStorage(): DraftStorage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Parse a stored draft, falling back to a clean slate on any invalid input. */
export function loadDispatchDraft(
  storage: DraftStorage | null = browserSessionStorage(),
): DispatchDraft | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(DISPATCH_DRAFT_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== 'object') return null;
    const record = parsed as Record<string, unknown>;
    const draft = { ...EMPTY_DISPATCH_DRAFT };
    for (const field of DRAFT_FIELDS) {
      const value = record[field];
      if (typeof value !== 'string') return null;
      draft[field] = value;
    }
    return draft;
  } catch {
    return null;
  }
}

export function isDraftEmpty(draft: DispatchDraft): boolean {
  return DRAFT_FIELDS.every((field) => draft[field] === '');
}

export function saveDispatchDraft(
  draft: DispatchDraft,
  storage: DraftStorage | null = browserSessionStorage(),
): void {
  if (!storage) return;
  try {
    if (isDraftEmpty(draft)) {
      storage.removeItem(DISPATCH_DRAFT_KEY);
      return;
    }
    storage.setItem(DISPATCH_DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // Storage is a convenience, never a prerequisite for dispatching.
  }
}

/**
 * Keep a draft event type only when the selected rule still accepts it. A
 * rule with no declared types accepts free-form input, so any non-empty
 * draft value is kept.
 */
export function restoredEventType(
  types: string[],
  fallback: string,
  draft: DispatchDraft | null,
): string {
  if (!draft) return fallback;
  if (types.length === 0) return draft.eventType || fallback;
  return types.includes(draft.eventType) ? draft.eventType : fallback;
}
