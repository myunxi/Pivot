/**
 * How close to the bottom the transcript must be for a user's own send to
 * re-arm bottom-following. Beyond this, the send should leave the viewport
 * where the user parked it (they are reading history).
 */
export const FOLLOW_OWN_SEND_MAX_DISTANCE_PX = 72;

interface TranscriptScroller {
  scrollHeight: number;
  clientHeight: number;
  scrollTop: number;
}

export function distanceFromBottom(s: TranscriptScroller): number {
  return s.scrollHeight - s.clientHeight - s.scrollTop;
}

export function shouldFollowOwnSend(distancePx: number): boolean {
  return distancePx <= FOLLOW_OWN_SEND_MAX_DISTANCE_PX;
}
