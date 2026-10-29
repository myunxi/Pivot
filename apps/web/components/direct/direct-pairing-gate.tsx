'use client';

import { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { DIRECT_TOKEN_KEY } from '@/lib/direct-pairing';

const IS_DIRECT_WEB = process.env.NEXT_PUBLIC_VICOA_DIRECT === '1';

/**
 * The inline bootstrap script covers full page loads. This gate covers
 * client-side transitions after the token disappears (for example, storage
 * cleared in another tab).
 */
export function DirectPairingGate() {
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!IS_DIRECT_WEB || !pathname?.startsWith('/dashboard')) return;
    let token = '';
    try {
      token = localStorage.getItem(DIRECT_TOKEN_KEY) ?? '';
    } catch {
      token = '';
    }
    if (!token) router.replace('/direct-pair');
  }, [pathname, router]);

  return null;
}
