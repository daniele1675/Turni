// Service worker: l'app si apre anche senza rete, mostrando gli ultimi turni scaricati.
const CACHE = "turni-v7";
const SHELL = ["/", "/static/app.js", "/static/style.css", "/manifest.webmanifest",
  "/static/icon-192.png", "/static/icon-512.png", "/static/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname === "/api/data" || SHELL.includes(url.pathname)) {
    // Prima la rete, poi la copia salvata.
    e.respondWith(
      fetch(e.request).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(url.pathname + url.search, copy)); }
        return res;
      }).catch(() => caches.match(url.pathname + url.search))
    );
  }
});
