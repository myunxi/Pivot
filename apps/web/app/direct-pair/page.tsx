'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  DIRECT_API_BASE_KEY,
  DIRECT_TOKEN_KEY,
  DIRECT_WS_URL_KEY,
  deriveWsUrl,
  normalizeServerUrl,
  parsePairLink,
} from '@/lib/direct-pairing';

const IS_DIRECT_WEB = process.env.NEXT_PUBLIC_VICOA_DIRECT === '1';

export default function DirectPairPage() {
  const router = useRouter();
  const [server, setServer] = useState('');
  const [token, setToken] = useState('');
  const [pairLink, setPairLink] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasExisting, setHasExisting] = useState(false);

  useEffect(() => {
    if (!IS_DIRECT_WEB) {
      router.replace('/dashboard');
      return;
    }
    try {
      setServer(
        localStorage.getItem(DIRECT_API_BASE_KEY) ?? window.location.origin,
      );
      setHasExisting(!!localStorage.getItem(DIRECT_TOKEN_KEY));
    } catch {
      setServer(window.location.origin);
    }
  }, [router]);

  const applyPairLink = (value: string) => {
    setPairLink(value);
    const parsed = parsePairLink(value);
    if (parsed) {
      setServer(parsed.serverUrl);
      setToken(parsed.token);
      setPairLink('');
      setError(null);
    }
  };

  const connect = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const base = normalizeServerUrl(server);
      const trimmedToken = token.trim();
      if (!trimmedToken) throw new Error('Token is required');

      const health = await fetch(`${base}/healthz`, { cache: 'no-store' });
      if (!health.ok) {
        throw new Error(`Server health check failed (${health.status})`);
      }

      const me = await fetch(`${base}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${trimmedToken}` },
        cache: 'no-store',
      });
      if (me.status === 401) {
        throw new Error(
          'Token was rejected. Copy the current token from direct-status.sh --token.',
        );
      }
      if (!me.ok) throw new Error(`Auth check failed (${me.status})`);

      try {
        localStorage.setItem(DIRECT_TOKEN_KEY, trimmedToken);
        localStorage.setItem(DIRECT_API_BASE_KEY, base);
        localStorage.setItem(DIRECT_WS_URL_KEY, deriveWsUrl(base));
      } catch {
        throw new Error('Could not save the connection in this browser');
      }

      window.location.replace('/dashboard/sessions/new');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect');
      setBusy(false);
    }
  };

  return (
    <main className="vicoa-direct-page flex min-h-[100dvh] items-center justify-center bg-background p-6 text-foreground">
      <div className="w-full max-w-md space-y-5 rounded-2xl border border-border bg-card p-6 shadow-sm">
        <div>
          <h1 className="text-xl font-semibold">Connect to your computer</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Pair this browser or installed app with a Vicoa direct-mode daemon.
            Tailscale must be connected.
          </p>
        </div>

        {hasExisting && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/40 p-3 text-sm">
            <span>This device is already paired.</span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => window.location.assign('/dashboard/sessions/new')}
            >
              Open dashboard
            </Button>
          </div>
        )}

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="direct-server">
            Server URL
          </label>
          <Input
            id="direct-server"
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            placeholder="https://your-machine.tailnet.ts.net"
            value={server}
            onChange={(event) => setServer(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            In an installed app this is usually the current origin.
          </p>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="direct-token">
            Token
          </label>
          <Input
            id="direct-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="64-character direct token"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Run <code>direct-status.sh --token</code> on the computer to copy
            the pairing link or token.
          </p>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="direct-pair-link">
            Pairing link (optional)
          </label>
          <Input
            id="direct-pair-link"
            type="url"
            autoComplete="off"
            spellCheck={false}
            placeholder="https://your-machine.tailnet.ts.net/#token=..."
            value={pairLink}
            onChange={(event) => applyPairLink(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Paste a pair link to fill the server and token automatically.
          </p>
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <Button
          type="button"
          className="w-full"
          disabled={busy || !server.trim() || !token.trim()}
          onClick={() => void connect()}
        >
          {busy ? 'Connecting…' : 'Connect'}
        </Button>
      </div>
    </main>
  );
}
