import { describe, expect, test } from 'vitest';
import {
  FOLLOW_OWN_SEND_MAX_DISTANCE_PX,
  distanceFromBottom,
  shouldFollowOwnSend,
} from './chat-scroll';

function scroller(scrollHeight: number, clientHeight: number, scrollTop: number) {
  return { scrollHeight, clientHeight, scrollTop };
}

describe('distanceFromBottom', () => {
  test('returns 0 when scrolled fully to the bottom', () => {
    expect(distanceFromBottom(scroller(1000, 400, 600))).toBe(0);
  });

  test('returns the remaining distance when scrolled up', () => {
    expect(distanceFromBottom(scroller(1000, 400, 200))).toBe(400);
  });

  test('is negative when content fits and scrollTop overshoots', () => {
    expect(distanceFromBottom(scroller(300, 400, 0))).toBe(-100);
  });
});

describe('shouldFollowOwnSend', () => {
  test('follows when exactly at the threshold', () => {
    expect(shouldFollowOwnSend(FOLLOW_OWN_SEND_MAX_DISTANCE_PX)).toBe(true);
  });

  test('follows just inside the threshold', () => {
    expect(shouldFollowOwnSend(FOLLOW_OWN_SEND_MAX_DISTANCE_PX - 1)).toBe(true);
  });

  test('does not follow just past the threshold', () => {
    expect(shouldFollowOwnSend(FOLLOW_OWN_SEND_MAX_DISTANCE_PX + 1)).toBe(false);
  });

  test('does not follow far up in history', () => {
    expect(shouldFollowOwnSend(5000)).toBe(false);
  });

  test('follows when the list is shorter than the viewport (negative distance)', () => {
    expect(shouldFollowOwnSend(-100)).toBe(true);
  });
});
