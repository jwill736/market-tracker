// Plumbline service worker: makes the app installable and opens instantly from the home screen.
// It keeps only the app's own files (page, script, styles, icons). Your portfolio data (/api/...)
// is never stored on the phone: it always comes live from your Plumbline, and when that can't be
// reached the page says so instead of showing old numbers.
const VERSION = "plumbline-v1";
const SHELL = ["/", "/static/app.js", "/static/styles.css", "/static/icon-192.png", "/static/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/login" || url.pathname === "/logout") return;   // always live
  // App files: the network first (so updates show up), the saved copy when offline.
  e.respondWith(fetch(e.request).then((resp) => {
    if (resp.ok && !resp.redirected && (url.pathname === "/" || url.pathname.startsWith("/static/"))) {
      const copy = resp.clone();
      caches.open(VERSION).then((c) => c.put(e.request, copy));
    }
    return resp;
  }).catch(() => caches.match(e.request).then((hit) => hit || (e.request.mode === "navigate" ? caches.match("/") : Response.error()))));
});
