/* LifeOps — sw.js (service worker)
   - Keeps a copy of the app's own files (the "shell") so it opens even offline.
   - NEVER caches /api/... : your data is not stored by the browser cache.
   - Pages and files: network first (always the newest version), the saved copy only when offline.
   - Shows push reminders and opens the right screen when one is tapped.
   Bump CACHE when the shell list changes; old caches are deleted on activate. */
'use strict';

const CACHE = 'lifeops-shell-v3';
const SHELL = [
  '/',
  '/offline.html',
  '/assets/css/style.css',
  '/assets/js/app.js',
  '/assets/js/qrcode.js',
  '/assets/js/theme-init.js',
  '/icons/icon-192.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return; // personal data: straight to the network, never cached
  event.respondWith(
    fetch(request, { cache: 'no-cache' })
      .then((response) => {
        if (response.ok && SHELL.includes(url.pathname)) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const saved = await caches.match(request);
        if (saved) return saved;
        if (request.mode === 'navigate') return caches.match('/offline.html');
        return Response.error();
      }),
  );
});

/** The push message as an object (an empty object if it's missing or not JSON). */
function readPush(event) {
  try {
    return event.data ? event.data.json() : {};
  } catch {
    return {};
  }
}

self.addEventListener('push', (event) => {
  const data = readPush(event);
  event.waitUntil(
    self.registration.showNotification(data.title || 'LifeOps', {
      body: data.body || 'You have something due.',
      tag: data.tag || 'lifeops',
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      data: { url: data.url || '/#/today' },
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data.url || '/#/today', self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      const open = windows.find((w) => new URL(w.url).origin === self.location.origin);
      if (open) {
        open.navigate(target);
        return open.focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});
