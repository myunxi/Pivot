import { describe, expect, test } from 'vitest';
import {
  defaultEventType,
  deleteLeftSession,
  formatTicketTime,
  manualRules,
  notifyRulesUpdated,
  type PivotRule,
} from './pivot-api';

function rule(overrides: Partial<PivotRule> & { name: string }): PivotRule {
  return {
    when: {
      source: 'manual',
      types: [],
      cron: null,
      timezone: 'UTC',
      ...overrides.when,
    },
    to: 'claude',
    cwd: '/tmp/project',
    worktree: 'new',
    read_only: false,
    prompt: 'do the thing',
    parallel: 1,
    retry: 0,
    ...overrides,
  };
}

describe('manualRules', () => {
  test('keeps only manual rules with no cron', () => {
    const rules = [
      rule({ name: 'manual' }),
      rule({ name: 'cron', when: { source: 'cron', types: [], cron: '0 9 * * *', timezone: 'UTC' } }),
      rule({ name: 'webhook', when: { source: 'webhook', types: ['x'], cron: null, timezone: 'UTC' } }),
      rule({ name: 'with-cron', when: { source: 'manual', types: [], cron: '0 9 * * *', timezone: 'UTC' } }),
    ];

    expect(manualRules(rules).map((item) => item.name)).toEqual(['manual']);
  });

  test('returns an empty array when no rule is manual', () => {
    expect(manualRules([])).toEqual([]);
  });
});

describe('defaultEventType', () => {
  test('uses the first declared type', () => {
    const ruleWithTypes = rule({
      name: 'typed',
      when: { source: 'manual', types: ['first', 'second'], cron: null, timezone: 'UTC' },
    });

    expect(defaultEventType(ruleWithTypes)).toBe('first');
  });

  test('falls back to the rule name when no type is declared', () => {
    expect(defaultEventType(rule({ name: 'free-form' }))).toBe('free-form');
  });
});

describe('formatTicketTime', () => {
  test('formats an ISO timestamp', () => {
    const iso = '2026-11-09T04:12:00Z';

    const formatted = formatTicketTime(iso);

    expect(formatted).not.toBe(iso);
    expect(formatted.length).toBeGreaterThan(0);
  });

  test('returns unparseable input unchanged', () => {
    expect(formatTicketTime('not-a-date')).toBe('not-a-date');
  });
});

describe('deleteLeftSession', () => {
  test('is false when the session was fully removed', () => {
    expect(
      deleteLeftSession({
        id: 'tkt-1',
        deleted: true,
        session_deleted: true,
        session_error: null,
      }),
    ).toBe(false);
  });

  test('is false for a ticket that never had a session', () => {
    expect(
      deleteLeftSession({
        id: 'tkt-1',
        deleted: true,
        session_deleted: false,
        session_error: null,
      }),
    ).toBe(false);
  });

  test('is true when stopping or hiding the session failed', () => {
    expect(
      deleteLeftSession({
        id: 'tkt-1',
        deleted: true,
        session_deleted: false,
        session_error: 'daemon unreachable',
      }),
    ).toBe(true);
  });
});

describe('notifyRulesUpdated', () => {
  test('does not throw without a browser window', () => {
    expect(() => notifyRulesUpdated()).not.toThrow();
  });
});
