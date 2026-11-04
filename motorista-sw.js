/* motorista-sw.js — deixa o app instalável e abrir sem sinal.
   A tela fica guardada; dados (/api/) NUNCA vêm do cache: sem sinal, o app guarda
   as baixas no celular e envia quando a conexão voltar. */
const CACHE = "ks-entregas-v1";
const CASCA = ["/motorista/", "/motorista/manifest.webmanifest", "/motorista/icone-192.png", "/motorista/icone-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(CASCA)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  if (!CASCA.includes(u.pathname)) return;
  // rede primeiro (versão nova sempre que houver sinal); sem sinal, a cópia guardada
  e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(CACHE).then(k => k.put(e.request, c)); return r; })
    .catch(() => caches.match(e.request)));
});
