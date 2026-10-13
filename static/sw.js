// Service Worker for AI Data Insight Builder PWA
const CACHE_NAME = 'insight-builder-v1';
const STATIC_ASSETS = [
  '/',
  '/manifest.json',
  '/static/style.css',
  '/static/app.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Non-GET requests (POST /inspect, /run, /explain, /ask 등) bypass cache
  if (event.request.method !== 'GET') {
    return;
  }

  const url = new URL(event.request.url);

  // Dynamic API endpoints bypass cache and always fetch directly
  if (url.pathname.startsWith('/api') || 
      url.pathname.startsWith('/health') || 
      url.pathname.startsWith('/run') || 
      url.pathname.startsWith('/inspect') || 
      url.pathname.startsWith('/explain') || 
      url.pathname.startsWith('/ask')) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match(event.request);
      })
    );
    return;
  }

  // Network-first strategy for pages and static assets
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200) {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return response;
      })
      .catch(() => {
        return caches.match(event.request);
      })
  );
});
