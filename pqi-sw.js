/* pqi-sw.js — deixa o Painel de Sistemas (PQI) instalável como app no celular.
   Não guarda dado nenhum: só a tela inicial, para abrir com aviso quando estiver sem sinal.
   /motorista/ e /qualidade têm o próprio service worker (escopo mais específico vence). */
const CACHE = "ks-pqi-v1";
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(["/pqi-app/icone-192.png"])).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("ks-pqi") && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || e.request.mode !== "navigate" || u.pathname !== "/") return;
  e.respondWith(fetch(e.request).then(r => { if (r.ok) { const c = r.clone(); caches.open(CACHE).then(k => k.put("/", c)); } return r; })
    .catch(() => caches.match("/").then(r => r || new Response(
      '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font:16px Arial;padding:40px;text-align:center;background:#06070b;color:#f2f0ea">Sem conexão. Confira o sinal e tente de novo.</body>',
      { headers: { "Content-Type": "text/html; charset=utf-8" } }))));
});
