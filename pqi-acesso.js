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
  // [v7.3] tela que é um PASSO do Fluxo interno: o nível é o da aba do passo (Áreas usa a aba "areas")
  var ABA_PASSO = (script && script.dataset.aba) || "";
  try { if (ABA_PASSO && new URLSearchParams(location.search).get("modo") === "areas") ABA_PASSO = "areas"; } catch (_) {}
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
      if (ABA_PASSO) {   // [v7.3] passo do Fluxo interno: vale só a aba do passo (4 = Excluir conta como Editar)
        acesso.nivelSistema = acesso.nivel = Math.min(3, Number((acesso.abas || {})[ABA_PASSO]) || 0);
        acesso.abas = null;
      }
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

/* ═══ [v4.17] ✕ PARA FECHAR NO CANTO SUPERIOR DIREITO DE TODO POPUP ═══
   Toda <dialog> do Painel ganha um ✕ no alto à direita. Ele faz o mesmo que a
   tecla Esc (dispara "cancel" — se a tela não deixar fechar, não fecha). */
(function () {
  "use strict";
  function css() {
    if (document.getElementById("pqi-x-css")) return;
    var s = document.createElement("style"); s.id = "pqi-x-css";
    s.textContent = "dialog.pqi-tem-x{position:fixed}" +
      ".pqi-x{position:absolute;top:10px;right:10px;z-index:5;width:34px;height:34px;border-radius:50%;border:1px solid rgba(0,0,0,.12);" +
      "background:rgba(255,255,255,.92);color:#1B2436;font:600 17px/1 system-ui,sans-serif;cursor:pointer;display:flex;align-items:center;justify-content:center;padding:0}" +
      ".pqi-x:hover{background:#B83232;color:#fff;border-color:#B83232}" +
      "dialog.pqi-tem-x h2:first-of-type,dialog.pqi-tem-x h3:first-of-type{padding-right:42px}";
    document.head.appendChild(s);
  }
  function fechar(d) {
    var ev = new Event("cancel", { cancelable: true });
    if (!d.dispatchEvent(ev)) return;
    if (d.open) d.close();
  }
  function armar(d) {
    if (d.querySelector(":scope > .pqi-x")) return;
    css();
    var b = document.createElement("button");
    b.type = "button"; b.className = "pqi-x"; b.textContent = "✕";
    b.title = "Fechar (Esc)"; b.setAttribute("aria-label", "Fechar");
    b.onclick = function (e) { e.preventDefault(); e.stopPropagation(); fechar(d); };
    d.classList.add("pqi-tem-x");
    d.appendChild(b);   // no fim: o foco automático da janela continua indo para o 1º campo, não para o ✕
  }
  function varrer() { Array.prototype.forEach.call(document.querySelectorAll("dialog"), armar); }
  function iniciar() {
    varrer();
    // telas que trocam o conteúdo da dialog (innerHTML) apagam o ✕: quando ela abre, ele volta
    new MutationObserver(function (ms) {
      ms.forEach(function (m) {
        if (m.type === "attributes" && m.target.tagName === "DIALOG" && m.target.open) armar(m.target);
        if (m.type === "childList") {
          if (m.target.tagName === "DIALOG") armar(m.target);
          Array.prototype.forEach.call(m.addedNodes, function (n) { if (n.nodeType === 1 && n.tagName === "DIALOG") armar(n); });
        }
      });
    }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["open"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar); else iniciar();
})();

/* ═══ [v5.11] TELA ESTÁVEL + ATALHOS (Ctrl+Z · Ctrl+Y · Ctrl+C · Ctrl+V) em todo o Painel ═══
   • ESTÁVEL: quando a tela é redesenhada (render, desenhar…), a altura fica travada
     durante a troca — a página não "encolhe" e por isso não pula para o topo; a rolagem
     das áreas internas (com id) e o campo onde a pessoa estava digitando continuam onde
     estavam. Nunca põe foco num campo que não estava com foco (não abre teclado no celular).
     window.render é estabilizado sozinho; outras funções: PQI_ESTAVEL.estabilizar("nome", …).
   • HISTÓRICO: PQI_ESTAVEL.historico({lista, salvar, redesenhar}) dá Ctrl+Z / Ctrl+Y à tela:
     a tela chama h.registrar(item) a cada alteração (antes de gravar).
   • ATALHOS: dentro de campo de texto, Ctrl+Z/C/V são os do navegador. Fora deles:
     Ctrl+Z desfaz · Ctrl+Y (ou Ctrl+Shift+Z) refaz · Ctrl+C sem texto selecionado copia a
     célula/valor que está sob o mouse · a tela pode tratar Ctrl+C/Ctrl+V (desenhos) com
     PQI_ESTAVEL.aoCopiar / PQI_ESTAVEL.aoColar (devolvem true quando trataram).
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  var E = window.PQI_ESTAVEL = window.PQI_ESTAVEL || {};
  var prof = 0;

  function raiz() { return document.getElementById("root") || document.body; }
  function emCampo(el) { return !!(el && el.closest && el.closest("input,textarea,select,[contenteditable='true'],[contenteditable='']")); }

  // ── tela estável ──
  function antes() {
    var r = raiz(), st = { r: r, mh: r.style.minHeight, rol: [], foco: null };
    r.style.minHeight = r.offsetHeight + "px";
    Array.prototype.forEach.call(r.querySelectorAll("[id]"), function (el) {
      if (el.scrollTop || el.scrollLeft) st.rol.push([el.id, el.scrollTop, el.scrollLeft]);
    });
    var a = document.activeElement;
    if (a && a.id && a !== document.body && r.contains(a)) {
      st.foco = { id: a.id, s: null, e: null };
      try { st.foco.s = a.selectionStart; st.foco.e = a.selectionEnd; } catch (_) {}
    }
    return st;
  }
  function depois(st) {
    st.rol.forEach(function (x) { var el = document.getElementById(x[0]); if (el) { el.scrollTop = x[1]; el.scrollLeft = x[2]; } });
    if (st.foco) {
      var el = document.getElementById(st.foco.id), a = document.activeElement;
      if (el && el !== a && (!a || a === document.body || !a.isConnected)) {
        try { el.focus({ preventScroll: true }); if (st.foco.s != null) el.setSelectionRange(st.foco.s, st.foco.e); } catch (_) {}
      }
    }
    requestAnimationFrame(function () { requestAnimationFrame(function () { st.r.style.minHeight = st.mh; }); });
  }
  E.estabilizar = function () {
    Array.prototype.forEach.call(arguments, function (nome) {
      var f = window[nome];
      if (typeof f !== "function" || f._pqiEstavel) return;
      var g = function () {
        if (prof) return f.apply(this, arguments);   // render dentro de render: o de fora já cuida
        prof++; var st = antes(), res;
        try { res = f.apply(this, arguments); }
        catch (e) { prof--; depois(st); throw e; }
        if (res && typeof res.then === "function") {
          prof--;   // assíncrono: libera para outras ações; a altura fica travada até terminar
          return res.then(function (v) { depois(st); aposRender(); return v; }, function (e) { depois(st); throw e; });
        }
        prof--; depois(st); aposRender(); return res;
      };
      g._pqiEstavel = true; window[nome] = g;
    });
  };
  var hooksRender = [];
  function aposRender() { hooksRender.forEach(function (h) { try { h(); } catch (_) {} }); }
  function auto() { E.estabilizar("render"); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else setTimeout(auto, 0);

  // ── aviso curto no rodapé ──
  E.aviso = function (txt) {
    var a = document.getElementById("pqi-aviso-atalho");
    if (!a) { a = document.createElement("div"); a.id = "pqi-aviso-atalho";
      a.style.cssText = "position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:2147483000;background:#1B2436;color:#fff;font:500 13px system-ui,sans-serif;padding:8px 14px;border-radius:8px;box-shadow:0 6px 18px rgba(0,0,0,.25);max-width:80vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none";
      document.body.appendChild(a); }
    a.textContent = txt; a.style.opacity = "1";
    clearTimeout(a._t); a._t = setTimeout(function () { a.style.opacity = "0"; }, 1800);
  };

  // ── área de transferência ──
  E.copiarTexto = function (txt) {
    txt = String(txt == null ? "" : txt);
    function velho() { var t = document.createElement("textarea"); t.value = txt; t.style.cssText = "position:fixed;left:-9999px;top:0"; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } catch (_) {} t.remove(); }
    try { if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(txt).catch(velho); } catch (_) {}
    velho(); return Promise.resolve();
  };
  var sob = null;
  document.addEventListener("mouseover", function (e) { sob = e.target; }, true);
  E.sob = function () { return sob && sob.isConnected ? sob : null; };
  function textoSob() {
    var el = E.sob(); if (!el || el === document.body || el === document.documentElement) return "";
    var alvo = el.closest("td,th,li,dt,dd,.pill,.chip,.sub,b,small,label,h1,h2,h3,h4") || el;
    if (alvo.closest("button,svg")) return "";
    var t = (alvo.innerText || alvo.textContent || "").replace(/\s+\n/g, "\n").trim();
    return t.length > 0 && t.length <= 400 ? t : "";
  }

  // ── histórico (desfazer / refazer) ──
  var HIST = null;
  E.historico = function (op) {
    var base = {}, undo = [], redo = [], ultT = 0, ultId = null, aplicando = false;
    var js = function (it) { try { return JSON.stringify(it.dados); } catch (_) { return null; } };
    var acha = function (id) { return (op.lista() || []).find(function (x) { return x.id === id; }); };
    var h = {
      semear: function () { if (aplicando) return; (op.lista() || []).forEach(function (it) { if (it && it.id) base[it.id] = js(it); }); },
      registrar: function (it) {
        if (aplicando || !it || !it.id) return;
        var cur = js(it), b = base[it.id], agora = Date.now();
        if (b == null) { base[it.id] = cur; return; }
        if (cur === b) return;
        var digitando = emCampo(document.activeElement);
        if (!(digitando && ultId === it.id && agora - ultT < 900)) { undo.push({ id: it.id, j: b }); if (undo.length > 40) undo.shift(); }
        base[it.id] = cur; redo = []; ultT = agora; ultId = it.id;
      },
      mover: function (de, para, rot) {
        var x = de.pop(); if (!x) { E.aviso(rot === "desfazer" ? "Nada para desfazer." : "Nada para refazer."); return; }
        var it = acha(x.id); if (!it) return;
        para.push({ id: x.id, j: js(it) });
        aplicando = true;
        try { it.dados = JSON.parse(x.j); base[x.id] = x.j; ultId = null; op.salvar(it); op.redesenhar(); }
        finally { aplicando = false; }
        E.aviso(rot === "desfazer" ? "↶ Desfeito (Ctrl+Y refaz)" : "↷ Refeito");
      },
      desfazer: function () { h.mover(undo, redo, "desfazer"); },
      refazer: function () { h.mover(redo, undo, "refazer"); }
    };
    hooksRender.push(h.semear);
    HIST = h; return h;
  };

  // ── teclado ──
  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    var k = (e.key || "").toLowerCase();
    if ("zycv".indexOf(k) < 0 || k === "") return;
    if (emCampo(e.target) || emCampo(document.activeElement)) return;   // campo de texto: atalhos do navegador
    var dlg = document.querySelector("dialog[open]");
    if (k === "z" || k === "y") {
      if (!HIST || dlg) return;
      e.preventDefault();
      if (k === "y" || e.shiftKey) HIST.refazer(); else HIST.desfazer();
      return;
    }
    var sel = window.getSelection && String(window.getSelection() || "");
    if (k === "c") {
      if (sel) return;                                   // texto selecionado: cópia normal
      if (typeof E.aoCopiar === "function" && E.aoCopiar(e)) { e.preventDefault(); return; }
      var t = textoSob(); if (!t) return;
      e.preventDefault(); E.copiarTexto(t); E.aviso("📋 Copiado: " + t.split("\n")[0].slice(0, 60));
      return;
    }
    if (k === "v") {
      if (typeof E.aoColar === "function" && E.aoColar(e)) e.preventDefault();
    }
  });
})();

/* [v6.4] MAPEAMENTO — Organograma, Diagnóstico (Atividades) e Processos abertos DENTRO do módulo
   Mapeamento (?embed=mapeamento). Nesse modo:
   • link para outro desses sistemas NÃO abre um dentro do outro: o Mapeamento troca de passo;
   • o tema claro/escuro é o do Mapeamento (um botão só para todos);
   • some o rodapé e o botão de tema próprio de cada tela. Fora do Mapeamento nada muda. */
(function () {
  "use strict";
  var q;
  try { q = new URLSearchParams(location.search); } catch (_) { return; }
  if (q.get("embed") !== "mapeamento" || window.parent === window) {
    window.PQI_EMBED = null;
    // [v7.3] Organograma e Processos só existem dentro do Fluxo interno: link antigo / aba antiga vão para lá
    if (["/organograma", "/mapa"].indexOf(location.pathname) >= 0) {
      var alvo = location.pathname + location.search;
      try { if (window.top !== window && typeof window.top.PQI_ABRIR_FLUXO === "function" && window.top.PQI_ABRIR_FLUXO(alvo)) { history.length > 1 ? history.back() : location.replace("about:blank"); return; } } catch (_) {}
      location.replace("/mapeamento?ir=" + encodeURIComponent(alvo));
    }
    return;
  }
  var modo = q.get("modo") || "";
  var raiz = document.documentElement;
  raiz.classList.add("pqi-embed");
  if (modo) raiz.classList.add("pqi-embed-" + modo);
  function tema(t) { if (t === "light" || t === "dark") raiz.setAttribute("data-theme", t); }
  try { tema(localStorage.getItem("pqi_mapeamento_tema")); } catch (_) {}
  window.PQI_EMBED = {
    modo: modo,
    ir: function (url) { window.parent.postMessage({ pqi: "mapeamento-ir", url: url }, location.origin); }
  };
  var css = document.createElement("style");
  css.textContent = "html.pqi-embed .doc-foot,html.pqi-embed #btnTema{display:none!important}" +
    "html.pqi-embed-areas .side-rg,html.pqi-embed-areas #btnVoltar{display:none!important}" +
    "html.pqi-embed-areas .shell-item{grid-template-columns:1fr!important;display:block!important}";
  (document.head || raiz).appendChild(css);
  // [v7.2] (i) explicativo em cada campo do Fluxo interno
  var aj = document.createElement("script"); aj.src = "/pqi-ajuda.js"; aj.defer = true; (document.head || raiz).appendChild(aj);
  var SISTEMAS = ["/organograma", "/diagnostico", "/mapa"];
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
    var u; try { u = new URL(a.getAttribute("href"), location.href); } catch (_) { return; }
    if (u.origin !== location.origin || SISTEMAS.indexOf(u.pathname) < 0) return;
    e.preventDefault();
    window.PQI_EMBED.ir(u.pathname + u.search);
  }, true);
  window.addEventListener("message", function (e) {
    if (e.origin === location.origin && e.data && e.data.pqi === "tema") {
      tema(e.data.tema);
      try { localStorage.setItem("diag_tema", e.data.tema); } catch (_) {}
    }
  });
})();
