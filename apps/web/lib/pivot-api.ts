/**
 * Client for the Pivot orchestration API (`pivot serve`).
 *
 * The browser never reaches 127.0.0.1:8787 directly: Next proxies
 * `/api/pivot/*` same-origin, and the direct-mode nonce rides along as a
 * Bearer token. That keeps the page working over Tailscale, where the browser
 * may not be on the orchestrator's machine at all.
 */

import { getDesktopConfig } from '@/lib/runtime-config';

export interface PivotRuleWhen {
  source: string | null;
  types: string[];
  cron: string | null;
  timezone: string;
}

export interface PivotRule {
  name: string;
  when: PivotRuleWhen;
  to: string;
  cwd: string;
  worktree: 'new' | 'none';
  read_only: boolean;
  prompt: string;
  group?: string;
  parallel: number;
  timeout?: number;
  retry: number;
  permission_mode?: string;
  model?: string;
}

export interface PivotTicket {
  id: string;
  event_id: string;
  state: string;
  rule: string;
  title: string;
  cwd: string;
  agent: string;
  worktree_path: string | null;
  agent_instance_id: string | null;
  needs_human_since: string | null;
  created_at: string;
  updated_at: string;
}

export interface PivotEvent {
  id: string;
  source: string;
  source_key: string;
  type: string;
  subject: string | null;
  summary: string | null;
  detail: string | null;
  link: string | null;
  hops: number;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface PivotRun {
  id: string;
  ticket_id: string;
  attempt: number;
  status: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface PivotAuditEntry {
  id: number;
  ticket_id: string | null;
  actor: string;
  action: string;
  detail: string | null;
  created_at: string;
}

export interface PivotTicketDetail {
  ticket: PivotTicket;
  event: PivotEvent | null;
  runs: PivotRun[];
  audit: PivotAuditEntry[];
  receipt: unknown;
}

export type DispatchStatus = 'running' | 'deferred' | 'duplicate' | 'no_rule';

export interface CreateTicketRequest {
  type: string;
  subject?: string;
  summary?: string;
  detail?: string;
  link?: string;
  key?: string;
}

export interface CreateTicketResponse {
  status: DispatchStatus | 'failed';
  event: string;
  ticket?: PivotTicket;
  reason?: string;
  error?: string;
}

export interface DeleteTicketResponse {
  id: string;
  deleted: boolean;
  session_deleted: boolean;
  session_error: string | null;
}

export interface RulesFileResponse {
  path: string;
  content: string;
}

export interface SaveRulesFileResponse {
  path: string;
  rules: PivotRule[];
}

export const RULES_UPDATED_EVENT = 'pivot:rules-updated';

export class PivotApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown = null) {
    super(message);
    this.name = 'PivotApiError';
    this.status = status;
    this.body = body;
  }
}

/** The direct-mode nonce, or null on plain web / SSR. */
export function pivotToken(): string | null {
  return getDesktopConfig()?.token ?? null;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function errorMessage(body: unknown): string | null {
  if (body === null || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  const value = record.error ?? record.detail;
  if (typeof value === 'string' && value.trim() !== '') return value;
  if (Array.isArray(value)) {
    const parts = value
      .map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
      .filter((item) => item !== '');
    if (parts.length > 0) return parts.join('; ');
  }
  return null;
}

async function pivotFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = pivotToken();
  if (!token) {
    throw new PivotApiError(
      'Dispatch is only available in direct mode or the desktop app',
      0,
    );
  }
  const response = await fetch(`/api/pivot${path}`, {
    ...init,
    cache: 'no-store',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  const body: unknown = text ? safeJson(text) : null;
  if (!response.ok) {
    throw new PivotApiError(
      errorMessage(body) ?? `Request failed (${response.status})`,
      response.status,
      body,
    );
  }
  return body as T;
}

export async function listRules(): Promise<PivotRule[]> {
  const body = await pivotFetch<{ rules: PivotRule[] }>('/rules');
  return body.rules;
}

export function getRulesFile(): Promise<RulesFileResponse> {
  return pivotFetch<RulesFileResponse>('/rules/file');
}

export function saveRulesFile(content: string): Promise<SaveRulesFileResponse> {
  return pivotFetch<SaveRulesFileResponse>('/rules/file', {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

export async function listTickets(
  options: { state?: string; limit?: number } = {},
): Promise<PivotTicket[]> {
  const params = new URLSearchParams();
  if (options.state) params.set('state', options.state);
  if (options.limit !== undefined) params.set('limit', String(options.limit));
  const query = params.toString();
  const body = await pivotFetch<{ tickets: PivotTicket[] }>(
    `/tickets${query ? `?${query}` : ''}`,
  );
  return body.tickets;
}

export function createTicket(
  request: CreateTicketRequest,
): Promise<CreateTicketResponse> {
  return pivotFetch<CreateTicketResponse>('/tickets', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

export function getTicket(id: string): Promise<PivotTicketDetail> {
  return pivotFetch<PivotTicketDetail>(`/tickets/${encodeURIComponent(id)}`);
}

export function deleteTicket(id: string): Promise<DeleteTicketResponse> {
  return pivotFetch<DeleteTicketResponse>(`/tickets/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

/** Tell the dashboard that rules.json changed, so open pages refetch. */
export function notifyRulesUpdated(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(RULES_UPDATED_EVENT));
}

/** Manual rules only: cron and webhook rules are not dispatchable by hand. */
export function manualRules(rules: PivotRule[]): PivotRule[] {
  return rules.filter(
    (rule) => rule.when.cron === null && rule.when.source === 'manual',
  );
}

/** The event type the form starts on for a rule. */
export function defaultEventType(rule: PivotRule): string {
  return rule.when.types[0] ?? rule.name;
}

/** Local short date for a ticket timestamp; bad input is shown verbatim. */
export function formatTicketTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * A delete left a live session behind when stopping or hiding it failed.
 * ``session_error`` is null for tickets that never had a session, so those do
 * not warn.
 */
export function deleteLeftSession(result: DeleteTicketResponse): boolean {
  return result.session_error !== null;
}
