const CACHE_NAME = 'smartbms-cache-v3';
// These are served from the /static mount, not /uploads (which holds
// uploaded floorplan images). Pointing at /uploads made every fetch 404, and
// because cache.addAll() rejects as a whole on any failure, the service
// worker never finished installing.
const STATIC_ASSETS = [
  '/static/bright_theme.css',
  '/static/graphs.js?v=3',
  '/static/help_system.js',
  '/static/search_system.js',
  '/static/realtime_toasts.js',
  '/static/analytics_widget.js',
  '/static/bulk_import.js',
  '/static/onboarding_tour.js',
  '/static/generate_firmware.js?v=3'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      // Cache each asset independently so one bad entry cannot abort the
      // whole install the way addAll() does.
      .then(cache => Promise.all(
        STATIC_ASSETS.map(url =>
          cache.add(url).catch(err =>
            console.warn('[sw] skipped precache for', url, err)
          )
        )
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(key => key !== CACHE_NAME)
            .map(key => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  // Network-first for HTML pages (always get fresh content)
  if (event.request.mode === 'navigate' || event.request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(event.request)
        .catch(() => caches.match('/') || new Response('Offline', { status: 503 }))
    );
    return;
  }
  
  // Cache-first for static assets only
  event.respondWith(
    caches.match(event.request)
      .then(cached => cached || fetch(event.request).then(response => {
        if (response.ok && STATIC_ASSETS.some(url => event.request.url.includes(url))) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      }))
      .catch(() => new Response('Offline', { status: 503 }))
  );
});
