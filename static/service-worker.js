const CACHE_NAME = 'smartbms-cache-v15';
// These are served from the /static mount, not /uploads (which holds
// uploaded floorplan images). Pointing at /uploads made every fetch 404, and
// because cache.addAll() rejects as a whole on any failure, the service
// worker never finished installing.
const STATIC_ASSETS = [
  '/static/vendor/chart.js-4.5.1/chart.umd.min.js',
  '/static/vendor/intro.js-7.2.0/intro.min.js',
  '/static/vendor/intro.js-7.2.0/introjs.min.css',
  '/static/vendor/fonts.css',
  '/static/icon.svg',
  '/static/vendor/dm-sans-5.3.0/files/dm-sans-latin-ext-wght-normal.woff2',
  '/static/vendor/dm-sans-5.3.0/files/dm-sans-latin-wght-normal.woff2',
  '/static/vendor/manrope-5.3.0/files/manrope-cyrillic-ext-wght-normal.woff2',
  '/static/vendor/manrope-5.3.0/files/manrope-cyrillic-wght-normal.woff2',
  '/static/vendor/manrope-5.3.0/files/manrope-greek-wght-normal.woff2',
  '/static/vendor/manrope-5.3.0/files/manrope-latin-ext-wght-normal.woff2',
  '/static/vendor/manrope-5.3.0/files/manrope-latin-wght-normal.woff2',
  '/static/vendor/manrope-5.3.0/files/manrope-vietnamese-wght-normal.woff2',
  '/static/bright_theme.css',
  '/static/admin_dashboard.css?v=5',
  '/static/workspace.css?v=7',
  '/static/workspace.js?v=4',
  '/static/device_enrollment.js?v=3',
  '/static/device_downlinks.js?v=1',
  '/static/device_configuration.js?v=1',
  '/static/management_tools.js?v=1',
  '/static/signin.css?v=3',
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
