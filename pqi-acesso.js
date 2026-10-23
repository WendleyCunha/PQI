/* ═══════════════════════════════════════════════════════════════════
   pqi-acesso.js — incluído em TODOS os sistemas do Painel:
     <script src="/pqi-acesso.js" data-modulo="diario"></script>

   • mostra no canto da tela o nível de acesso da pessoa neste sistema
     (Visualizar · Adicionar · Editar);
   • no perfil "Visualizar", impede que a tela tente gravar (o servidor já
     recusaria — aqui só evita a espera e explica o motivo);
   • quando o servidor recusa algo por permissão (403), mostra o motivo.
   • [v4.16] PERMISSÃO POR ABA: PQI_ACESSO.abas = {aba: 0–4} deste sistema e
     PQI_ACESSO.nivelAba("cx") devolve o nível daquela aba
     (0 Sem acesso · 1 Visualizar · 2 Adicionar · 3 Editar · 4 Excluir).
   A regra de verdade fica no servidor (main.py + permissoes.py): este
   arquivo só deixa a experiência clara.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  var script = document.currentScript;
  var MODULO = (script && script.dataset.modulo) || "";
  var NOMES = { 0: "Sem acesso", 1: "Visualizar", 2: "Adicionar", 3: "Editar" };
  var DICAS = {
    1: "Você pode consultar e baixar relatórios, mas não gravar nada neste sistema.",
    2: "Você pode incluir registros novos e completar campos vazios. O que outras pessoas registraram fica protegido.",
    3: "Você pode incluir, alterar e excluir neste sistema."
  };
  var acesso = { nivel: 3, nivelSistema: 3, perfil: "", email: "", permissoes: {}, abas: null, soAcesso: false, carregado: false };
  window.PQI_ACESSO = acesso;
  // [v4.16] nível de uma aba; sem a lista de abas (sistema sem abas), vale o nível do sistema (Editar = tudo)
  acesso.nivelAba = function (id) {
    if (acesso.abas && id in acesso.abas) return Number(acesso.abas[id]) || 0;
    return acesso.nivelSistema >= 3 ? 4 : acesso.nivelSistema;
  };

  var fetchOriginal = window.fetch.bind(window);
  acesso.pronto = fetchOriginal("/api/auth/me", { cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) {
      if (!j) return acesso;
      acesso.permissoes = j.permissoes || {};
      acesso.nivelSistema = MODULO in acesso.permissoes ? acesso.permissoes[MODULO] : 3;
      acesso.abas = (j.abas && j.abas[MODULO]) || null;
      // uma aba com nível maior que o sistema (ex.: sistema Visualizar, aba CX Adicionar) também libera gravar
      var maxAba = 0;
      if (acesso.abas) Object.keys(acesso.abas).forEach(function (k) { maxAba = Math.max(maxAba, Math.min(3, Number(acesso.abas[k]) || 0)); });
      acesso.nivel = Math.max(acesso.nivelSistema, maxAba);
      acesso.perfil = j.perfil_nome || "";
      acesso.perfilSistema = (j.perfil_sistema && j.perfil_sistema[MODULO]) || null;   // [v4.23]
      acesso.filaQtd = (j.usuario && j.usuario.fila_qtd && j.usuario.fila_qtd[MODULO]) || null;
      // [v4.25] "Ver tudo" por pessoa (Segurança → acesso por pessoa). Desmarcado = só a fila (Qtd). Admin vê tudo.
      acesso.verTudo = !!(j.usuario && (j.usuario.papel === "admin" || (j.usuario.ver_tudo && j.usuario.ver_tudo[MODULO] === true)));
      acesso.email = (j.usuario && j.usuario.email) || "";
      acesso.soAcesso = (j.so_acesso || []).indexOf(MODULO) >= 0;
      acesso.carregado = true;
      document.documentElement.dataset.pqiNivel = String(acesso.nivel);
      if (!acesso.soAcesso && acesso.nivel < 3 && !acesso.abas) selo();
      document.dispatchEvent(new CustomEvent("pqi-acesso", { detail: acesso }));
      return acesso;
    })
    .catch(function () { return acesso; });

  // ── é uma gravação na própria API? (POST/PUT/PATCH/DELETE) ──
  function ehGravacao(url, metodo) {
    if (!metodo || metodo === "GET" || metodo === "HEAD") return false;
    var u = String(url || "");
    return u.indexOf("/api/") === 0 || u.indexOf(location.origin + "/api/") === 0;
  }
  window.fetch = function (url, op) {
    var metodo = ((op && op.method) || "GET").toUpperCase();
    var alvo = typeof url === "string" ? url : (url && url.url) || "";
    // [v4.23] tabular / responder / fila da Carteira: liberado para quem acessa o sistema (o servidor confere)
    var livre = /\/api\/carteira\/(interacoes|reservas|atribuicoes|presenca|evolucao|transferencias)/.test(alvo);
    if (acesso.carregado && !acesso.soAcesso && acesso.nivel <= 1 && !livre && ehGravacao(alvo, metodo) && alvo.indexOf("/api/auth/") < 0) {
      aviso("Seu acesso neste sistema é “Visualizar” — nada foi gravado.");
      return Promise.resolve(new Response(JSON.stringify({ detail: "Seu perfil neste sistema é “Visualizar”: você pode consultar, mas não gravar." }),
        { status: 403, headers: { "Content-Type": "application/json" } }));
    }
    return fetchOriginal(url, op).then(function (r) {
      if (r.status === 403 && ehGravacao(alvo, metodo)) {
        r.clone().json().then(function (j) { aviso((j && j.detail) || "Você não tem permissão para esta alteração."); }).catch(function () {});
      }
      return r;
    });
  };

  // ── selo no canto + aviso ──
  var css = ".pqi-selo{position:fixed;right:14px;bottom:calc(14px + env(safe-area-inset-bottom,0px));z-index:9998;display:flex;align-items:center;gap:8px;" +
    "background:#1B2436;color:#fff;border-radius:999px;padding:7px 14px 7px 10px;font:500 13px/1.2 'IBM Plex Sans',system-ui,sans-serif;box-shadow:0 6px 18px rgba(0,0,0,.22);cursor:help;max-width:calc(100vw - 28px)}" +
    ".pqi-selo b{font-weight:600}.pqi-selo i{font-style:normal;font-size:15px}" +
    ".pqi-aviso{position:fixed;left:50%;top:calc(14px + env(safe-area-inset-top,0px));transform:translateX(-50%);z-index:9999;max-width:min(560px,calc(100vw - 28px));" +
    "background:#B83232;color:#fff;border-radius:10px;padding:11px 16px;font:500 14px/1.4 'IBM Plex Sans',system-ui,sans-serif;box-shadow:0 10px 28px rgba(0,0,0,.28)}" +
    "@media print{.pqi-selo,.pqi-aviso{display:none!important}}";
  function estilo() { if (document.getElementById("pqi-acesso-css")) return; var s = document.createElement("style"); s.id = "pqi-acesso-css"; s.textContent = css; document.head.appendChild(s); }
  function quandoPronto(fn) { if (document.body) fn(); else document.addEventListener("DOMContentLoaded", fn); }
  function selo() {
    quandoPronto(function () {
      estilo();
      var b = document.createElement("div");
      b.className = "pqi-selo"; b.setAttribute("role", "status");
      b.title = DICAS[acesso.nivel] || "";
      b.innerHTML = "<i>" + (acesso.nivel === 1 ? "👁" : "➕") + "</i><span>Seu acesso aqui: <b>" + NOMES[acesso.nivel] + "</b>" + (acesso.perfil ? " · " + acesso.perfil.replace(/</g, "&lt;") : "") + "</span>";
      b.onclick = function () { aviso(DICAS[acesso.nivel] || "", true); };
      document.body.appendChild(b);
    });
  }
  var timer = null;
  function aviso(texto, neutro) {
    quandoPronto(function () {
      estilo();
      var a = document.querySelector(".pqi-aviso");
      if (!a) { a = document.createElement("div"); a.className = "pqi-aviso"; a.setAttribute("role", "alert"); document.body.appendChild(a); }
      a.style.background = neutro ? "#1B2436" : "#B83232";
      a.textContent = texto;
      clearTimeout(timer); timer = setTimeout(function () { a.remove(); }, 6000);
    });
  }
  acesso.aviso = aviso;
})();
