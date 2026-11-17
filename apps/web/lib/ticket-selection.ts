/**
 * Pure set operations for the dispatch history's multi-select.
 *
 * Every function returns a new Set; callers hold the result in React state.
 * Prune returns the input set unchanged when nothing was removed so polls do
 * not re-render the list for no reason.
 */

export function toggleTicketSelection(
  selected: ReadonlySet<string>,
  id: string,
): Set<string> {
  const next = new Set(selected);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  return next;
}

export function pruneTicketSelection(
  selected: ReadonlySet<string>,
  ids: string[],
): Set<string> {
  const listed = new Set(ids);
  const next = new Set([...selected].filter((id) => listed.has(id)));
  if (next.size === selected.size) return selected as Set<string>;
  return next;
}

export function allTicketsSelected(
  selected: ReadonlySet<string>,
  ids: string[],
): boolean {
  return ids.length > 0 && ids.every((id) => selected.has(id));
}

export function toggleAllTickets(
  selected: ReadonlySet<string>,
  ids: string[],
): Set<string> {
  const next = new Set(selected);
  if (allTicketsSelected(selected, ids)) {
    for (const id of ids) next.delete(id);
    return next;
  }
  for (const id of ids) next.add(id);
  return next;
}
