import { describe, expect, test } from 'vitest';
import type { MessageResponse } from '@/lib/backend-api';
import {
  buildDisplayIndexByKey,
  foldSettledTurns,
  hasLiveFold,
  isFoldedTurnChild,
  turnActivityLabel,
  turnIsLive,
  type TranscriptItem,
  type TurnActivityItem,
} from './turn-folding';

const base: MessageResponse = {
  id: 'x',
  content: '',
  sender_type: 'agent',
  created_at: '2026-01-01T00:00:00.000Z',
  requires_user_input: false,
  message_metadata: null,
};

function message(
  id: string,
  content: string,
  options: {
    sender?: string;
    metadata?: MessageResponse['message_metadata'];
    requiresUserInput?: boolean;
  } = {},
): TranscriptItem {
  return {
    type: 'message',
    key: id,
    message: {
      ...base,
      id,
      content,
      sender_type: options.sender ?? 'agent',
      message_metadata: options.metadata ?? null,
      requires_user_input: options.requiresUserInput ?? false,
    },
  };
}

const user = (id: string, content: string) => message(id, content, { sender: 'user' });
const prose = (id: string, content: string, requiresUserInput = false) =>
  message(id, content, { requiresUserInput });
const thinkingMessage = (id: string) =>
  message(id, 'reasoning…', { metadata: { thinking: { source: 'claude' } } });
const ask = (id: string) =>
  message(id, 'May I proceed?', {
    metadata: {
      ask_user_question: {
        questions: [
          {
            question: 'Proceed?',
            header: 'Confirm',
            options: [{ label: 'Yes', description: '' }],
          },
        ],
      },
    },
  });
const options = (id: string) =>
  message(id, '[OPTIONS]\n1. Yes\n2. No\n[/OPTIONS]');
const tools = (id: string, ...paths: string[]): TranscriptItem => ({
  type: 'tool-group',
  key: `tools-${id}`,
  messages: paths.map((path, index) => ({
    ...base,
    id: `${id}-${index}`,
    content: `Using tool: **Read** - \`${path}\``,
  })),
});
const separator = (date = 'Jan 1, 2026'): TranscriptItem => ({
  type: 'separator',
  date,
  key: `sep-${date}`,
});

function types(items: TranscriptItem[]): string[] {
  return items.map((item) => item.type);
}

function folds(items: TranscriptItem[]): TurnActivityItem[] {
  return items.filter(
    (item): item is TurnActivityItem => item.type === 'turn-activity',
  );
}

describe('foldSettledTurns', () => {
  test('folds tool rows and keeps the final answer visible', () => {
    const items = foldSettledTurns([user('u1', 'go'), tools('t1', '/a'), prose('a1', 'done')]);

    expect(types(items)).toEqual(['message', 'turn-activity', 'message']);
    const [fold] = folds(items);
    expect(fold.items.map((item) => item.key)).toEqual(['tools-t1']);
    expect(fold.stepCount).toBe(1);
    expect(fold.complete).toBe(true);
    expect(fold.live).toBe(false);
  });

  test('keys the fold on the first folded row and keeps it as the turn grows', () => {
    const first = foldSettledTurns(
      [user('u1', 'go'), tools('t1', '/a'), thinkingMessage('th')],
      { liveTurn: true },
    );
    const grown = foldSettledTurns(
      [user('u1', 'go'), tools('t1', '/a'), tools('t2', '/b'), thinkingMessage('th')],
      { liveTurn: true },
    );

    expect(folds(first)[0].key).toBe('turn-activity-tools-t1');
    expect(folds(grown)[0].key).toBe('turn-activity-tools-t1');
    expect(folds(grown)[0].stepCount).toBe(2);
  });

  test('keeps the latest row visible while the turn is live', () => {
    const items = foldSettledTurns(
      [user('u1', 'go'), tools('t1', '/a'), thinkingMessage('th')],
      { liveTurn: true },
    );

    expect(types(items)).toEqual(['message', 'turn-activity', 'message']);
    const [fold] = folds(items);
    expect(fold.live).toBe(true);
    expect(fold.complete).toBe(false);
    expect(fold.items.map((item) => item.key)).toEqual(['tools-t1']);
  });

  test('does not fold a live turn with a single row', () => {
    const items = foldSettledTurns([user('u1', 'go'), thinkingMessage('th')], {
      liveTurn: true,
    });

    expect(folds(items)).toEqual([]);
    expect(types(items)).toEqual(['message', 'message']);
  });

  test('keeps a question visible while the live turn preceding it folds', () => {
    const items = foldSettledTurns([user('u1', 'go'), tools('t1', '/a'), ask('q1')], {
      liveTurn: true,
    });

    expect(types(items)).toEqual(['message', 'turn-activity', 'message']);
    expect(folds(items)[0].items.map((item) => item.key)).toEqual(['tools-t1']);
  });

  test('swallows the formerly visible latest row once the turn settles', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      tools('t1', '/a'),
      thinkingMessage('th'),
    ]);

    expect(types(items)).toEqual(['message', 'turn-activity']);
    expect(folds(items)[0].items.map((item) => item.key)).toEqual(['tools-t1', 'th']);
    expect(folds(items)[0].complete).toBe(false);
  });

  test('folds earlier settled turns even while the last turn is live', () => {
    const items = foldSettledTurns(
      [
        user('u1', 'one'),
        tools('t1', '/a'),
        prose('a1', 'first answer'),
        user('u2', 'two'),
        tools('t2', '/b'),
        thinkingMessage('th'),
      ],
      { liveTurn: true },
    );

    expect(types(items)).toEqual([
      'message',
      'turn-activity',
      'message',
      'message',
      'turn-activity',
      'message',
    ]);
    expect(folds(items).map((fold) => fold.stepCount)).toEqual([1, 1]);
  });

  test('a question splits a turn into separate folds', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      tools('t1', '/a'),
      ask('q1'),
      tools('t2', '/b'),
      prose('a1', 'done'),
    ]);

    expect(types(items)).toEqual([
      'message',
      'turn-activity',
      'message',
      'turn-activity',
      'message',
    ]);
    expect(folds(items).map((fold) => fold.items[0].key)).toEqual([
      'tools-t1',
      'tools-t2',
    ]);
  });

  test('inline options stay visible and split the turn like a question', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      tools('t1', '/a'),
      options('o1'),
      prose('a1', 'done'),
    ]);

    expect(types(items)).toEqual(['message', 'turn-activity', 'message', 'message']);
    expect(folds(items)[0].items.map((item) => item.key)).toEqual(['tools-t1']);
  });

  test('treats requires_user_input on the final answer as prose, not a question', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      tools('t1', '/a'),
      prose('a1', 'done', true),
    ]);

    expect(types(items)).toEqual(['message', 'turn-activity', 'message']);
    expect(folds(items)[0].complete).toBe(true);
  });

  test('folds middle prose even when it carries requires_user_input', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      prose('p1', 'working on it', true),
      prose('a1', 'done'),
    ]);

    expect(types(items)).toEqual(['message', 'turn-activity', 'message']);
    expect(folds(items)[0].items.map((item) => item.key)).toEqual(['p1']);
  });

  test('keeps only the last prose as the answer', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      prose('p1', 'step one'),
      prose('p2', 'step two'),
      prose('a1', 'done'),
    ]);

    expect(folds(items)[0].items.map((item) => item.key)).toEqual(['p1', 'p2']);
  });

  test('marks a turn with no answer as incomplete', () => {
    const items = foldSettledTurns([user('u1', 'go'), tools('t1', '/a')]);
    const [fold] = folds(items);

    expect(fold.complete).toBe(false);
    expect(fold.stepCount).toBe(1);
  });

  test('treats leading output with no user message as a turn', () => {
    const items = foldSettledTurns([separator(), tools('t1', '/a'), prose('a1', 'done')]);

    expect(types(items)).toEqual(['separator', 'turn-activity', 'message']);
  });

  test('a turn with only a user message produces no fold', () => {
    expect(types(foldSettledTurns([user('u1', 'hi')]))).toEqual(['message']);
  });

  test('a separator inside a turn stays in place and is never folded', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      separator(),
      tools('t1', '/a'),
      prose('a1', 'done'),
    ]);

    expect(types(items)).toEqual(['message', 'separator', 'turn-activity', 'message']);
    expect(folds(items)[0].items.map((item) => item.key)).toEqual(['tools-t1']);
  });

  test('never folds the live thinking indicator row', () => {
    const thinkingRow: TranscriptItem = { type: 'thinking', key: 'thinking' };
    const items = foldSettledTurns([user('u1', 'go'), thinkingRow, tools('t1', '/a'), prose('a1', 'done')]);

    expect(types(items)).toEqual(['message', 'thinking', 'turn-activity', 'message']);
  });

  test('returns an empty list unchanged', () => {
    expect(foldSettledTurns([])).toEqual([]);
  });
});

describe('turnActivityLabel', () => {
  test('shows only steps when settled', () => {
    expect(turnActivityLabel({ stepCount: 1, live: false })).toBe('1 step');
    expect(turnActivityLabel({ stepCount: 11, live: false })).toBe('11 steps');
  });

  test('shows the working state and activity while live', () => {
    expect(turnActivityLabel({ stepCount: 11, live: true }, 'Smooshing')).toBe(
      'Working · 11 steps (Smooshing)',
    );
  });

  test('omits the parenthetical when there is no activity text', () => {
    expect(turnActivityLabel({ stepCount: 2, live: true })).toBe('Working · 2 steps');
  });
});

describe('turnIsLive', () => {
  test('follows the debounced agent-thinking signal', () => {
    expect(turnIsLive({ agentThinking: true })).toBe(true);
    expect(turnIsLive({ agentThinking: false })).toBe(false);
  });

  test('ignores session status, which stays ACTIVE between turns', () => {
    expect(turnIsLive({ agentThinking: false, status: 'ACTIVE' })).toBe(false);
    expect(turnIsLive({ agentThinking: false, status: 'AWAITING_INPUT' })).toBe(false);
  });
});

describe('hasLiveFold', () => {
  test('is true only when a live fold is present', () => {
    const live = foldSettledTurns(
      [user('u1', 'go'), tools('t1', '/a'), tools('t2', '/b')],
      { liveTurn: true },
    );
    const settled = foldSettledTurns([user('u1', 'go'), tools('t1', '/a'), tools('t2', '/b')]);

    expect(hasLiveFold(live)).toBe(true);
    expect(hasLiveFold(settled)).toBe(false);
    expect(hasLiveFold([])).toBe(false);
  });
});

describe('buildDisplayIndexByKey', () => {
  test('maps ordinary rows to their own display index', () => {
    const items = foldSettledTurns([user('u1', 'go'), tools('t1', '/a'), prose('a1', 'done')]);
    const map = buildDisplayIndexByKey(items);

    expect(map.get('u1')).toBe(0);
    expect(map.get('tools-t1')).toBe(1);
    expect(map.get('a1')).toBe(2);
  });

  test('points folded children back at the fold row', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      tools('t1', '/a'),
      thinkingMessage('th'),
      prose('a1', 'done'),
    ]);
    const map = buildDisplayIndexByKey(items);

    expect(items[1].type).toBe('turn-activity');
    expect(map.get('tools-t1')).toBe(1);
    expect(map.get('th')).toBe(1);
    expect(map.get('a1')).toBe(2);
  });

  test('does not map the summary key itself (it carries no searchable text)', () => {
    const items = foldSettledTurns([user('u1', 'go'), tools('t1', '/a')]);
    const map = buildDisplayIndexByKey(items);

    expect(map.has('turn-activity-tools-t1')).toBe(false);
  });
});

describe('isFoldedTurnChild', () => {
  test('is true for a row that lives inside a fold', () => {
    const items = foldSettledTurns([
      user('u1', 'go'),
      tools('t1', '/a'),
      thinkingMessage('th'),
      prose('a1', 'done'),
    ]);

    expect(isFoldedTurnChild(items, 'tools-t1')).toBe(true);
    expect(isFoldedTurnChild(items, 'th')).toBe(true);
  });

  test('is false for visible rows and for the summary key', () => {
    const items = foldSettledTurns([user('u1', 'go'), tools('t1', '/a'), prose('a1', 'done')]);

    expect(isFoldedTurnChild(items, 'u1')).toBe(false);
    expect(isFoldedTurnChild(items, 'a1')).toBe(false);
    expect(isFoldedTurnChild(items, 'turn-activity-tools-t1')).toBe(false);
  });

  test('is false when there are no folds at all', () => {
    expect(isFoldedTurnChild([user('u1', 'go'), prose('a1', 'done')], 'a1')).toBe(false);
    expect(isFoldedTurnChild([], 'anything')).toBe(false);
  });
});
