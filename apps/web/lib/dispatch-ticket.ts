/**
 * Map a stored ticket back onto the dispatch form.
 *
 * Manual events with no subject get the event type as their subject on the
 * backend; showing that as a user-written title would invent one, so an equal
 * value is treated as "not filled". The dedupe key is deliberately never
 * restored: reusing an old key would make the next dispatch a duplicate.
 */

import type { PivotTicketDetail } from '@/lib/pivot-api';

export interface TicketFormFields {
  rule: string | null;
  eventType: string;
  subject: string;
  detail: string;
  link: string;
}

export function joinBody(
  summary: string | null,
  detail: string | null,
): string {
  return [summary, detail]
    .map((part) => part?.trim() ?? '')
    .filter((part) => part !== '')
    .join('\n\n');
}

export function formFieldsFromTicket(
  detail: PivotTicketDetail,
  availableRules: string[],
): TicketFormFields | null {
  const event = detail.event;
  if (!event) return null;

  const rule = availableRules.includes(detail.ticket.rule)
    ? detail.ticket.rule
    : null;

  return {
    rule,
    eventType: event.type,
    subject: event.subject === event.type ? '' : (event.subject ?? ''),
    detail: joinBody(event.summary, event.detail),
    link: event.link ?? '',
  };
}
