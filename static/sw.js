const CACHE_NAME = 'coach-v1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/style.css',
  '/app.js'
];

// Install: cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch(() => {
        // Ignore 404 errors for assets during install
      });
    })
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  self.clients.claim();
});

// Fetch: cache-first for static, network-first for API
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // API calls: network-first
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.status === 200) {
            const cache = caches.open(CACHE_NAME);
            cache.then((c) => c.put(event.request, response.clone()));
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Static assets: cache-first
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});

// Push notification handler
self.addEventListener('push', (event) => {
  if (!event.data) return;

  let notificationData = { title: 'Notification', body: 'New message' };
  try {
    notificationData = event.data.json();
  } catch (e) {
    notificationData.body = event.data.text();
  }

  event.waitUntil(
    self.registration.showNotification(notificationData.title, {
      body: notificationData.body,
      icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect fill="%231f2937" width="192" height="192"/><text x="96" y="120" font-size="100" font-weight="bold" text-anchor="middle" fill="%23fff" font-family="Arial">💪</text></svg>',
      badge: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect fill="%231f2937" width="192" height="192"/><text x="96" y="120" font-size="100" font-weight="bold" text-anchor="middle" fill="%23fff" font-family="Arial">💪</text></svg>',
      tag: 'briefing',
      requireInteraction: false
    })
  );
});

// Notification click handler
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then((windowClients) => {
      for (let i = 0; i < windowClients.length; i++) {
        const client = windowClients[i];
        if (client.url === '/' && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});
