import { describe, expect, test } from 'vitest';
import {
  allTicketsSelected,
  pruneTicketSelection,
  toggleAllTickets,
  toggleTicketSelection,
} from './ticket-selection';

describe('toggleTicketSelection', () => {
  test('adds an unselected id', () => {
    expect([...toggleTicketSelection(new Set(), 'a')]).toEqual(['a']);
  });

  test('removes a selected id', () => {
    expect([...toggleTicketSelection(new Set(['a', 'b']), 'a')]).toEqual(['b']);
  });

  test('does not mutate the input set', () => {
    const selected = new Set(['a']);

    toggleTicketSelection(selected, 'b');

    expect([...selected]).toEqual(['a']);
  });
});

describe('pruneTicketSelection', () => {
  test('drops ids that are no longer listed', () => {
    expect([...pruneTicketSelection(new Set(['a', 'gone', 'b']), ['a', 'b'])]).toEqual(['a', 'b']);
  });

  test('keeps the same set when nothing was removed', () => {
    const selected = new Set(['a']);

    expect(pruneTicketSelection(selected, ['a', 'b'])).toBe(selected);
  });
});

describe('allTicketsSelected', () => {
  test('is false for an empty list', () => {
    expect(allTicketsSelected(new Set(), [])).toBe(false);
  });

  test('is true only when every listed id is selected', () => {
    expect(allTicketsSelected(new Set(['a', 'b']), ['a', 'b'])).toBe(true);
    expect(allTicketsSelected(new Set(['a']), ['a', 'b'])).toBe(false);
  });
});

describe('toggleAllTickets', () => {
  test('selects every listed id when not all are selected', () => {
    expect([...toggleAllTickets(new Set(['a']), ['a', 'b'])]).toEqual(['a', 'b']);
  });

  test('clears every listed id when all are selected', () => {
    expect([...toggleAllTickets(new Set(['a', 'b']), ['a', 'b'])]).toEqual([]);
  });

  test('leaves unrelated ids alone', () => {
    expect([...toggleAllTickets(new Set(['other']), ['a', 'b'])]).toEqual(['other', 'a', 'b']);
  });
});
