/* qualidade-sw.js — deixa a Qualidade instalável como app (Android/Chrome).
   Nada de dado vem do cache: a tela é sempre a versão nova quando há sinal;
   sem sinal, abre a última cópia guardada (as leituras precisam de conexão). */
const CACHE = "ks-qualidade-v1";
const CASCA = ["/qualidade-app/manifest.webmanifest", "/qualidade-app/icone-192.png", "/qualidade-app/icone-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(CASCA)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("ks-qualidade") && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  const tela = e.request.mode === "navigate" && u.pathname === "/qualidade";
  if (!tela && !CASCA.includes(u.pathname)) return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && !r.redirected) { const c = r.clone(); caches.open(CACHE).then(k => k.put(tela ? "/qualidade" : e.request, c)); }
    return r;
  }).catch(() => caches.match(tela ? "/qualidade" : e.request).then(r => r || new Response(
    '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font:16px Arial;padding:40px;text-align:center">Sem conexão. Confira o sinal e tente de novo.</body>',
    { headers: { "Content-Type": "text/html; charset=utf-8" } }))));
});
