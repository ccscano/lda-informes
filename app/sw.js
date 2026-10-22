// Guarda la web para que abra al instante; los datos (/api) siempre van a la red.
const CACHE = "lda-v14";
const SHELL = ["/", "/manifest.webmanifest", "/icono.svg", "/icono-180.png", "/icono-192.png"];
self.addEventListener("install", e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // Red primero (para recibir cambios), caché si no hay conexión
  e.respondWith(fetch(e.request).then(r => {
    const copia = r.clone();
    if (r.ok) caches.open(CACHE).then(c => c.put(e.request, copia));
    return r;
  }).catch(() => caches.match(e.request)));
});
