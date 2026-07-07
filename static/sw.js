/*
 * SHERIA CENTRIC — Service Worker
 *
 * Strategy
 * --------
 * - Precache a small "app shell" of static assets so the installed PWA opens
 *   instantly and survives short network drops.
 * - Network-first for navigation (HTML) requests so logged-in pages always
 *   show fresh data; fall back to the cached offline page if the network
 *   fails.
 * - Cache-first (stale-while-revalidate) for static assets in /static/.
 * - Never cache POST / PUT / DELETE — only GET.
 * - Bypass the cache for anything the app considers sensitive (auth, APIs,
 *   uploads). These must always hit the network.
 */

const VERSION = "sheria-centric-pwa-v2";
const STATIC_CACHE = `${VERSION}-static`;
const RUNTIME_CACHE = `${VERSION}-runtime`;

const APP_SHELL = [
  "/static/manifest.webmanifest",
  "/static/icon.svg",
  "/static/favicon.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/apple-touch-icon.png",
  "/static/offline.html"
];

/* Anything that matches these patterns is considered "must always be fresh"
   and is never served from the cache. */
const NETWORK_ONLY_PATTERNS = [
  /^\/api\//i,
  /^\/login/i,
  /^\/logout/i,
  /^\/signup/i,
  /^\/forgot_password/i,
  /^\/client_login/i,
  /^\/client_manual_login/i,
  /^\/google_/i,
  /^\/oauth/i,
  /\/upload/i
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(APP_SHELL.filter(Boolean)))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith("sheria-centric-pwa-") && !k.startsWith(VERSION))
          .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

function isNetworkOnly(url) {
  return NETWORK_ONLY_PATTERNS.some((re) => re.test(url.pathname));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (isNetworkOnly(url)) return;

  if (req.mode === "navigate" || (req.headers.get("accept") || "").includes("text/html")) {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(req);
          return fresh;
        } catch (err) {
          const cached = await caches.match(req);
          if (cached) return cached;
          const offline = await caches.match("/static/offline.html");
          if (offline) return offline;
          return new Response(
            "<h1>You are offline</h1><p>Try again in a moment.</p>",
            { headers: { "Content-Type": "text/html; charset=utf-8" }, status: 503 }
          );
        }
      })()
    );
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(RUNTIME_CACHE);
        const cached = await cache.match(req);
        const network = fetch(req)
          .then((res) => {
            if (res && res.status === 200 && res.type === "basic") {
              cache.put(req, res.clone());
            }
            return res;
          })
          .catch(() => cached);
        return cached || network;
      })()
    );
  }
});

self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

self.addEventListener("push", (event) => {
  let payload = {
    title: "SHERIA CENTRIC",
    body: "You have new workspace updates.",
    url: "/notifications",
    count: 0,
  };
  try {
    if (event.data) {
      const parsed = event.data.json();
      payload = { ...payload, ...parsed };
    }
  } catch (err) {
  }

  const title = payload.title || "SHERIA CENTRIC";
  const options = {
    body: payload.body || "Open the app to review your notifications.",
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    data: { url: payload.url || "/notifications" },
    tag: "sheria-workspace-alert",
    renotify: true,
    vibrate: [120, 60, 120],
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || "/notifications";
  event.waitUntil(
    (async () => {
      const allClients = await clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of allClients) {
        if ("focus" in client) {
          if (client.url.includes(targetUrl) || client.url.includes("/notifications")) {
            return client.focus();
          }
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })()
  );
});
