/* pqi-agente.js — [v6.0] Agente IA no topo do Painel (base de conhecimento)
 *
 * Botão "Agente" na barra do topo (ao lado de Início / Expandir / Tema) que abre um popup:
 *   • todo mundo: conversa — pergunta → passo a passo oficial + fonte + 👍/👎;
 *   • Administrador e perfil Gerente: ⚙️ dentro do popup → Artigos, Pendências, Histórico, Painel.
 * Usa as cores do PQI (variáveis --panel, --gold… do index.html) com o toque "aurora"
 * do Wendley Desenvolvimento. API em agente.py (/api/agente/...).
 * index.html chama PQIAgente.iniciar() depois do login e PQIAgente.parar() ao sair.
 */
(function () {
  'use strict';
  if (window.PQIAgente) return;

  const CSS = `
.ag-btn{position:relative;display:flex;align-items:center;gap:7px;padding:8px 14px;border-radius:10px;cursor:pointer;
  font:600 12.5px 'Space Grotesk','Inter',sans-serif;color:var(--ink);border:1px solid transparent;
  background:linear-gradient(var(--panel-2),var(--panel-2)) padding-box,
             linear-gradient(120deg,rgba(232,179,61,.85),rgba(139,92,246,.85),rgba(34,211,238,.75)) border-box;
  transition:transform .25s cubic-bezier(.2,.9,.25,1.15),box-shadow .25s}
.ag-btn:hover{transform:translateY(-1px);box-shadow:0 6px 22px -8px rgba(139,92,246,.7)}
.ag-btn.aberto{box-shadow:0 0 0 3px rgba(139,92,246,.22)}
.ag-orb{width:15px;height:15px;border-radius:50%;flex:none;
  background:radial-gradient(circle at 35% 30%,#fff,#e8b33d 42%,#fb7185 78%);box-shadow:0 0 10px rgba(232,179,61,.85);animation:agPulse 2.8s ease-in-out infinite}
@keyframes agPulse{0%,100%{transform:scale(1)}50%{transform:scale(1.16)}}
.ag-n{position:absolute;top:-6px;right:-6px;min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:var(--vermelho);color:#fff;
  font:700 10.5px Inter,sans-serif;display:none;align-items:center;justify-content:center}
@media (max-width:760px){.ag-btn span.ag-txt{display:none}}

.ag-pop{position:fixed;z-index:9000;right:14px;width:420px;max-width:calc(100vw - 20px);border-radius:20px;overflow:hidden;
  display:flex;flex-direction:column;color:var(--ink);font:14px Inter,sans-serif;
  background:color-mix(in srgb,var(--panel) 88%,transparent);backdrop-filter:saturate(160%) blur(20px);-webkit-backdrop-filter:saturate(160%) blur(20px);
  border:1px solid var(--line);box-shadow:0 30px 80px -24px rgba(0,0,0,.65),0 0 0 1px rgba(139,92,246,.12);
  transform-origin:top right;transform:scale(.94) translateY(-6px);opacity:0;pointer-events:none;
  transition:transform .32s cubic-bezier(.2,.9,.25,1.15),opacity .18s,width .3s cubic-bezier(.4,0,.2,1)}
.ag-pop.aberto{transform:none;opacity:1;pointer-events:auto}
.ag-pop.largo{width:880px}
.ag-pop::before{content:"";position:absolute;inset:-60px -40px auto;height:220px;pointer-events:none;opacity:.55;filter:blur(14px);
  background:radial-gradient(closest-side,rgba(139,92,246,.32),transparent 70%),radial-gradient(closest-side at 72% 55%,rgba(34,211,238,.24),transparent 70%),
             radial-gradient(closest-side at 28% 70%,rgba(232,179,61,.24),transparent 70%);animation:agAur 14s ease-in-out infinite alternate}
@keyframes agAur{to{transform:translate(6%,8%) rotate(7deg)}}
.ag-cab{position:relative;display:flex;align-items:center;gap:10px;padding:13px 13px 11px;border-bottom:1px solid var(--line)}
.ag-av{width:38px;height:38px;border-radius:13px;display:grid;place-items:center;flex:none;
  background:linear-gradient(135deg,rgba(139,92,246,.95),rgba(34,211,238,.8));box-shadow:0 0 16px rgba(139,92,246,.45)}
.ag-av i{width:15px;height:15px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#fff,#e8b33d 45%,#fb7185);box-shadow:0 0 9px rgba(232,179,61,.9)}
.ag-cab .ag-tit{flex:1;min-width:0}
.ag-cab b{display:block;font:700 15px 'Space Grotesk',Inter,sans-serif}
.ag-cab small{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink-muted)}
.ag-cab small::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--verde);box-shadow:0 0 8px var(--verde)}
.ag-ib{width:34px;height:34px;border-radius:11px;border:1px solid var(--line);background:var(--panel-2);color:var(--ink);cursor:pointer;
  display:grid;place-items:center;font-size:15px;transition:transform .25s cubic-bezier(.2,.9,.25,1.15),border-color .2s}
.ag-ib[hidden]{display:none}
.ag-ib:hover{border-color:var(--gold)}
.ag-ib.eng:hover{transform:rotate(30deg)}
.ag-ib.on{border-color:var(--gold);background:color-mix(in srgb,var(--gold) 16%,var(--panel-2))}

.ag-chat{position:relative;flex:1;overflow:auto;padding:13px;display:flex;flex-direction:column;gap:9px;min-height:200px}
.ag-m{max-width:90%;padding:10px 12px;border-radius:15px;line-height:1.5;animation:agIn .3s cubic-bezier(.2,.9,.25,1.15);word-wrap:break-word}
@keyframes agIn{from{opacity:0;transform:translateY(5px)}}
.ag-eu{align-self:flex-end;background:linear-gradient(135deg,var(--gold-soft),var(--gold));color:#1b1405;border-bottom-right-radius:5px;font-weight:500}
.ag-ia{align-self:flex-start;background:var(--panel-2);border:1px solid var(--line);border-bottom-left-radius:5px}
.ag-ia h4{margin:0 0 6px;font:700 14px 'Space Grotesk',Inter,sans-serif}
.ag-ia ol{margin:0;padding-left:19px}.ag-ia li{margin:3px 0}
.ag-src{margin-top:8px;padding-top:6px;border-top:1px dashed var(--line);font-size:11.5px;color:var(--ink-muted)}
.ag-src b{color:var(--azul)}
.ag-fb{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
.ag-fb button,.ag-sug button{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:9px;padding:5px 10px;font:12px Inter,sans-serif;cursor:pointer}
.ag-fb button:hover,.ag-sug button:hover{border-color:var(--gold)}
.ag-sug{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.ag-sug button{border-radius:20px}
.ag-ok{color:var(--verde)}.ag-av2{color:var(--gold)}
.ag-dig span{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;animation:agB 1s infinite}
.ag-dig span:nth-child(1){background:#8b5cf6}.ag-dig span:nth-child(2){background:#22d3ee;animation-delay:.15s}.ag-dig span:nth-child(3){background:#e8b33d;animation-delay:.3s}
@keyframes agB{0%,100%{opacity:.3;transform:none}50%{opacity:1;transform:translateY(-3px)}}
.ag-pe{position:relative;display:flex;gap:8px;padding:10px 12px 12px;border-top:1px solid var(--line)}
.ag-pe input{flex:1;min-width:0;background:var(--panel-2);border:1px solid var(--line);border-radius:13px;padding:11px 12px;color:var(--ink);font:14px Inter,sans-serif;outline:none}
.ag-pe input:focus{border-color:rgba(139,92,246,.8);box-shadow:0 0 0 3px rgba(139,92,246,.18)}
.ag-env{width:46px;border:0;border-radius:13px;cursor:pointer;color:#fff;font-size:17px;background:linear-gradient(135deg,#8b5cf6,#22d3ee)}
.ag-env:disabled{opacity:.5;cursor:default}

.ag-cfg{position:relative;display:none;flex:1;flex-direction:column;min-height:0}
.ag-pop.modo-cfg .ag-chat,.ag-pop.modo-cfg .ag-pe{display:none}
.ag-pop.modo-cfg .ag-cfg{display:flex}
.ag-abas{display:flex;gap:2px;padding:6px 10px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
.ag-abas button{border:0;background:none;color:var(--ink-muted);padding:9px 12px;border-bottom:2px solid transparent;font:600 13px Inter,sans-serif;cursor:pointer}
.ag-abas button.on{color:var(--ink);border-bottom-color:var(--gold)}
.ag-abas .c{background:var(--vermelho);color:#fff;border-radius:9px;font-size:10.5px;padding:1px 6px;margin-left:4px}
.ag-cc{flex:1;overflow:auto;padding:13px}
.ag-g2{display:grid;grid-template-columns:1fr 1.15fr;gap:12px}
@media (max-width:760px){.ag-g2{grid-template-columns:1fr}}
.ag-cx{background:var(--panel-2);border:1px solid var(--line);border-radius:14px;padding:12px}
.ag-cx h5{margin:0 0 8px;font:700 14px 'Space Grotesk',Inter,sans-serif}
.ag-art{border:1px solid var(--line);border-radius:11px;padding:9px 10px;margin-bottom:7px;cursor:pointer;transition:border-color .15s,background .15s}
.ag-art:hover,.ag-art.sel{border-color:var(--gold);background:color-mix(in srgb,var(--gold) 7%,transparent)}
.ag-art .t{font-weight:700;font-size:13px}.ag-art .m{font-size:11.5px;color:var(--ink-muted);margin-top:2px}
.ag-tg{display:inline-block;font-size:10.5px;font-weight:700;border-radius:6px;padding:1px 6px;margin-right:4px;background:rgba(139,92,246,.16);color:var(--violet)}
.ag-tg.off{background:rgba(128,128,128,.2);color:var(--ink-muted)}
.ag-cfg label{display:block;font:700 11.5px Inter,sans-serif;color:var(--ink-muted);margin:9px 0 4px}
.ag-f{width:100%;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:8px 10px;font:13.5px Inter,sans-serif;box-sizing:border-box}
textarea.ag-f{min-height:130px;resize:vertical}
.ag-bt{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.ag-b1{border:0;border-radius:10px;padding:8px 14px;font:700 13px Inter,sans-serif;cursor:pointer;color:#1b1405;background:linear-gradient(135deg,var(--gold-soft),var(--gold))}
.ag-b2{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:10px;padding:8px 14px;font:600 13px Inter,sans-serif;cursor:pointer}
.ag-b1:disabled{opacity:.6;cursor:default}
.ag-dica{font-size:11.5px;color:var(--ink-faint);margin-top:4px}
.ag-pd{border:1px solid var(--line);border-radius:12px;padding:10px;margin-bottom:8px}
.ag-pd .q{font-weight:700}.ag-pd .m{font-size:11.5px;color:var(--ink-muted);margin:3px 0 8px}
.ag-w{font-size:10.5px;font-weight:700;border-radius:6px;padding:1px 6px}
.ag-w.n{background:color-mix(in srgb,var(--gold) 18%,transparent);color:var(--gold)}.ag-w.b{background:color-mix(in srgb,var(--vermelho) 18%,transparent);color:var(--vermelho)}
.ag-cfg table{width:100%;border-collapse:collapse;font-size:12.5px}
.ag-cfg th{text-align:left;color:var(--ink-muted);font-weight:600;border-bottom:1px solid var(--line);padding:7px}
.ag-cfg td{border-bottom:1px solid var(--line);padding:7px;vertical-align:top}
.ag-k{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:12px}
.ag-k div{background:var(--panel-2);border:1px solid var(--line);border-radius:12px;padding:12px}
.ag-k b{display:block;font:700 24px 'Space Grotesk',Inter,sans-serif}.ag-k span{font-size:12px;color:var(--ink-muted)}
.ag-bar{height:7px;background:var(--line);border-radius:5px;overflow:hidden;margin-top:5px}
.ag-bar i{display:block;height:100%;background:linear-gradient(90deg,#8b5cf6,#22d3ee)}
.ag-vz{color:var(--ink-muted);text-align:center;padding:18px}
.ag-erro{color:var(--vermelho);font-size:12.5px;margin-top:8px}
.ag-toast{position:fixed;left:50%;bottom:20px;z-index:9100;transform:translate(-50%,16px);opacity:0;pointer-events:none;transition:.25s;
  background:var(--panel-2);color:var(--ink);border:1px solid var(--gold);border-radius:12px;padding:10px 16px;font:13px Inter,sans-serif}
.ag-toast.on{transform:translate(-50%,0);opacity:1}
`;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dataBR = iso => { if (!iso) return ''; const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('pt-BR') + ' ' + d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); };
  const diaBR = iso => { if (!iso) return ''; const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('pt-BR'); };

  let ME = null, BTN = null, POP = null, TOAST = null, TIMER = null, ABA = 'art', ARTS = [], SEL = null, PEND_ATUAL = null, ocupado = false;

  async function api(url, metodo, corpo) {
    const op = { method: metodo || 'GET', headers: { 'Accept': 'application/json' }, cache: 'no-store', credentials: 'same-origin' };
    if (corpo !== undefined) { op.headers['Content-Type'] = 'application/json'; op.body = JSON.stringify(corpo); }
    const r = await fetch('/api/agente' + url, op);
    let j = null; try { j = await r.json(); } catch (_) {}
    if (!r.ok) {
      let msg = (j && j.detail) || ('Erro ' + r.status);
      if (Array.isArray(msg)) msg = msg.map(x => (x.msg || '').replace(/^Value error, /, '')).join(' ');
      throw new Error(msg);
    }
    return j;
  }

  function toast(m) {
    TOAST.textContent = m; TOAST.classList.add('on');
    clearTimeout(TOAST._t); TOAST._t = setTimeout(() => TOAST.classList.remove('on'), 2400);
  }

  /* ───────── montagem ───────── */
  function montar() {
    if (BTN) return;
    const st = document.createElement('style'); st.id = 'agEstilo'; st.textContent = CSS; document.head.appendChild(st);

    BTN = document.createElement('button');
    BTN.type = 'button'; BTN.className = 'ag-btn'; BTN.title = 'Agente — tire dúvidas sobre os processos';
    BTN.innerHTML = '<span class="ag-orb"></span><span class="ag-txt">Agente</span><span class="ag-n" id="agN">0</span>';
    const direita = document.querySelector('.navbar .nav-right');
    const antes = document.getElementById('userWidget');
    if (direita) direita.insertBefore(BTN, antes && antes.parentNode === direita ? antes : direita.firstChild);
    BTN.addEventListener('click', alternar);

    POP = document.createElement('section');
    POP.className = 'ag-pop'; POP.setAttribute('role', 'dialog'); POP.setAttribute('aria-label', 'Agente do PQI');
    POP.innerHTML = `
      <div class="ag-cab">
        <div class="ag-av"><i></i></div>
        <div class="ag-tit"><b id="agTit">Agente PQI</b><small id="agSub">online · responde com base nos processos</small></div>
        <button type="button" class="ag-ib eng" id="agEng" title="Alimentar o agente" hidden>⚙️</button>
        <button type="button" class="ag-ib" id="agFechar" title="Fechar (Esc)">✕</button>
      </div>
      <div class="ag-chat" id="agChat"></div>
      <form class="ag-pe" id="agForm" autocomplete="off">
        <input id="agIn" maxlength="500" placeholder="Pergunte como fazer algo no PQI…">
        <button class="ag-env" id="agEnv" title="Enviar">➤</button>
      </form>
      <div class="ag-cfg">
        <div class="ag-abas" id="agAbas">
          <button type="button" class="on" data-a="art">📄 Artigos</button>
          <button type="button" data-a="pen">📥 Pendências <span class="c" id="agPc" hidden>0</span></button>
          <button type="button" data-a="his">🕘 Histórico</button>
          <button type="button" data-a="pai">📊 Painel</button>
        </div>
        <div class="ag-cc" id="agCc"></div>
      </div>`;
    document.body.appendChild(POP);
    TOAST = document.createElement('div'); TOAST.className = 'ag-toast'; document.body.appendChild(TOAST);

    POP.querySelector('#agFechar').addEventListener('click', fechar);
    POP.querySelector('#agEng').addEventListener('click', alternarCfg);
    POP.querySelector('#agForm').addEventListener('submit', e => { e.preventDefault(); perguntar(POP.querySelector('#agIn').value); });
    POP.querySelectorAll('#agAbas button').forEach(b => b.addEventListener('click', () => {
      ABA = b.dataset.a; POP.querySelectorAll('#agAbas button').forEach(x => x.classList.toggle('on', x === b)); render();
    }));
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && POP.classList.contains('aberto')) fechar(); });
    document.addEventListener('mousedown', e => {
      if (POP.classList.contains('aberto') && !POP.contains(e.target) && !BTN.contains(e.target) && !POP.classList.contains('modo-cfg')) fechar();
    });
    window.addEventListener('resize', posicionar);
  }

  function posicionar() {
    const nav = document.querySelector('.navbar');
    const base = nav ? nav.getBoundingClientRect().bottom : 72;
    POP.style.top = (base + 8) + 'px';
    POP.style.maxHeight = `calc(100vh - ${base + 20}px)`;
    POP.style.height = POP.classList.contains('modo-cfg') ? `min(760px, calc(100vh - ${base + 20}px))` : `min(620px, calc(100vh - ${base + 20}px))`;
  }

  function alternar() { POP.classList.contains('aberto') ? fechar() : abrir(); }
  function abrir() {
    posicionar(); POP.classList.add('aberto'); BTN.classList.add('aberto');
    if (!POP.querySelector('#agChat').children.length) ola();
    setTimeout(() => { const i = POP.querySelector('#agIn'); if (i && !POP.classList.contains('modo-cfg')) i.focus(); }, 200);
  }
  function fechar() { POP.classList.remove('aberto'); BTN.classList.remove('aberto'); }

  function contador() {
    const n = ME && ME.pode_alimentar ? (ME.pendencias || 0) : 0;
    const el = BTN.querySelector('#agN'); el.textContent = n; el.style.display = n ? 'flex' : 'none';
    const pc = POP.querySelector('#agPc'); pc.textContent = n; pc.hidden = !n;
  }

  async function carregarMe() {
    try { ME = await api('/me'); } catch (_) { return; }
    POP.querySelector('#agEng').hidden = !ME.pode_alimentar;
    if (!ME.pode_alimentar && POP.classList.contains('modo-cfg')) modoChat();
    POP.querySelector('#agSub').textContent = 'online · ' + (ME.ia === 'base' ? 'responde com base nos processos' : 'IA + base de processos');
    contador();
  }

  /* ───────── conversa ───────── */
  const chat = () => POP.querySelector('#agChat');
  function bolha(cls, html) {
    const d = document.createElement('div'); d.className = 'ag-m ' + cls; d.innerHTML = html;
    chat().appendChild(d); chat().scrollTop = chat().scrollHeight; return d;
  }
  function ola() {
    const nome = ME && ME.nome ? ', ' + esc(ME.nome) : '';
    const sug = (ME && ME.sugestoes || []).filter(Boolean);
    const d = bolha('ag-ia', `Olá${nome}! 👋 Sou o <b>Agente do PQI</b>. Pergunte como fazer algo e eu respondo com o passo a passo oficial.` +
      (sug.length ? '<div class="ag-sug">' + sug.map(s => `<button type="button">${esc(s)}</button>`).join('') + '</div>' : ''));
    d.querySelectorAll('.ag-sug button').forEach(b => b.addEventListener('click', () => perguntar(b.textContent)));
  }

  function textoIA(t) {
    // a IA devolve texto simples; numeração "1." vira lista
    const linhas = String(t || '').split('\n').map(x => x.trim()).filter(Boolean);
    const itens = linhas.filter(l => /^\d+[.)]\s/.test(l));
    if (itens.length >= 2 && itens.length >= linhas.length - 2) {
      const antes = linhas.slice(0, linhas.indexOf(itens[0])).map(esc).join('<br>');
      return (antes ? antes + '<br>' : '') + '<ol>' + itens.map(l => '<li>' + esc(l.replace(/^\d+[.)]\s*/, '')) + '</li>').join('') + '</ol>';
    }
    return linhas.map(esc).join('<br>');
  }

  async function perguntar(q) {
    q = String(q || '').trim(); if (!q || ocupado) return;
    ocupado = true; POP.querySelector('#agEnv').disabled = true;
    POP.querySelector('#agIn').value = '';
    bolha('ag-eu', esc(q));
    const d = bolha('ag-ia', '<span class="ag-dig"><span></span><span></span><span></span></span>');
    try {
      const tela = (document.getElementById('badgeName') || {}).textContent || '';
      const r = await api('/perguntar', 'POST', { pergunta: q, tela });
      if (r.tipo === 'resposta') {
        const corpo = r.passos ? '<ol>' + r.passos.map(p => '<li>' + esc(p) + '</li>').join('') + '</ol>' : textoIA(r.texto);
        const f = r.fonte || {};
        d.innerHTML = `<h4>${esc(r.titulo)}</h4>${corpo}
          <div class="ag-src">📄 Fonte: <b>${esc(f.titulo)}</b> · v${esc(f.versao)} · ${esc(diaBR(f.atualizado_em))}${f.por ? ' · ' + esc(f.por) : ''}</div>
          <div class="ag-fb"><button type="button" data-u="1">👍 Resolveu</button><button type="button" data-u="0">👎 Não resolveu</button></div>`;
        ligarRetorno(d, r.pergunta_id);
      } else {
        d.innerHTML = '🤔 <b>Ainda não tenho isso na base do PQI.</b><br>Não vou inventar: sua pergunta foi para quem cuida dos processos e, quando for documentada, eu passo a responder. Enquanto isso, procure seu gestor.';
        if (ME && ME.pode_alimentar) { ME.pendencias = (ME.pendencias || 0) + 1; contador(); }
      }
    } catch (e) {
      d.innerHTML = '⚠️ ' + esc(e.message || 'Não consegui responder agora.') + ' Tente de novo.';
    } finally {
      ocupado = false; POP.querySelector('#agEnv').disabled = false; chat().scrollTop = chat().scrollHeight;
    }
  }

  function ligarRetorno(d, pid) {
    const fb = d.querySelector('.ag-fb');
    fb.querySelector('[data-u="1"]').addEventListener('click', async () => {
      fb.outerHTML = '<div class="ag-src ag-ok">Obrigado pelo retorno ✓</div>';
      try { await api('/retorno', 'POST', { pergunta_id: pid, util: true }); } catch (_) {}
    });
    fb.querySelector('[data-u="0"]').addEventListener('click', () => {
      fb.innerHTML = `<input class="ag-f" placeholder="O que faltou? (opcional)" maxlength="300" style="flex:1;min-width:180px">
        <button type="button">Enviar</button>`;
      const inp = fb.querySelector('input'); inp.focus();
      const enviar = async () => {
        const obs = inp.value.trim();
        fb.outerHTML = '<div class="ag-src ag-av2">Enviado para melhorar este artigo.</div>';
        try { await api('/retorno', 'POST', { pergunta_id: pid, util: false, obs }); } catch (_) {}
        if (ME && ME.pode_alimentar) { ME.pendencias = (ME.pendencias || 0) + 1; contador(); }
      };
      fb.querySelector('button').addEventListener('click', enviar);
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); enviar(); } });
    });
  }

  /* ───────── engrenagem: alimentar ───────── */
  function modoChat() {
    POP.classList.remove('modo-cfg', 'largo'); POP.querySelector('#agEng').classList.remove('on');
    POP.querySelector('#agTit').textContent = 'Agente PQI'; carregarMe(); posicionar();
  }
  function alternarCfg() {
    if (POP.classList.contains('modo-cfg')) return modoChat();
    POP.classList.add('modo-cfg', 'largo'); POP.querySelector('#agEng').classList.add('on');
    POP.querySelector('#agTit').textContent = 'Alimentar o agente';
    POP.querySelector('#agSub').textContent = 'Administrador e Gerente · clique na ⚙️ para voltar à conversa';
    posicionar(); render();
  }
  const cc = () => POP.querySelector('#agCc');
  function carregando() { cc().innerHTML = '<div class="ag-vz"><span class="ag-dig"><span></span><span></span><span></span></span></div>'; }
  function erroCfg(e) { cc().innerHTML = `<div class="ag-vz">⚠️ ${esc(e.message)}</div>`; }
  function irAba(a) { ABA = a; POP.querySelectorAll('#agAbas button').forEach(x => x.classList.toggle('on', x.dataset.a === a)); }
  function render() { ({ art: rArt, pen: rPen, his: rHis, pai: rPai })[ABA](); }

  async function rArt(pre) {
    if (!pre) { carregando(); try { ARTS = (await api('/artigos')).artigos || []; } catch (e) { return erroCfg(e); } }
    const a = pre || ARTS.find(x => x.id === SEL) || null;
    const lista = ARTS.length ? ARTS.map(x => `<div class="ag-art ${x.id === SEL ? 'sel' : ''}" data-id="${esc(x.id)}">
        <div class="t">${esc(x.titulo)}</div>
        <div class="m">${x.setor ? `<span class="ag-tg">${esc(x.setor)}</span>` : ''}${x.ativo === false ? '<span class="ag-tg off">Desativado</span>' : ''}v${esc(x.versao)} · ${esc(diaBR(x.atualizado_em))} · ${esc((x.atualizado_por || {}).nome || '')}${x.usos ? ' · ' + esc(x.usos) + ' usos' : ''}</div></div>`).join('')
      : '<div class="ag-vz">Nenhum artigo ainda. Crie o primeiro →</div>';
    const versoes = a && a.id && (a.versoes || []).length ? `<label>Versões anteriores</label>` + a.versoes.slice(0, 6).map(v =>
      `<div class="ag-dica" style="display:flex;justify-content:space-between;align-items:center;gap:8px">v${esc(v.versao)} · ${esc(dataBR(v.em))}${v.por ? ' · ' + esc(v.por) : ''}
       <button type="button" class="ag-b2" data-v="${esc(v.versao)}" style="padding:3px 9px">Voltar a esta</button></div>`).join('') : '';
    const deP = PEND_ATUAL ? `<div class="ag-dica" style="margin-bottom:6px">📥 Respondendo a pendência: “${esc(PEND_ATUAL.pergunta)}”${PEND_ATUAL.obs ? ' — ' + esc(PEND_ATUAL.obs) : ''}</div>` : '';
    cc().innerHTML = `<div class="ag-g2">
      <div class="ag-cx"><h5>Artigos (${ARTS.length})</h5><button type="button" class="ag-b1" id="agNovo" style="margin-bottom:8px">+ Novo artigo</button>${lista}</div>
      <div class="ag-cx"><h5>${a && a.id ? 'Editar artigo' : 'Novo artigo'}</h5>${deP}
        <label>Título — como as pessoas perguntariam</label><input class="ag-f" id="fT" maxlength="160" value="${esc(a ? a.titulo || '' : '')}" placeholder="Ex.: Como importar a carteira do dia">
        <label>Setor</label><input class="ag-f" id="fS" maxlength="60" value="${esc(a ? a.setor || '' : '')}" placeholder="CX, Compras, Logística, Qualidade…">
        <label>Passo a passo — um passo por linha</label><textarea class="ag-f" id="fP" placeholder="Abra…&#10;Clique em…&#10;Confira…">${esc(a && a.passos ? a.passos.join('\n') : '')}</textarea>
        <div class="ag-dica">O agente responde só com o que estiver aqui. Escreva como explicaria para alguém novo.</div>
        <label>Palavras que as pessoas usam para perguntar isso</label><input class="ag-f" id="fK" value="${esc(a ? a.palavras || '' : '')}" placeholder="importar, planilha, BI, pedidos em aberto…">
        ${a && a.id ? '<label>O que mudou? (vai para o histórico)</label><input class="ag-f" id="fM" maxlength="300" placeholder="Ex.: incluí o caso de Compras sem data">' : ''}
        <label style="display:flex;gap:8px;align-items:center;font-weight:600"><input type="checkbox" id="fA" ${!a || a.ativo !== false ? 'checked' : ''}> Ativo (o agente usa este artigo)</label>
        <div class="ag-bt"><button type="button" class="ag-b1" id="fSalvar">${a && a.id ? 'Salvar nova versão' : 'Salvar artigo'}</button>
          ${a && a.id ? '<button type="button" class="ag-b2" id="fTestar">Testar na conversa</button>' : ''}
          ${PEND_ATUAL ? '<button type="button" class="ag-b2" id="fCancP">Cancelar</button>' : ''}</div>
        <div class="ag-erro" id="fErro"></div>${versoes}</div></div>`;
    cc().querySelectorAll('.ag-art').forEach(el => el.addEventListener('click', () => { SEL = el.dataset.id; PEND_ATUAL = null; rArt(ARTS.find(x => x.id === SEL)); }));
    cc().querySelector('#agNovo').addEventListener('click', () => { SEL = null; PEND_ATUAL = null; rArt({}); });
    const cancP = cc().querySelector('#fCancP'); if (cancP) cancP.addEventListener('click', () => { PEND_ATUAL = null; irAba('pen'); rPen(); });
    const testar = cc().querySelector('#fTestar'); if (testar) testar.addEventListener('click', () => { modoChat(); perguntar(a.titulo); });
    cc().querySelectorAll('[data-v]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm(`Voltar o artigo ao conteúdo da versão ${b.dataset.v}? O conteúdo atual fica guardado como versão anterior.`)) return;
      try { await api(`/artigos/${encodeURIComponent(a.id)}/restaurar`, 'POST', { versao: Number(b.dataset.v) }); toast('Versão restaurada'); rArt(); } catch (e) { toast(e.message); }
    }));
    cc().querySelector('#fSalvar').addEventListener('click', async (ev) => {
      const corpo = {
        titulo: cc().querySelector('#fT').value, setor: cc().querySelector('#fS').value,
        passos: cc().querySelector('#fP').value.split('\n').map(x => x.trim()).filter(Boolean),
        palavras: cc().querySelector('#fK').value, ativo: cc().querySelector('#fA').checked,
        pendencia_id: PEND_ATUAL ? PEND_ATUAL.id : null, motivo: (cc().querySelector('#fM') || {}).value || null,
      };
      ev.target.disabled = true; cc().querySelector('#fErro').textContent = '';
      try {
        const salvo = a && a.id ? await api('/artigos/' + encodeURIComponent(a.id), 'PUT', corpo) : await api('/artigos', 'POST', corpo);
        SEL = salvo.id;
        if (PEND_ATUAL) { PEND_ATUAL = null; await carregarMe(); }
        toast(a && a.id ? 'Nova versão salva — o agente já usa' : 'Artigo criado — o agente já responde');
        rArt();
      } catch (e) { cc().querySelector('#fErro').textContent = e.message; ev.target.disabled = false; }
    });
  }

  async function rPen() {
    carregando();
    let lst; try { lst = (await api('/pendencias')).pendencias || []; } catch (e) { return erroCfg(e); }
    if (ME) { ME.pendencias = lst.length; contador(); }
    cc().innerHTML = `<div class="ag-cx"><h5>Perguntas que o agente não resolveu</h5>
      <div class="ag-dica" style="margin-bottom:10px">Responda e vira artigo. Nada do que as pessoas escrevem entra na base sem passar por aqui.</div>
      ${lst.length ? lst.map(p => `<div class="ag-pd"><div class="q">${esc(p.pergunta)}</div>
        <div class="m">${esc((p.de || {}).nome || '')} · ${esc(dataBR(p.em))} · <span class="ag-w ${p.motivo === 'nao_resolveu' ? 'b' : 'n'}">${p.motivo === 'nao_resolveu' ? '👎 Não resolveu' : 'Sem resposta na base'}</span>${p.obs ? '<br>“' + esc(p.obs) + '”' : ''}</div>
        <div class="ag-bt" style="margin:0"><button type="button" class="ag-b1" data-r="${esc(p.id)}">${p.artigo_id ? 'Corrigir o artigo' : 'Responder → virar artigo'}</button>
        <button type="button" class="ag-b2" data-d="${esc(p.id)}">Descartar</button></div></div>`).join('') : '<div class="ag-vz">Nenhuma pendência 🎉</div>'}</div>`;
    cc().querySelectorAll('[data-r]').forEach(b => b.addEventListener('click', async () => {
      PEND_ATUAL = lst.find(x => x.id === b.dataset.r); irAba('art');
      try { ARTS = (await api('/artigos')).artigos || []; } catch (e) { return erroCfg(e); }
      const alvo = PEND_ATUAL.artigo_id ? ARTS.find(x => x.id === PEND_ATUAL.artigo_id) : null;
      SEL = alvo ? alvo.id : null;
      rArt(alvo || { titulo: PEND_ATUAL.pergunta });
    }));
    cc().querySelectorAll('[data-d]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm('Descartar esta pendência? Ela sai da lista (fica registrada como descartada).')) return;
      try { await api(`/pendencias/${encodeURIComponent(b.dataset.d)}/descartar`, 'POST'); toast('Pendência descartada'); rPen(); } catch (e) { toast(e.message); }
    }));
  }

  async function rHis() {
    carregando();
    let lst; try { lst = (await api('/historico')).historico || []; } catch (e) { return erroCfg(e); }
    cc().innerHTML = `<div class="ag-cx"><h5>Histórico de alterações</h5>
      <div class="ag-dica" style="margin-bottom:8px">Toda mudança fica registrada. Para voltar uma versão, abra o artigo em “Artigos”.</div>
      ${lst.length ? `<table><tr><th>Quando</th><th>Quem</th><th>O quê</th><th>Artigo</th><th>Versão</th></tr>
      ${lst.map(h => `<tr><td>${esc(dataBR(h.em))}</td><td>${esc((h.por || {}).nome || '')}</td><td>${esc(h.acao)}${h.detalhe ? '<div class="ag-dica">' + esc(h.detalhe) + '</div>' : ''}</td><td>${esc(h.titulo)}</td><td>v${esc(h.versao)}</td></tr>`).join('')}</table>`
        : '<div class="ag-vz">Nada registrado ainda.</div>'}</div>`;
  }

  async function rPai() {
    carregando();
    let p; try { p = await api('/painel?dias=30'); } catch (e) { return erroCfg(e); }
    const pc = n => p.perguntas ? Math.round(n / p.perguntas * 100) + '%' : '—';
    const mx = Math.max(1, ...(p.mais_usados || []).map(x => x.n));
    cc().innerHTML = `<div class="ag-k">
        <div><b>${esc(p.perguntas)}</b><span>perguntas em ${esc(p.dias)} dias</span></div>
        <div><b style="color:var(--verde)">${pc(p.respondidas)}</b><span>respondidas pela base</span></div>
        <div><b style="color:var(--gold)">${esc(p.sem_resposta)}</b><span>sem resposta (viraram pendência)</span></div>
        <div><b style="color:var(--vermelho)">${esc(p.nao_resolveu)}</b><span>👎 não resolveu</span></div>
        <div><b>${esc(p.artigos_ativos)}</b><span>artigos ativos · ${p.ia === 'base' ? 'sem IA externa' : 'IA: ' + esc(p.ia)}</span></div></div>
      <div class="ag-g2">
        <div class="ag-cx"><h5>O que mais perguntam</h5>${(p.mais_usados || []).length ? p.mais_usados.map(x => `<div style="margin-bottom:9px"><div style="display:flex;justify-content:space-between;gap:8px"><span>${esc(x.titulo)}</span><b>${esc(x.n)}</b></div><div class="ag-bar"><i style="width:${x.n / mx * 100}%"></i></div></div>`).join('') : '<div class="ag-vz">Sem perguntas no período.</div>'}</div>
        <div class="ag-cx"><h5>Atenção</h5>
          <div class="ag-pd"><div class="q">👎 Artigos com mais “não resolveu”</div><div class="m">${(p.mais_negativos || []).length ? p.mais_negativos.map(x => esc(x.titulo) + ' (' + esc(x.n) + ')').join('<br>') : 'Nenhum 🎉'}</div></div>
          <div class="ag-pd"><div class="q">⏳ Sem revisão há mais de 60 dias</div><div class="m">${(p.sem_revisao || []).length ? p.sem_revisao.map(esc).join('<br>') : 'Nenhum'}</div></div>
        </div></div>`;
  }

  /* ───────── ciclo de vida (chamado pelo index.html) ───────── */
  window.PQIAgente = {
    iniciar() {
      montar(); BTN.style.display = ''; carregarMe();
      clearInterval(TIMER); TIMER = setInterval(() => { if (!document.hidden) carregarMe(); }, 5 * 60 * 1000);
    },
    parar() {
      clearInterval(TIMER); ME = null;
      if (!BTN) return;
      fechar(); POP.classList.remove('modo-cfg', 'largo'); POP.querySelector('#agEng').classList.remove('on');
      POP.querySelector('#agEng').hidden = true; BTN.style.display = 'none';
      POP.querySelector('#agChat').innerHTML = ''; ARTS = []; SEL = null; PEND_ATUAL = null;
    },
  };
})();
