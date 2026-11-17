'use client';

/**
 * Direct-mode rules.json editor.
 *
 * The daemon validates the whole document before replacing the file and only
 * then swaps `engine.rules`, so a bad save shows the parser's error and leaves
 * both the file and the running rules untouched. A successful save broadcasts
 * `pivot:rules-updated`, which open pages (the dispatch form) listen for.
 */

import { useEffect, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  getRulesFile,
  notifyRulesUpdated,
  saveRulesFile,
} from '@/lib/pivot-api';
import { isRulesEditorDirty, rulesSavedMessage } from '@/lib/rules-editor';

interface RulesEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function RulesEditorDialog({
  open,
  onOpenChange,
}: RulesEditorDialogProps) {
  const [path, setPath] = useState('');
  const [content, setContent] = useState('');
  const [savedContent, setSavedContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Unsaved edits survive closing/reopening the dialog, and a reopen must not
  // refetch over them.
  const dirtyRef = useRef(false);

  useEffect(() => {
    if (!open || dirtyRef.current) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const file = await getRulesFile();
        if (cancelled) return;
        setPath(file.path);
        setContent(file.content);
        setSavedContent(file.content);
        dirtyRef.current = false;
      } catch (loadError) {
        if (!cancelled) setError(messageOf(loadError));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const dirty = isRulesEditorDirty(content, savedContent);

  const handleChange = (value: string) => {
    setContent(value);
    dirtyRef.current = isRulesEditorDirty(value, savedContent);
    setError(null);
    setNotice(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await saveRulesFile(content);
      setPath(result.path);
      setSavedContent(content);
      dirtyRef.current = false;
      setNotice(rulesSavedMessage(result.rules.length));
      notifyRulesUpdated();
    } catch (saveError) {
      // Keep the user's text so they can fix the reported problem.
      dirtyRef.current = true;
      setError(messageOf(saveError));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Edit rules.json</DialogTitle>
          <DialogDescription>
            Validated and hot-reloaded when saved; <code>pivot serve</code>{' '}
            keeps running.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <div className="truncate rounded-md border bg-muted/40 px-3 py-2 font-mono text-xs text-muted-foreground">
            {path || 'Loading path…'}
          </div>
          <textarea
            aria-label="rules.json"
            value={content}
            onChange={(event) => handleChange(event.target.value)}
            readOnly={loading}
            spellCheck={false}
            className="custom-scrollbar h-[55vh] w-full resize-none rounded-md border border-input bg-background px-3 py-2 font-mono text-xs ring-offset-background placeholder:text-muted-foreground/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            placeholder={loading ? 'Loading rules.json…' : '{'}
          />
        </div>

        {error ? (
          <p
            role="alert"
            className="flex items-start gap-1.5 whitespace-pre-wrap rounded-md border border-destructive/30 bg-destructive/5 p-3 font-mono text-xs text-destructive"
          >
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{error}</span>
          </p>
        ) : null}
        {notice ? (
          <p
            role="status"
            className="flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs text-emerald-700 dark:text-emerald-400"
          >
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
            <span>{notice}</span>
          </p>
        ) : null}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="cursor-pointer"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Close
          </Button>
          <Button
            type="button"
            className="cursor-pointer"
            onClick={() => {
              void handleSave();
            }}
            disabled={loading || saving || !dirty}
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
