/* ═══════════════════════════════════════════════════════════════════
   pqi-ajuda.js — (i) EXPLICATIVO EM CADA CAMPO DO FLUXO INTERNO (v7.2)
   Carregado pelo pqi-acesso.js só dentro do Fluxo interno (?embed=mapeamento).
   Procura rótulos de campo, cabeçalhos de coluna e botões de seção e põe um (i)
   ao lado, com o que preencher e o que vem depois. Não muda nada nas telas:
   só acrescenta o (i). Para incluir uma explicação, acrescente uma linha em AJUDA.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  if (window.PQI_AJUDA) return;
  var AJUDA = {
    // ── 1 · Organograma ──
    // "aba:" = botões do menu lateral · "/pagina>" = só naquela tela
    "/organograma>aba:organograma": "A estrutura do setor desenhada: quem é quem e a quem responde. <b>Comece aqui</b>: sem pessoas no setor, as atividades não têm quem faz. Próximo passo: <b>2 · Atividades</b>.",
    "aba:por nivel": "As pessoas de cada subsetor arrumadas por nível (A diretoria · B gerência · C supervisão · D operação). Use <b>⇄ Mudar</b> para transformar um subsetor em setor ou levá-lo a outro setor.",
    "aba:lista e edicao": "Todas as pessoas do setor numa tabela, para editar rápido (nome, cargo, nível, subsetor, a quem responde).",
    "aba:importar": "Cole as pessoas de uma planilha (Excel ou Google Sheets) de uma vez: nome, cargo, nível, subsetor, a quem responde…",
    "aba:setor": "Dados do setor: nome, cor, subsetores, níveis. Aqui também o setor inteiro pode virar subsetor de outro setor (⇄ Mudar a estrutura).",
    "/diagnostico>aba:atividades": "Tudo o que o departamento faz, uma linha por atividade, com subsetor e executores. <b>Pronto quando</b> a lista estiver completa. Próximo passo: <b>3 · Atividade → Processo</b>.",
    "/diagnostico>aba:organograma": "O organograma REAL (como funciona na prática), que pode ser diferente do oficial do passo 1. Use para registrar quem de fato faz o quê.",
    "aba:linha de vida": "O resumo do processo: em que fase está, quanto segue a IT e o que falta. Próximo passo: <b>Registrar</b> como é feito hoje.",
    "aba:registrar": "Registre o que foi visto no levantamento: passos reais, passos da IT e anotações.",
    "aba:como e feito hoje": "Os passos reais, na ordem em que acontecem. O fluxograma é montado sozinho a partir daqui (e o contrário também).",
    "aba:fluxograma": "O desenho do processo em raias (quem faz o quê). Fica sempre igual ao Como é feito hoje: mudou um, muda o outro.",
    "aba:it atual": "A Instrução de Trabalho oficial, passo a passo. É a fonte da verdade do Agente.",
    "aba:anotacoes": "Notas do levantamento (entrevistas, observações), com data e fonte.",
    "setor": "O setor do Organograma.",
    "/diagnostico>nome": "Nome do novo departamento. Ele também vira um setor no Organograma (passo 1) — cadastre as pessoas lá.",
    "nome": "Nome completo da pessoa, como todos a conhecem. Deixe vazio para registrar uma <b>vaga em aberto</b>.",
    "cargo": "O cargo oficial (ex.: Analista de Atendimento). Aparece no organograma e ajuda a sugerir o nível.",
    "nivel hierarquico": "A = Diretoria · B = Gerência/Coordenação · C = Supervisão/Liderança · D = Operacional/Analistas. Define a linha em que a pessoa aparece.",
    "subsetor": "A “perna” do setor em que a pessoa trabalha (ex.: SAC dentro de CX). Atividades e processos também podem ser de um subsetor — se ele mudar de setor, vão junto.",
    "responde a": "O gestor direto. Desenha a linha do organograma e diz quem aprova o trabalho da pessoa.",
    "tambem responde a": "Dupla subordinação: outra pessoa a quem também responde (ex.: um coordenador de projeto).",
    "posicionar abaixo de": "Só muda a POSIÇÃO no desenho (ex.: aparece abaixo do coordenador, mas responde ao gerente). Não muda a chefia.",
    "escala": "Dias e turnos de trabalho (ex.: 5x2, 6x1). Usado também pelo Ponto.",
    "horario": "Horário de trabalho (ex.: 08:00 às 17:48 · intervalo 12:00–13:00). O Ponto usa para sugerir a jornada.",
    "e-mail": "E-mail da pessoa. Com ele, pedidos e avisos (ex.: pedido de indicador da Melhoria Contínua) chegam para ela no PQI.",
    "ramal / telefone": "Como falar com a pessoa.",
    "observacoes": "Qualquer informação útil que não cabe nos outros campos.",
    "nome do setor": "O nome do setor como todos conhecem. Ele vira departamento no Diagnóstico e área em Processos.",
    "descricao": "Em uma frase, o que este item faz ou é.",
    // ── 2 · Atividades (Diagnóstico) ──
    "atividade": "O nome da atividade, começando por um verbo. Ex.: <b>Conferir carga na doca</b>, <b>Responder WhatsApp</b>.",
    "categoria": "O tipo de demanda (ex.: operacional, controle, atendimento). Ajuda a agrupar no relatório.",
    "descricao / exemplo": "Um exemplo concreto de quando a atividade acontece. Ajuda quem vai mapear o processo depois.",
    "ja registrada oficialmente?": "Se a atividade já existe em algum sistema/controle oficial (tickets, tags, planilha oficial) ou se é feita “por fora”.",
    "subsetor responsavel": "O subsetor que DEVERIA fazer a atividade. Se o subsetor mudar de setor, a atividade e o processo (com a IT) vão junto.",
    "executores": "Quem faz a atividade hoje (pessoas do Organograma). Pode ser mais de uma. Vira o “Quem faz” do processo.",
    "modo de execucao": "Individual (cada um faz a sua), Dividida (cada um faz uma parte) ou Em equipe (todos ao mesmo tempo).",
    "pessoas (equipe)": "Quantas pessoas trabalham juntas na atividade, quando o modo é Em equipe.",
    // ── 3 · Áreas ──
    "responsavel pelo mapeamento da area": "Quem está mapeando os processos desta área. Quando todos estiverem cadastrados, essa pessoa marca <b>✓ 100% mapeado</b>.",
    "subsetor (opcional)": "Se o processo é de um subsetor (ex.: Qualidade dentro da Frota), escolha aqui. A atividade também é criada nesse subsetor.",
    "criticidade": "Alta = impacta cliente, dinheiro ou lei; Média = impacta o dia a dia; Baixa = interno e contornável. Ajuda a priorizar.",
    "processos": "Um processo por linha (pode colar uma lista). Cada um vira um processo e também uma atividade do setor. Próximo: abra o processo e registre <b>Como é feito hoje</b>.",
    "macroprocesso (opcional)": "Agrupamento maior (ex.: Pós-venda) que junta vários processos.",
    // ── 4 · Processos ──
    "como e feito hoje": "Cada linha é um passo, na ordem em que acontece de verdade (inclusive o que é feito “por fora”). O fluxograma é montado sozinho a partir daqui. Arraste ⠿ para mudar a ordem. Escreva uma pergunta (“…?”) para virar uma decisão.",
    "🔗 nome no fluxograma": "O texto curto que aparece no símbolo do fluxograma. Se ficar vazio, usa o começo da descrição.",
    "nome no fluxograma": "O texto curto que aparece no símbolo do fluxograma. Se ficar vazio, usa o começo da descrição.",
    "quem faz": "Quem executa o passo. Vira a RAIA do fluxograma (mudar aqui muda a raia, e vice-versa).",
    "sistema": "Onde o passo é feito (ERP, planilha, WhatsApp, papel…).",
    "dado gerado": "O que o passo produz ou registra (ex.: nota fiscal, ticket, etiqueta).",
    "tempo (min)": "Minutos que o passo leva, em média. Com “Vezes/mês”, calcula as horas por mês — usado na Melhoria Contínua.",
    "vezes/mes": "Quantas vezes por mês o passo acontece.",
    "corresponde ao passo da it": "A qual passo da Instrução de Trabalho este passo real corresponde. Sem correspondência = “feito sem IT”.",
    "aderencia": "Conforme = feito como a IT manda. Divergente = feito diferente (descreva em “O que difere da IT”). Divergências viram sugestão na Melhoria Contínua.",
    "o que difere da it": "Explique a diferença entre o que se faz e o que a IT manda.",
    "observacao": "Qualquer observação sobre o passo.",
    "nome do processo *": "O nome do processo, começando por um verbo. Ex.: <b>Triar retornos do CD</b>.",
    "nome do processo": "O nome do processo, começando por um verbo. Ex.: <b>Triar retornos do CD</b>.",
    "area": "O setor do Organograma dono do processo.",
    "responsavel(is) — dono do processo": "Quem responde pelo processo (pode ser mais de um). Será a primeira pessoa consultada na Melhoria Contínua.",
    "codigo da it (se tiver)": "O código da Instrução de Trabalho oficial, se já existir em papel ou outro sistema.",
    "objetivo": "Para que o processo existe, em uma frase.",
    "gatilho": "O que faz o processo começar (ex.: chegada do retorno no CD).",
    "entradas": "O que o processo recebe para começar (documentos, materiais, informações).",
    "saidas": "O que o processo entrega no fim.",
    "recebe de (area / processo anterior)": "De quem/que processo vem o trabalho.",
    "entrega para (area / processo seguinte)": "Para quem/que processo o trabalho segue."
  };
  function nrm(s) { return String(s || "").replace(/\s+/g, " ").trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s*\*\s*$/, ""); }
  var CHAVES = Object.keys(AJUDA).map(function (k) { return [nrm(k), AJUDA[k]]; }).sort(function (a, b) { return b[0].length - a[0].length; });
  var MAPA = {}; CHAVES.forEach(function (c) { MAPA[c[0]] = c[1]; });
  function achar(txt, aba) {
    var t = nrm(txt); if (!t || t.length > 60) return null;
    var pg = location.pathname + ">", tipo = aba ? "aba:" : "";
    return MAPA[nrm(pg + tipo + t)] || MAPA[tipo + t] || (aba ? null : MAPA[nrm(pg + t)]) || null;
  }
  // texto "próprio" do elemento (sem o conteúdo de inputs/selects/ícones)
  function textoProprio(el) {
    var s = "";
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) s += n.nodeValue;
      else if (n.nodeType === 1 && /^(SPAN|B|SMALL)$/.test(n.tagName) && !n.querySelector("input,select,textarea")) s += n.textContent;
      if (n.nodeType === 1 && /^(INPUT|SELECT|TEXTAREA|BUTTON|DIV)$/.test(n.tagName)) break;
    }
    return s.replace(/­/g, "");
  }
  var css = document.createElement("style");
  css.textContent = ".pqi-i{display:inline-grid;place-items:center;width:17px;height:17px;margin:0 0 0 5px;border-radius:50%;font:italic 700 11px/1 Georgia,serif;color:#2B63B0;background:rgba(43,99,176,.12);cursor:help;vertical-align:middle;flex:none;border:1px solid transparent}" +
    ".pqi-i:hover,.pqi-i:focus{border-color:#2B63B0;outline:none}" +
    "#pqiDica{position:fixed;z-index:9999;max-width:340px;background:var(--sheet,#fff);color:var(--ink,#1B2436);border:1px solid var(--rule,#D9DEE7);border-left:3px solid #2B63B0;border-radius:8px;padding:10px 12px;font:13px/1.5 'IBM Plex Sans',system-ui,sans-serif;box-shadow:0 10px 30px rgba(10,15,25,.2);text-transform:none;letter-spacing:0;font-weight:400}" +
    "#pqiDica b{color:#2B63B0}";
  (document.head || document.documentElement).appendChild(css);
  var dica = null, de = null;
  function mostrar(b) {
    if (!dica) { dica = document.createElement("div"); dica.id = "pqiDica"; dica.setAttribute("role", "tooltip"); document.body.appendChild(dica); }
    dica.innerHTML = b.getAttribute("data-i"); dica.hidden = false; de = b;
    var r = b.getBoundingClientRect(), w = Math.min(340, innerWidth - 16);
    dica.style.left = Math.max(8, Math.min(r.left - 10, innerWidth - w - 8)) + "px";
    var h = dica.offsetHeight; dica.style.top = (r.bottom + 8 + h < innerHeight ? r.bottom + 8 : Math.max(8, r.top - h - 8)) + "px";
  }
  function esconder() { if (dica) dica.hidden = true; de = null; }
  document.addEventListener("mouseover", function (e) { var b = e.target.closest && e.target.closest(".pqi-i"); if (b) mostrar(b); else if (de) esconder(); });
  document.addEventListener("focusin", function (e) { if (e.target.classList && e.target.classList.contains("pqi-i")) mostrar(e.target); });
  document.addEventListener("click", function (e) { var b = e.target.closest && e.target.closest(".pqi-i"); if (b) { e.preventDefault(); e.stopPropagation(); de === b ? esconder() : mostrar(b); } }, true);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") esconder(); });
  addEventListener("scroll", esconder, true);
  function botao(txt) { var b = document.createElement("span"); b.className = "pqi-i"; b.tabIndex = 0; b.setAttribute("role", "button"); b.setAttribute("aria-label", "Ajuda"); b.setAttribute("data-i", txt); b.textContent = "i"; return b; }
  function marcar() {
    var alvos = document.querySelectorAll("label.f, label, th, .side-btn span:not(.n):not(.fz), .sub-titulo, .bar-tit");
    for (var i = 0; i < alvos.length; i++) {
      var el = alvos[i];
      if (el.hasAttribute("data-pqi-i") || el.closest("#pqiDica")) continue;
      var aba = !!el.closest(".side-btn");
      var txt = textoProprio(el), ajuda = achar(txt, aba);
      el.setAttribute("data-pqi-i", "1");
      if (!ajuda) continue;
      var b = botao(ajuda);
      var campo = el.querySelector("input,select,textarea");
      if (campo && campo.parentNode === el) {   // rótulo + campo: o texto e o (i) ficam juntos numa linha
        var rot = document.createElement("span"); rot.style.cssText = "display:inline-flex;align-items:center;gap:2px";
        while (el.firstChild && el.firstChild !== campo && !(el.firstChild.nodeType === 1 && /^(INPUT|SELECT|TEXTAREA|DIV)$/.test(el.firstChild.tagName))) rot.appendChild(el.firstChild);
        rot.appendChild(b); el.insertBefore(rot, el.firstChild);
      } else el.appendChild(b);
    }
  }
  var pend = false;
  function agendar() { if (pend) return; pend = true; requestAnimationFrame(function () { pend = false; marcar(); }); }
  function iniciar() { marcar(); new MutationObserver(agendar).observe(document.body, { childList: true, subtree: true }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar); else iniciar();
  window.PQI_AJUDA = { AJUDA: AJUDA, achar: achar };
})();
