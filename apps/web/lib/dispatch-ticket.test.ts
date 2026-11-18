import { describe, expect, test } from 'vitest';
import { formFieldsFromTicket, joinBody } from './dispatch-ticket';
import type { PivotTicketDetail } from './pivot-api';

function detail(overrides: Partial<PivotTicketDetail> = {}): PivotTicketDetail {
  return {
    ticket: {
      id: 'tkt-1',
      event_id: 'evt-1',
      state: 'done',
      rule: 'demo-rule',
      title: 'Investigate',
      cwd: '/tmp/project',
      agent: 'claude',
      worktree_path: null,
      agent_instance_id: 'inst-1',
      needs_human_since: null,
      created_at: '2026-11-09T04:12:00Z',
      updated_at: '2026-11-09T04:13:00Z',
    },
    event: {
      id: 'evt-1',
      source: 'manual',
      source_key: 'manual:1',
      type: 'demo',
      subject: 'Investigate',
      summary: 'Short summary',
      detail: 'Full detail',
      link: 'https://example.com',
      hops: 0,
      payload: null,
      created_at: '2026-11-09T04:12:00Z',
    },
    runs: [],
    audit: [],
    receipt: null,
    ...overrides,
  };
}

describe('joinBody', () => {
  test('joins summary and detail with a blank line', () => {
    expect(joinBody('summary', 'detail')).toBe('summary\n\ndetail');
  });

  test('drops empty parts and trims whitespace', () => {
    expect(joinBody('  summary  ', '   ')).toBe('summary');
    expect(joinBody(null, ' detail ')).toBe('detail');
    expect(joinBody(null, null)).toBe('');
  });
});

describe('formFieldsFromTicket', () => {
  test('maps a manual event back onto the form', () => {
    expect(formFieldsFromTicket(detail(), ['demo-rule'])).toEqual({
      rule: 'demo-rule',
      eventType: 'demo',
      subject: 'Investigate',
      detail: 'Short summary\n\nFull detail',
    });
  });

  test('clears a subject that was filled in from the event type', () => {
    const withDefaultSubject = detail();
    withDefaultSubject.event = {
      ...withDefaultSubject.event!,
      subject: withDefaultSubject.event!.type,
    };

    expect(formFieldsFromTicket(withDefaultSubject, ['demo-rule'])?.subject).toBe('');
  });

  test('returns a null rule when the rule was deleted', () => {
    expect(formFieldsFromTicket(detail(), ['other-rule'])?.rule).toBeNull();
  });

  test('returns null when the ticket has no event', () => {
    expect(formFieldsFromTicket(detail({ event: null }), ['demo-rule'])).toBeNull();
  });
});
