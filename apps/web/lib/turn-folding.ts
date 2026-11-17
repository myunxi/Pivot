/**
 * Turn folding for the session transcript.
 *
 * A turn is everything after one user message. Once a turn has settled, its
 * reasoning cards, tool runs, sub-agent groups and intermediate prose collapse
 * into one summary row, leaving the turn's final answer visible. Active turns
 * keep their newest row out of the fold so the reader can see what the agent is
 * doing right now.
 *
 * `requires_user_input` is deliberately NOT an interactivity signal: Vicoa sets
 * it on the final answer of a turn too ("the agent is waiting on you"), and
 * treating it as a question would fold the answer or split the turn.
 */

import type { MessageResponse } from '@/lib/backend-api';
import { getMessageVisibleText } from '@/components/dashboard/chat-message-item';
import { isToolUseContent } from '@/components/dashboard/tool-use-parsing';
import { parseThinkingPayload } from '@/components/dashboard/thinking-card';
import { parseAskUserQuestionPayload } from '@/components/dashboard/ask-user-question-panel';
import { extractMessageOptions } from '@/components/ui/message-markdown-utils';

export interface TurnActivityItem {
  type: 'turn-activity';
  key: string;
  items: TranscriptItem[];
  stepCount: number;
  complete: boolean;
  live: boolean;
}

export type TranscriptItem =
  | { type: 'separator'; date: string; key: string }
  | { type: 'message'; message: MessageResponse; key: string }
  | { type: 'tool-group'; messages: MessageResponse[]; key: string }
  | {
      type: 'subagent-group';
      messages: MessageResponse[];
      subagentType: string;
      description: string;
      key: string;
    }
  | { type: 'thinking'; key: string }
  | TurnActivityItem;

export interface TurnFoldingOptions {
  /** The last turn is still producing output; keep its newest row visible. */
  liveTurn?: boolean;
}

const USER_SENDER_TYPES = new Set(['user', 'human', 'USER', 'HUMAN']);

export function isUserMessage(message: MessageResponse): boolean {
  return USER_SENDER_TYPES.has(message.sender_type);
}

/** A real prompt panel or inline options — not `requires_user_input`. */
function isInteractive(message: MessageResponse): boolean {
  if (parseAskUserQuestionPayload(message) !== null) return true;
  return extractMessageOptions(message.content).options.length > 0;
}

function isAgentProse(item: TranscriptItem): boolean {
  if (item.type !== 'message') return false;
  const { message } = item;
  if (isUserMessage(message) || isInteractive(message)) return false;
  if (parseThinkingPayload(message)) return false;
  return !isToolUseContent(getMessageVisibleText(message));
}

function isFoldable(item: TranscriptItem): boolean {
  if (item.type === 'separator' || item.type === 'thinking') return false;
  if (item.type === 'message') {
    if (isUserMessage(item.message) || isInteractive(item.message)) return false;
  }
  return true;
}

/** Split at user messages; leading agent output with no user message is a turn. */
function splitTurns(items: readonly TranscriptItem[]): TranscriptItem[][] {
  const turns: TranscriptItem[][] = [];
  let current: TranscriptItem[] = [];
  for (const item of items) {
    if (item.type === 'message' && isUserMessage(item.message)) {
      if (current.length > 0) turns.push(current);
      current = [item];
    } else {
      current.push(item);
    }
  }
  if (current.length > 0) turns.push(current);
  return turns;
}

function foldTurn(
  turn: TranscriptItem[],
  complete: boolean,
  live: boolean,
): TranscriptItem[] {
  // Only the last prose is the answer; earlier prose is process output.
  let answerIndex = -1;
  for (let index = turn.length - 1; index >= 0; index--) {
    if (isAgentProse(turn[index])) {
      answerIndex = index;
      break;
    }
  }

  const visible = new Set<number>();
  if (answerIndex >= 0) visible.add(answerIndex);
  if (live && turn.length > 0) visible.add(turn.length - 1);

  const output: TranscriptItem[] = [];
  let pending: TranscriptItem[] = [];
  const flush = () => {
    if (pending.length === 0) return;
    output.push({
      type: 'turn-activity',
      key: `turn-activity-${pending[0].key}`,
      items: pending,
      stepCount: pending.length,
      complete,
      live,
    });
    pending = [];
  };

  turn.forEach((item, index) => {
    if (visible.has(index) || !isFoldable(item)) {
      flush();
      output.push(item);
      return;
    }
    pending.push(item);
  });
  flush();
  return output;
}

export function foldSettledTurns(
  items: readonly TranscriptItem[],
  options: TurnFoldingOptions = {},
): TranscriptItem[] {
  const turns = splitTurns(items);
  const liveIndex = options.liveTurn && turns.length > 0 ? turns.length - 1 : -1;
  const output: TranscriptItem[] = [];
  turns.forEach((turn, index) => {
    output.push(...foldTurn(turn, turn.some(isAgentProse), index === liveIndex));
  });
  return output;
}

/**
 * Whether a turn is mid-output. Only the debounced "agent is thinking" signal
 * counts: session status stays ACTIVE between turns in direct mode, and flips
 * to AWAITING_INPUT when an answer asks for input.
 */
export function turnIsLive(signals: {
  agentThinking: boolean;
  status?: string | null;
}): boolean {
  return signals.agentThinking;
}

export function turnActivityLabel(
  item: Pick<TurnActivityItem, 'stepCount' | 'live'>,
  activityText?: string | null,
): string {
  const steps = `${item.stepCount} ${item.stepCount === 1 ? 'step' : 'steps'}`;
  if (!item.live) return steps;
  const base = `Working · ${steps}`;
  return activityText ? `${base} (${activityText})` : base;
}

/** True while a live fold is on screen, so the page footer can hide its vibing hint. */
export function hasLiveFold(items: readonly TranscriptItem[]): boolean {
  return items.some((item) => item.type === 'turn-activity' && item.live);
}

/**
 * Virtuoso row index for each searchable key. A folded child points back at
 * its summary row, so scrolling to a hit inside a fold lands on the fold that
 * contains it. The summary key itself is absent: it carries no text of its own.
 */
export function buildDisplayIndexByKey(
  items: readonly TranscriptItem[],
): Map<string, number> {
  const map = new Map<string, number>();
  items.forEach((item, index) => {
    if (item.type === 'turn-activity') {
      for (const child of item.items) map.set(child.key, index);
    } else {
      map.set(item.key, index);
    }
  });
  return map;
}

/** Whether `itemKey` is one of the rows hidden inside a folded turn. */
export function isFoldedTurnChild(
  items: readonly TranscriptItem[],
  itemKey: string,
): boolean {
  return items.some(
    (item) =>
      item.type === 'turn-activity' &&
      item.items.some((child) => child.key === itemKey),
  );
}
