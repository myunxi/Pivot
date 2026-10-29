// Service Worker for Push Notifications and the direct-mode PWA shell.
//
// Cloud builds register `/sw.js` for push only. The direct build registers
// `/sw.js?direct=1`, which enables the offline shell cache.
const DIRECT_MODE =
  new URL(self.location.href).searchParams.get('direct') === '1';

const CACHE_NAME = 'vicoa-shell-v1';
const SHELL_ASSETS = [
  '/offline.html',
  '/manifest.json',
  '/favicon-192x192.png',
  '/favicon-512x512.png',
];

self.addEventListener('push', function(event) {
  console.log('[Service Worker] Push received.');

  if (event.data) {
    const data = event.data.json();
    console.log('[Service Worker] Push data: ', data);

    const title = data.title || 'Agent Dashboard';
    const options = {
      body: data.body || 'You have a new notification',
      icon: '/favicon.ico',
      badge: '/favicon.ico',
      data: data.data || {},
      requireInteraction: true,
      actions: data.actions || []
    };

    event.waitUntil(
      self.registration.showNotification(title, options)
    );
  }
});

self.addEventListener('notificationclick', function(event) {
  console.log('[Service Worker] Notification click received.');

  event.notification.close();

  // Fall back to the dashboard home (which lists the sessions) when the push
  // carried no target. NOT /dashboard/agents — that is the saved-presets page;
  // it only looked right back when session routes lived under that prefix.
  const url = event.notification.data.url || '/dashboard';

  event.waitUntil(
    clients.openWindow(url)
  );
});

self.addEventListener('install', function(event) {
  console.log('[Service Worker] Installing...');
  if (DIRECT_MODE) {
    event.waitUntil(
      caches
        .open(CACHE_NAME)
        .then((cache) => cache.addAll(SHELL_ASSETS))
        .catch((error) => {
          console.warn('[Service Worker] Shell precache failed:', error);
        })
    );
  }
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  console.log('[Service Worker] Activating...');
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter(
            (name) =>
              name.startsWith('vicoa-shell-') && name !== CACHE_NAME
          )
          .map((name) => caches.delete(name))
      );
      await self.clients.claim();
    })()
  );
});

if (DIRECT_MODE) {
  const isCacheableAsset = (pathname) =>
    pathname.startsWith('/_next/static/') ||
    pathname.startsWith('/images/') ||
    pathname.startsWith('/favicon');

  self.addEventListener('fetch', function(event) {
    const request = event.request;
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    // API calls, health probes and the websocket must always hit the daemon.
    if (
      url.pathname.startsWith('/api/') ||
      url.pathname === '/healthz' ||
      url.pathname === '/ws'
    ) {
      return;
    }

    if (request.mode === 'navigate') {
      event.respondWith(
        fetch(request).catch(async () => {
          const cache = await caches.open(CACHE_NAME);
          return (
            (await cache.match('/offline.html')) ||
            new Response('Offline', { status: 503, statusText: 'Offline' })
          );
        })
      );
      return;
    }

    if (!isCacheableAsset(url.pathname)) return;

    event.respondWith(
      caches.open(CACHE_NAME).then(async (cache) => {
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((response) => {
            if (response.ok) {
              void cache.put(request, response.clone()).catch(() => {});
            }
            return response;
          })
          .catch(() => null);
        return cached || (await network) || Response.error();
      })
    );
  });
}
