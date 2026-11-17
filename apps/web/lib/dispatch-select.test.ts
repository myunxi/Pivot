import { describe, expect, test } from 'vitest';
import { isSelectable, ruleSelection } from './dispatch-select';
import type { PivotRule } from './pivot-api';

const rules: PivotRule[] = [
  {
    name: 'single',
    when: { source: 'manual', types: ['only'], cron: null, timezone: 'UTC' },
    to: 'claude',
    cwd: '/tmp/project',
    worktree: 'new',
    read_only: false,
    prompt: 'go',
    parallel: 1,
    retry: 0,
  },
  {
    name: 'free-form',
    when: { source: 'manual', types: [], cron: null, timezone: 'UTC' },
    to: 'claude',
    cwd: '/tmp/project',
    worktree: 'new',
    read_only: false,
    prompt: 'go',
    parallel: 1,
    retry: 0,
  },
];

describe('isSelectable', () => {
  test('rejects the empty value Radix emits before options register', () => {
    expect(isSelectable('')).toBe(false);
  });

  test('accepts a real value', () => {
    expect(isSelectable('single')).toBe(true);
  });
});

describe('ruleSelection', () => {
  test('returns the rule and its default event type', () => {
    expect(ruleSelection(rules, 'single')).toEqual({ name: 'single', eventType: 'only' });
  });

  test('falls back to the rule name when the rule declares no types', () => {
    expect(ruleSelection(rules, 'free-form')).toEqual({
      name: 'free-form',
      eventType: 'free-form',
    });
  });

  test('ignores an empty name', () => {
    expect(ruleSelection(rules, '')).toBeNull();
  });

  test('returns null for an unknown rule', () => {
    expect(ruleSelection(rules, 'missing')).toBeNull();
  });
});
