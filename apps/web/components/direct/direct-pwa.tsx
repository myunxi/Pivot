'use client';

import { useEffect } from 'react';

/** Register the shell service worker for the installed direct-mode PWA. */
export function DirectPwa() {
  useEffect(() => {
    if (process.env.NODE_ENV !== 'production') return;
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) {
      return;
    }

    const register = () => {
      navigator.serviceWorker.register('/sw.js?direct=1').catch((error) => {
        console.error('Failed to register the Vicoa service worker:', error);
      });
    };

    if (document.readyState === 'complete') {
      register();
      return;
    }
    window.addEventListener('load', register, { once: true });
    return () => window.removeEventListener('load', register);
  }, []);

  return null;
}
