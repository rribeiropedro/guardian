const CACHE_VERSION = "v1";
const MAPBOX_CACHE = `mapbox-tiles-${CACHE_VERSION}`;
const MAX_CACHE_ENTRIES = 400;

function isMapboxRequest(urlString) {
  try {
    const url = new URL(urlString);
    return url.hostname.endsWith(".mapbox.com");
  } catch {
    return false;
  }
}

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;

  const deleteCount = keys.length - maxEntries;
  for (let i = 0; i < deleteCount; i += 1) {
    await cache.delete(keys[i]);
  }
}

self.addEventListener("install", (event) => {
  // Activate immediately so first map session can benefit.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith("mapbox-tiles-") && key !== MAPBOX_CACHE)
          .map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  if (!isMapboxRequest(request.url)) return;

  event.respondWith(
    (async () => {
      const cache = await caches.open(MAPBOX_CACHE);
      const cached = await cache.match(request);
      if (cached) {
        event.waitUntil(
          (async () => {
            try {
              const fresh = await fetch(request);
              if (fresh.ok) {
                await cache.put(request, fresh.clone());
                await trimCache(MAPBOX_CACHE, MAX_CACHE_ENTRIES);
              }
            } catch {
              // Ignore refresh failures and keep serving cached content.
            }
          })(),
        );
        return cached;
      }

      const response = await fetch(request);
      if (response.ok) {
        await cache.put(request, response.clone());
        void trimCache(MAPBOX_CACHE, MAX_CACHE_ENTRIES);
      }
      return response;
    })(),
  );
});
