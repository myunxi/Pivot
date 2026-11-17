'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import Link from 'next/link';
import {
  AlertTriangle,
  ExternalLink,
  Loader2,
  MessageCircle,
  RefreshCw,
  Send,
  Trash2,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import {
  createTicket,
  defaultEventType,
  deleteLeftSession,
  deleteTicket,
  formatTicketTime,
  getTicket,
  listRules,
  listTickets,
  manualRules,
  PivotApiError,
  RULES_UPDATED_EVENT,
  type CreateTicketResponse,
  type PivotRule,
  type PivotTicket,
} from '@/lib/pivot-api';
import {
  loadDispatchDraft,
  restoredEventType,
  saveDispatchDraft,
  type DispatchDraft,
} from '@/lib/dispatch-draft';
import { isSelectable, ruleSelection } from '@/lib/dispatch-select';
import { formFieldsFromTicket } from '@/lib/dispatch-ticket';
import {
  allTicketsSelected,
  pruneTicketSelection,
  toggleAllTickets,
  toggleTicketSelection,
} from '@/lib/ticket-selection';

const POLL_INTERVAL_MS = 2000;
const TICKET_LIMIT = 30;

const STATUS_STYLES: Record<string, string> = {
  queued: 'bg-muted text-muted-foreground',
  spawning: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
  running: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
  needs_human: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  done: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  failed: 'bg-red-500/10 text-red-600 dark:text-red-400',
  interrupted: 'bg-red-500/10 text-red-600 dark:text-red-400',
  taken_over: 'bg-violet-500/10 text-violet-600 dark:text-violet-400',
};

type SubmitOutcome =
  | { kind: 'running' | 'deferred' | 'duplicate' | 'no_rule'; response: CreateTicketResponse }
  | { kind: 'error'; message: string };

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function StatusBadge({ state }: { state: string }) {
  return (
    <span
      className={cn(
        'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium',
        STATUS_STYLES[state] ?? 'bg-muted text-muted-foreground',
      )}
    >
      {state}
    </span>
  );
}

function sessionHref(ticket: PivotTicket): string | null {
  if (!ticket.agent_instance_id) return null;
  return `/dashboard/sessions/${ticket.agent_instance_id}`;
}

function SessionLink({ ticket }: { ticket: PivotTicket }) {
  const href = sessionHref(ticket);
  if (!href) return null;
  return (
    <Link
      href={href}
      className="inline-flex cursor-pointer items-center gap-1 underline underline-offset-4"
    >
      Open session
      <ExternalLink className="h-3.5 w-3.5" />
    </Link>
  );
}

export default function DispatchPage() {
  const [rules, setRules] = useState<PivotRule[]>([]);
  const [tickets, setTickets] = useState<PivotTicket[]>([]);
  const [selectedRule, setSelectedRule] = useState('');
  const [eventType, setEventType] = useState('');
  const [subject, setSubject] = useState('');
  const [detail, setDetail] = useState('');
  const [link, setLink] = useState('');
  const [dedupeKey, setDedupeKey] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesError, setRulesError] = useState<string | null>(null);
  const [ticketsError, setTicketsError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<SubmitOutcome | null>(null);
  const [backfillBusy, setBackfillBusy] = useState<string | null>(null);
  const [backfillNotice, setBackfillNotice] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteSummary, setDeleteSummary] = useState<string | null>(null);
  const [deleteWarnings, setDeleteWarnings] = useState<string[]>([]);

  // The draft is restored once; until `draftReady` flips, the save effect must
  // stay quiet or the first render's empty state overwrites the stored draft.
  const [draftReady, setDraftReady] = useState(false);
  const draftRef = useRef<DispatchDraft | null>(null);
  const draftAppliedRef = useRef(false);
  // Set by history backfill so the rule-change effect does not replace the
  // restored event type with the rule's default.
  const preservedEventTypeRef = useRef<string | null>(null);

  const manual = useMemo(() => manualRules(rules), [rules]);
  const activeRule = useMemo(
    () => manual.find((rule) => rule.name === selectedRule) ?? null,
    [manual, selectedRule],
  );
  const activeRuleName = activeRule?.name ?? '';
  const activeTypesKey = activeRule?.when.types.join('\u0000') ?? '';

  const loadRules = useCallback(async () => {
    setRulesLoading(true);
    try {
      const loaded = await listRules();
      const available = manualRules(loaded);
      setRules(loaded);
      setRulesError(null);
      setSelectedRule((current) => {
        const preferred = current || draftRef.current?.selectedRule || '';
        const chosen =
          available.find((rule) => rule.name === preferred) ?? available[0];
        return chosen?.name ?? '';
      });
    } catch (error) {
      setRulesError(messageOf(error));
    } finally {
      setRulesLoading(false);
    }
  }, []);

  const loadTickets = useCallback(async (initial = false) => {
    try {
      const next = await listTickets({ limit: TICKET_LIMIT });
      setTickets(next);
      setSelected((current) =>
        pruneTicketSelection(current, next.map((ticket) => ticket.id)),
      );
      setTicketsError(null);
    } catch (error) {
      // Polling failures keep the current list and selection: only the first
      // load or an explicit refresh shows the error banner.
      if (initial) setTicketsError(messageOf(error));
    }
  }, []);

  // Restore the draft before anything is written back.
  useEffect(() => {
    const draft = loadDispatchDraft();
    draftRef.current = draft;
    if (draft) {
      if (draft.selectedRule) setSelectedRule(draft.selectedRule);
      if (draft.eventType) setEventType(draft.eventType);
      setSubject(draft.subject);
      setDetail(draft.detail);
      setLink(draft.link);
      setDedupeKey(draft.dedupeKey);
    }
    setDraftReady(true);
  }, []);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  useEffect(() => {
    const onRulesUpdated = () => {
      void loadRules();
    };
    window.addEventListener(RULES_UPDATED_EVENT, onRulesUpdated);
    return () => window.removeEventListener(RULES_UPDATED_EVENT, onRulesUpdated);
  }, [loadRules]);

  useEffect(() => {
    void loadTickets(true);
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') void loadTickets();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [loadTickets]);

  // Pick the starting event type whenever the selected rule (or its declared
  // types) changes. Reloading rules with the same name and types must not
  // clobber an event type the user is editing.
  useEffect(() => {
    if (!activeRule) return;
    if (preservedEventTypeRef.current !== null) {
      setEventType(preservedEventTypeRef.current);
      preservedEventTypeRef.current = null;
      draftAppliedRef.current = true;
      return;
    }
    const fallback = defaultEventType(activeRule);
    if (!draftAppliedRef.current) {
      setEventType(restoredEventType(activeRule.when.types, fallback, draftRef.current));
      draftAppliedRef.current = true;
      return;
    }
    setEventType(fallback);
    // activeRule is read but keyed by its stable name + types so a rules
    // reload with identical content does not reset the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRuleName, activeTypesKey]);

  useEffect(() => {
    if (!draftReady) return;
    saveDispatchDraft({
      selectedRule,
      eventType,
      subject,
      detail,
      link,
      dedupeKey,
    });
  }, [draftReady, selectedRule, eventType, subject, detail, link, dedupeKey]);

  const selectRule = useCallback(
    (value: string) => {
      if (!isSelectable(value)) return;
      preservedEventTypeRef.current = null;
      draftAppliedRef.current = true;
      setSelectedRule(value);
      const selection = ruleSelection(rules, value);
      if (selection) setEventType(selection.eventType);
    },
    [rules],
  );

  const selectEventType = useCallback((value: string) => {
    if (!isSelectable(value)) return;
    setEventType(value);
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const type = eventType.trim();
    if (!type) {
      setOutcome({ kind: 'error', message: 'Event type is required.' });
      return;
    }
    setSubmitting(true);
    setOutcome(null);
    setBackfillNotice(null);
    try {
      const response = await createTicket({
        type,
        subject: subject.trim() || undefined,
        detail: detail.trim() || undefined,
        link: link.trim() || undefined,
        key: dedupeKey.trim() || undefined,
      });
      if (response.status === 'failed') {
        setOutcome({ kind: 'error', message: response.error ?? 'Dispatch failed.' });
      } else {
        setOutcome({ kind: response.status, response });
      }
      if (response.status === 'running' || response.status === 'deferred') {
        setSubject('');
        setDetail('');
        setLink('');
        setDedupeKey('');
        void loadTickets(true);
      }
    } catch (error) {
      setOutcome({ kind: 'error', message: messageOf(error) });
    } finally {
      setSubmitting(false);
    }
  };

  const backfill = async (id: string) => {
    setBackfillBusy(id);
    setBackfillNotice(null);
    try {
      const detailResponse = await getTicket(id);
      const fields = formFieldsFromTicket(
        detailResponse,
        manual.map((rule) => rule.name),
      );
      if (!fields) {
        setBackfillNotice('This ticket has no event to restore.');
        return;
      }
      if (fields.rule !== null && fields.rule !== selectedRule) {
        preservedEventTypeRef.current = fields.eventType;
        setSelectedRule(fields.rule);
      }
      setEventType(fields.eventType);
      setSubject(fields.subject);
      setDetail(fields.detail);
      setLink(fields.link);
      // Dedupe key is intentionally left alone.
      if (fields.rule === null) {
        setBackfillNotice(
          'The rule no longer exists; the other fields were filled in.',
        );
      }
    } catch (error) {
      setBackfillNotice(messageOf(error));
    } finally {
      setBackfillBusy(null);
    }
  };

  const requestDelete = () => {
    if (selected.size === 0) return;
    setDeleteSummary(null);
    setDeleteWarnings([]);
    setConfirmingDelete(true);
  };

  const confirmDelete = async () => {
    const ids = [...selected];
    setDeleteBusy(true);
    const outcomes = await Promise.allSettled(ids.map((id) => deleteTicket(id)));
    let deleted = 0;
    let alreadyGone = 0;
    let failed = 0;
    const warnings: string[] = [];

    outcomes.forEach((result, index) => {
      const id = ids[index];
      if (result.status === 'fulfilled') {
        deleted += 1;
        if (deleteLeftSession(result.value)) {
          warnings.push(
            `Ticket ${id} was deleted, but its session was left behind: ${
              result.value.session_error ?? 'unknown error'
            }`,
          );
        }
        return;
      }
      if (result.reason instanceof PivotApiError && result.reason.status === 404) {
        alreadyGone += 1;
        return;
      }
      failed += 1;
      warnings.push(`Ticket ${id} could not be deleted: ${messageOf(result.reason)}`);
    });

    setDeleteSummary(
      `Deleted ${deleted}, already gone ${alreadyGone}, failed ${failed}.`,
    );
    setDeleteWarnings(warnings);
    setConfirmingDelete(false);
    setDeleteBusy(false);
    setSelected(new Set());
    await loadTickets(true);
  };

  const ticketIds = tickets.map((ticket) => ticket.id);
  const allSelected = allTicketsSelected(selected, ticketIds);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Dispatch</h1>
          <p className="text-sm text-muted-foreground">
            Send a manual event to a Pivot rule and watch the ticket it creates.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="cursor-pointer"
          onClick={() => {
            void loadRules();
            void loadTickets(true);
          }}
          disabled={rulesLoading}
        >
          <RefreshCw className={cn('h-4 w-4', rulesLoading && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {rulesError ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600 dark:text-red-400"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Could not load rules: {rulesError}. Is <code>pivot serve</code>{' '}
            running on 127.0.0.1:8787?
          </span>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>New dispatch</CardTitle>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              <div className="space-y-2">
                <Label htmlFor="dispatch-rule">Rule</Label>
                <Select
                  value={selectedRule}
                  onValueChange={selectRule}
                  disabled={manual.length === 0}
                >
                  <SelectTrigger id="dispatch-rule" className="cursor-pointer">
                    <SelectValue
                      placeholder={
                        manual.length === 0
                          ? 'No manual rules'
                          : 'Select a manual rule'
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {manual.map((rule) => (
                      <SelectItem key={rule.name} value={rule.name}>
                        {rule.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {manual.length === 0 && !rulesLoading ? (
                  <p className="text-xs text-muted-foreground">
                    Add a rule with <code>when.source = &quot;manual&quot;</code> to
                    rules.json.
                  </p>
                ) : null}
              </div>

              <div className="space-y-2">
                <Label htmlFor="dispatch-event-type">Event type</Label>
                {activeRule && activeRule.when.types.length > 1 ? (
                  <Select value={eventType} onValueChange={selectEventType}>
                    <SelectTrigger
                      id="dispatch-event-type"
                      className="cursor-pointer"
                    >
                      <SelectValue placeholder="Select an event type" />
                    </SelectTrigger>
                    <SelectContent>
                      {activeRule.when.types.map((type) => (
                        <SelectItem key={type} value={type}>
                          {type}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    id="dispatch-event-type"
                    value={eventType}
                    onChange={(event) => setEventType(event.target.value)}
                    readOnly={
                      activeRule !== null && activeRule.when.types.length === 1
                    }
                    placeholder={
                      activeRule ? 'Event type' : 'Event type (matched by type)'
                    }
                  />
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="dispatch-subject">Subject</Label>
                <Input
                  id="dispatch-subject"
                  value={subject}
                  onChange={(event) => setSubject(event.target.value)}
                  placeholder="Ticket title; also the new session name"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="dispatch-detail">Detail</Label>
                <textarea
                  id="dispatch-detail"
                  value={detail}
                  onChange={(event) => setDetail(event.target.value)}
                  placeholder="Task instructions the agent reads from input.md"
                  className="min-h-32 w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/60 focus-visible:ring-[1px]"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="dispatch-link">Link</Label>
                <Input
                  id="dispatch-link"
                  value={link}
                  onChange={(event) => setLink(event.target.value)}
                  placeholder="https://…"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="dispatch-dedupe">Dedupe key</Label>
                <Input
                  id="dispatch-dedupe"
                  value={dedupeKey}
                  onChange={(event) => setDedupeKey(event.target.value)}
                  placeholder="Optional; the same key never creates two tickets"
                />
              </div>

              <Button
                type="submit"
                className="w-full cursor-pointer"
                disabled={submitting || !eventType.trim()}
              >
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                Dispatch
              </Button>
            </form>

            {outcome ? (
              <div className="mt-4 text-sm" aria-live="polite">
                {outcome.kind === 'error' ? (
                  <p className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-red-600 dark:text-red-400">
                    {outcome.message}
                  </p>
                ) : outcome.kind === 'running' ? (
                  <p className="flex flex-wrap items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 text-emerald-700 dark:text-emerald-400">
                    <span>
                      Ticket{' '}
                      <code className="font-mono">
                        {outcome.response.ticket?.id ?? outcome.response.event}
                      </code>{' '}
                      dispatched.
                    </span>
                    {outcome.response.ticket ? (
                      <SessionLink ticket={outcome.response.ticket} />
                    ) : null}
                  </p>
                ) : outcome.kind === 'deferred' ? (
                  <p className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-amber-700 dark:text-amber-400">
                    Deferred
                    {outcome.response.reason ? `: ${outcome.response.reason}` : ''}
                    {outcome.response.ticket ? (
                      <span className="ml-1 font-mono">
                        ({outcome.response.ticket.id})
                      </span>
                    ) : null}
                  </p>
                ) : outcome.kind === 'duplicate' ? (
                  <p className="rounded-md border border-border bg-muted/40 p-3">
                    Duplicate event — no new ticket was created.
                  </p>
                ) : (
                  <p className="rounded-md border border-border bg-muted/40 p-3">
                    No rule matches event type{' '}
                    <code className="font-mono">{eventType.trim()}</code>.
                  </p>
                )}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <CardTitle>Recent tickets</CardTitle>
            <div className="flex items-center gap-2">
              {selected.size > 0 ? (
                <span className="text-xs text-muted-foreground">
                  {selected.size} selected
                </span>
              ) : null}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="cursor-pointer"
                onClick={requestDelete}
                disabled={selected.size === 0 || deleteBusy}
              >
                <Trash2 className="h-4 w-4" />
                Delete
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {ticketsError ? (
              <p
                role="alert"
                className="mb-3 rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600 dark:text-red-400"
              >
                Could not load tickets: {ticketsError}
              </p>
            ) : null}

            <div className="flex items-center gap-2 border-b pb-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                aria-label="Select all tickets"
                className="h-4 w-4 cursor-pointer accent-primary"
                checked={allSelected}
                onChange={() => setSelected((current) => toggleAllTickets(current, ticketIds))}
              />
              <span>Select all</span>
            </div>

            {tickets.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No tickets yet.
              </p>
            ) : (
              <ul className="divide-y">
                {tickets.map((ticket) => {
                  const href = sessionHref(ticket);
                  return (
                    <li
                      key={ticket.id}
                      className={cn(
                        'flex cursor-pointer items-start gap-3 py-3 hover:bg-muted/40',
                        backfillBusy === ticket.id && 'opacity-60',
                      )}
                      onClick={() => {
                        void backfill(ticket.id);
                      }}
                    >
                      <input
                        type="checkbox"
                        aria-label={`Select ${ticket.title}`}
                        className="mt-1 h-4 w-4 cursor-pointer accent-primary"
                        checked={selected.has(ticket.id)}
                        onClick={(event) => event.stopPropagation()}
                        onChange={() =>
                          setSelected((current) =>
                            toggleTicketSelection(current, ticket.id),
                          )
                        }
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <StatusBadge state={ticket.state} />
                          <span className="truncate text-sm font-medium">
                            {ticket.title}
                          </span>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                          <code className="font-mono">{ticket.id}</code>
                          <span>{ticket.rule}</span>
                          <span>{formatTicketTime(ticket.created_at)}</span>
                        </div>
                      </div>
                      {href ? (
                        <Link
                          href={href}
                          onClick={(event) => event.stopPropagation()}
                          className="inline-flex shrink-0 cursor-pointer items-center gap-1 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                        >
                          <MessageCircle className="h-3.5 w-3.5" />
                          {ticket.state === 'needs_human'
                            ? 'Answer'
                            : 'Open session'}
                        </Link>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}

            {backfillNotice ? (
              <p className="mt-3 text-xs text-muted-foreground" aria-live="polite">
                {backfillNotice}
              </p>
            ) : null}

            {deleteSummary ? (
              <div className="mt-3 space-y-1 text-xs" aria-live="polite">
                <p>{deleteSummary}</p>
                {deleteWarnings.map((warning) => (
                  <p
                    key={warning}
                    className="flex items-start gap-1 text-amber-600 dark:text-amber-400"
                  >
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                    <span>{warning}</span>
                  </p>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <Dialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {selected.size} ticket(s)?</DialogTitle>
            <DialogDescription>
              Their sessions are stopped and hidden, and their input files are
              removed. Worktrees are kept because they may contain uncommitted
              changes.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="cursor-pointer"
              onClick={() => setConfirmingDelete(false)}
              disabled={deleteBusy}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              className="cursor-pointer"
              onClick={() => {
                void confirmDelete();
              }}
              disabled={deleteBusy}
            >
              {deleteBusy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
