/**
 * True on touch-first devices (phones, tablets). Used to keep Enter as a plain
 * newline there while desktop keeps Enter-to-send.
 */
export function isCoarsePointer(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia('(pointer: coarse)').matches;
}
