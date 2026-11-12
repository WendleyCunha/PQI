"""
agente.py — [v6.0] AGENTE IA DO PQI (base de conhecimento)

O agente fica no TOPO do Painel (pqi-agente.js), ao lado de Início / Expandir / Tema.
Qualquer pessoa logada pergunta; só quem ALIMENTA (Administrador e o perfil
Gerente — ver AGENTE_PERFIS_ALIMENTAM) cria e corrige os artigos.

COMO RESPONDE
  1) Procura na base os artigos ativos que tratam do assunto (palavras do título,
     do passo a passo e das "palavras que as pessoas usam").
  2) Sem IA externa (padrão — custo zero): devolve o passo a passo do melhor artigo.
     Com IA externa (AGENTE_IA=groq ou anthropic no Render): a IA escreve a resposta
     usando SÓ os artigos encontrados; se eles não cobrem a pergunta, diz que não sabe.
  3) Não achou nada (ou a pessoa marcou 👎): vira PENDÊNCIA para quem alimenta.
     Nada do que o usuário escreve entra na base sem passar por quem alimenta.

Variáveis de ambiente (todas opcionais):
  AGENTE_IA                 "" (padrão: só a base) · "groq" · "anthropic"
  GROQ_API_KEY / GROQ_MODEL           (Groq tem plano gratuito; modelo padrão openai/gpt-oss-120b)
  ANTHROPIC_API_KEY / ANTHROPIC_MODEL (a mesma chave do resumo do Diagnóstico)
  AGENTE_PERFIS_ALIMENTAM   nomes de perfil, separados por vírgula (padrão: "Gerente")

Coleções no Firestore: agente_artigos · agente_pendencias · agente_perguntas · agente_historico

[v6.1] FONTE ÚNICA = MAPA DIGITAL
  Cada processo do Mapa vira um artigo do agente (id "mapa-<processo>"), montado SÓ do que está
  no Mapa: a IT vigente (passos + quem + onde + regra), as regras gerais e o fluxograma (lido
  símbolo a símbolo, na ordem das setas). Toda gravação no Mapa refaz o artigo na hora — o agente
  nunca tem uma versão diferente do Mapa. Esses artigos NÃO são editados no agente (só no Mapa).

[v6.2] FALTA VIRAR IT (aba só de quem tem a ⚙️)
  Lista o que o agente sabe (ou foi perguntado) e ainda não tem IT no Mapa: artigos manuais e
  perguntas sem resposta. "Criar processo no Mapa" leva o assunto para o Mapa (o artigo vira a nova
  IT já amarrada). Quando a IT do Mapa passa a responder, a pendência fecha sozinha e o artigo
  manual sai de cena — uma coisa só.
"""
import hashlib
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

import conhecimento as cnh
import db_diario as db

router = APIRouter(prefix="/api/agente")
H: dict = {}   # funções do main.py: usuario_atual, nome_perfil

COL_ART = "agente_artigos"
COL_PEN = "agente_pendencias"
COL_PERG = "agente_perguntas"
COL_HIS = "agente_historico"
COL_EX = "agente_exemplos"      # [v6.4] respostas aprovadas (👍) — referência para perguntas parecidas
COL_COR = "agente_correcoes"    # [v7.3] correções (DE → PARA) dos artigos gerados (Organograma, Atividades, Melhoria…)
COL_RESP = "agente_respostas"   # [v7.3] a resposta de cada pergunta: a mesma pergunta recebe a mesma resposta, para todos

MAX_VERSOES = 20          # versões anteriores guardadas dentro de cada artigo (para "voltar versão")
MIN_PONTOS = 3            # pontuação mínima para considerar que um artigo responde a pergunta
_CACHE_SEG = 60


def ligar(app, usuario_atual: Callable, nome_perfil: Callable,
          setores: Optional[Callable] = None, usuarios: Optional[Callable] = None, processos: Optional[Callable] = None,
          criar_processo: Optional[Callable] = None, salvar_processo: Optional[Callable] = None,
          fontes: Optional[Callable] = None) -> None:
    """setores(): setores do Organograma ([{id, dados:{nome, pessoas:[{nome,cargo,email,vaga…}]}}]);
    usuarios(): {email: {nome, ativo}} de quem tem login no PQI."""
    H.update(usuario_atual=usuario_atual, nome_perfil=nome_perfil,
             setores=setores or (lambda: []), usuarios=usuarios or (lambda: {}), processos=processos or (lambda: []),
             criar_processo=criar_processo, salvar_processo=salvar_processo, fontes=fontes or (lambda: {}))
    app.include_router(router)


# ══════════════════════════════════════════════════════════════════════════
# Persistência (trocável nos testes — ver testes_agente.py)
# ══════════════════════════════════════════════════════════════════════════
def _fs_listar(col: str) -> list:
    return [{**(d.to_dict() or {}), "id": d.id} for d in db.get_db().collection(col).stream(timeout=db.TIMEOUT_FS)]


def _fs_ler(col: str, doc_id: str) -> Optional[dict]:
    s = db.get_db().collection(col).document(doc_id).get(timeout=db.TIMEOUT_FS)
    return {**(s.to_dict() or {}), "id": s.id} if s.exists else None


def _fs_gravar(col: str, doc_id: str, dados: dict) -> None:
    dados = {k: v for k, v in dados.items() if k != "id"}
    db.get_db().collection(col).document(doc_id).set(dados, timeout=db.TIMEOUT_FS)


def _fs_desde(col: str, campo: str, desde: str) -> list:
    q = db.get_db().collection(col).where(campo, ">=", desde)
    return [{**(d.to_dict() or {}), "id": d.id} for d in q.stream(timeout=db.TIMEOUT_FS)]


P = {"listar": _fs_listar, "ler": _fs_ler, "gravar": _fs_gravar, "desde": _fs_desde}


# ══════════════════════════════════════════════════════════════════════════
# Utilidades
# ══════════════════════════════════════════════════════════════════════════
def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def novo_id() -> str:
    return uuid.uuid4().hex[:16]


def sem_acento(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


_PARADAS = set("""a o e de da do das dos um uma uns umas como que qual quais quando onde para pra pro com sem
no na nos nas em por eu meu minha meus minhas se ser ao aos os as ou isso esse essa este esta aqui ali
la lá faco faço fazer feito tem ter tenho voce vocês voces sobre mais menos muito pode posso devo preciso
quero sei saber oi ola olá bom dia boa tarde noite obrigado""".split())


def palavras(texto: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9+]+", sem_acento(texto)) if len(w) > 2 and w not in _PARADAS]


def _usuario(request: Request) -> dict:
    return H["usuario_atual"](request)


def _quem(u: dict) -> dict:
    return {"email": u.get("email"), "nome": u.get("nome") or u.get("email")}


def perfis_que_alimentam() -> set:
    bruto = os.environ.get("AGENTE_PERFIS_ALIMENTAM", "Gerente")
    return {sem_acento(x).strip() for x in bruto.split(",") if x.strip()}


def pode_alimentar(u: dict) -> bool:
    if u.get("papel") == "admin":
        return True
    try:
        nome = H["nome_perfil"](u) or ""
    except Exception:
        nome = ""
    return sem_acento(nome).strip() in perfis_que_alimentam()


def _exigir_alimentar(request: Request) -> dict:
    u = _usuario(request)
    if not pode_alimentar(u):
        raise HTTPException(status_code=403, detail="Só o Administrador e o perfil Gerente alimentam o agente.")
    return u


# ══════════════════════════════════════════════════════════════════════════
# Base de artigos (com cache curto: o agente é chamado a cada pergunta)
# ══════════════════════════════════════════════════════════════════════════
_cache = {"t": 0.0, "artigos": [], "salvos": [], "gerados": []}


def esquecer_cache() -> None:
    _cache["t"] = 0.0


def _gerados() -> list:
    """[v7.3] TUDO o que está escrito no Fluxo interno e na Melhoria Contínua vira artigo (conhecimento.py),
    já com as correções de quem cuida do agente."""
    try:
        gerados = cnh.gerar(H.get("fontes", lambda: {})() or {})
    except Exception as e:   # noqa: BLE001 — uma fonte fora do ar não derruba o agente: fica o que já tinha
        print(f"[agente] não consegui ler as fontes: {type(e).__name__}: {e}")
        return _cache.get("gerados") or []
    corr: dict = {}
    for c in P["listar"](COL_COR):
        if c.get("ativo", True):
            corr.setdefault(c.get("artigo_id"), []).append(c)
    for lst in corr.values():
        lst.sort(key=lambda c: c.get("em") or "")
    return [cnh.aplicar_correcoes(a, corr.get(a["id"], [])) for a in gerados]


def artigos() -> list:
    """Artigos escritos à mão + IT de cada processo (mapa-) + tudo o que é gerado das fontes."""
    if time.time() - _cache["t"] > _CACHE_SEG:
        salvos = P["listar"](COL_ART)
        gerados = _gerados()
        _cache.update(t=time.time(), salvos=salvos, gerados=gerados, artigos=salvos + gerados)
    return _cache["artigos"]


def artigos_salvos() -> list:
    artigos()
    return _cache["salvos"]


def artigo(aid: str) -> Optional[dict]:
    """Um artigo salvo (lido do banco) ou gerado (do cache)."""
    return P["ler"](COL_ART, aid) or next((a for a in artigos() if a.get("id") == aid and a.get("gerado")), None)


def _radical(w: str) -> str:
    """Radical simples: "libero", "liberar", "liberação", "liberaram" → "liber" (plural, verbo, substantivo)."""
    return w[:5] if len(w) >= 5 else w


def pontuar(pergunta: str, art: dict) -> int:
    """Quantas palavras da pergunta aparecem no artigo (título e palavras-chave valem mais).
    [v6.3.2] Variações da mesma palavra contam em todo o texto (título, palavras-chave E passos)."""
    alvo_forte = set(palavras(art.get("titulo", "") + " " + art.get("palavras", "")))
    alvo_fraco = set(palavras(" ".join(art.get("passos") or [])))
    rad_forte = {_radical(x) for x in alvo_forte if len(x) >= 5}
    rad_fraco = {_radical(x) for x in alvo_fraco if len(x) >= 5}
    pts = 0
    for w in set(palavras(pergunta)):
        if w in alvo_forte or (len(w) >= 5 and _radical(w) in rad_forte):
            pts += 2
        elif w in alvo_fraco or (len(w) >= 5 and _radical(w) in rad_fraco):
            pts += 1
    return pts


MIN_PONTOS_IA = 2   # com IA ligada a busca pode ser menos exigente: se não tiver a ver, a IA responde "não sei"


def buscar(pergunta: str, limite: int = 5) -> list:
    ativos = [a for a in artigos() if a.get("ativo", True)]
    ws = set(palavras(pergunta))
    no_titulo = lambda a: len(ws & set(palavras(a.get("titulo", ""))))   # empate: quem tem a pergunta no título vem antes
    notas = sorted(((pontuar(pergunta, a), a) for a in ativos), key=lambda x: (-x[0], -no_titulo(x[1])))
    minimo = MIN_PONTOS_IA if provedor() else MIN_PONTOS
    minimo = max(1, min(minimo, 2 * len(ws)))   # [v7.3] pergunta de uma palavra só ("o que é agrupamento?"): basta achá-la no título
    return [a for n, a in notas if n >= minimo][:limite]


def fonte(a: dict) -> dict:
    return {"id": a.get("id"), "titulo": a.get("titulo"), "versao": a.get("versao", 1),
            "atualizado_em": a.get("atualizado_em"), "por": (a.get("atualizado_por") or {}).get("nome")}


# ══════════════════════════════════════════════════════════════════════════
# IA externa (opcional)
# ══════════════════════════════════════════════════════════════════════════
INSTRUCOES = (   # [v6.4] mais humano: um colega experiente, com as próprias palavras
    "Você é o Agente do PQI, um colega experiente da King Star Colchões que ajuda as pessoas no dia a dia. "
    "Converse em português do Brasil de um jeito natural e humano, com as SUAS palavras — não copie o texto dos artigos. "
    "Vá direto ao ponto do que foi perguntado e explique o porquê quando isso ajudar a pessoa a fazer certo. "
    "Use passos numerados só quando for um procedimento com várias etapas; para dúvidas simples, responda em uma ou duas frases. "
    "Você pode juntar informações de artigos diferentes e tirar conclusões lógicas a partir deles, como um colega que conhece bem o processo. "
    "Mas NUNCA invente nome de tela, botão, campo, menu, opção, prazo, valor ou regra que não esteja escrito nos artigos: "
    "se o artigo não diz o nome exato, descreva o que fazer sem inventar um nome. "
    "As CORREÇÕES escritas num artigo valem mais que o resto do texto dele. "
    "Os artigos vêm de TUDO o que está escrito no sistema: Organograma (setores, gestor no topo, pessoas, quem responde a quem), "
    "Atividades (o que é, quem executa), Processos (IT, como é feito hoje, TO BE, ficha completa) e Melhoria Contínua (melhorias e indicadores). "
    "Cruze as informações entre eles para responder (ex.: quem é o gestor de um setor = quem está no topo do organograma daquele setor; "
    "o que uma pessoa faz = as atividades em que ela é executora e os processos em que é responsável). "
    "Se houver CONVERSA ANTERIOR, continue o mesmo assunto. EXEMPLOS APROVADOS são respostas que a equipe validou: use como referência de conteúdo e de jeito de responder. "
    "Se os artigos não permitem responder, responda exatamente: NAO_SEI. "
    "Na última linha, escreva FONTE: seguido do número do artigo que você mais usou (ex.: FONTE: 2)."
)


ULTIMO_ERRO_IA: dict = {}   # o último motivo de a IA não ter respondido (aparece no teste da ⚙️, sem a chave)


def _http_json(url: str, corpo: dict, cab: dict, timeout: int = 40) -> dict:
    # User-Agent próprio: a Groq (Cloudflare) recusa chamadas que se identificam como "Python-urllib"
    req = urllib.request.Request(url, data=json.dumps(corpo).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "PQI-Agente/1.0 (+https://pqi-c2tu.onrender.com)", **cab})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def provedor() -> str:
    p = os.environ.get("AGENTE_IA", "").strip().lower()
    if p == "groq" and os.environ.get("GROQ_API_KEY"):
        return "groq"
    if p == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return ""


LIMITE_CORPO_IA = 3500   # caracteres de cada artigo que vão para a IA (cabe no plano gratuito da Groq)


def _corpo(a: dict) -> str:
    out, tam = [], 0
    for p in a.get("passos") or []:
        t = f"- {p}"
        if tam + len(t) > LIMITE_CORPO_IA:
            out.append("- …"); break
        out.append(t); tam += len(t)
    return "\n".join(out)


def perguntar_ia(pergunta: str, achados: list, historico: Optional[list] = None, exemplos: Optional[list] = None) -> Optional[str]:
    """Texto da IA, "NAO_SEI", ou None se a IA está desligada/falhou (aí vale a resposta da base)."""
    prov = provedor()
    if not prov:
        return None
    base = "\n\n".join(f"ARTIGO {i + 1}: {a.get('titulo')}\n" + _corpo(a) for i, a in enumerate(achados))
    usuario = f"ARTIGOS:\n{base}\n\n"
    if exemplos:
        usuario += "EXEMPLOS APROVADOS:\n" + "\n\n".join(f"Pergunta: {e.get('pergunta')}\nResposta aprovada: {e.get('texto')}" for e in exemplos) + "\n\n"
    if historico:
        usuario += "CONVERSA ANTERIOR:\n" + "\n".join(f"Pessoa: {h.get('p')}\nAgente: {h.get('r')}" for h in historico) + "\n\n"
    usuario += f"PERGUNTA: {pergunta}"
    try:
        if prov == "groq":
            modelo = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
            corpo = {"model": modelo, "temperature": 0.4, "max_tokens": 700,
                     "messages": [{"role": "system", "content": INSTRUCOES}, {"role": "user", "content": usuario}]}
            if "gpt-oss" in modelo:   # modelo que "pensa" antes: raciocínio curto e espaço para a resposta não sair vazia
                corpo.update(max_tokens=2500, reasoning_effort="low", include_reasoning=False)
            r = _http_json("https://api.groq.com/openai/v1/chat/completions", corpo,
                           {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]})
            return (r.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or None
        r = _http_json("https://api.anthropic.com/v1/messages",
                       {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 700, "system": INSTRUCOES,
                        "messages": [{"role": "user", "content": usuario}]},
                       {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip() or None
    except urllib.error.HTTPError as e:
        try:
            corpo_erro = e.read().decode("utf-8", "replace")[:400]
        except Exception:   # noqa: BLE001
            corpo_erro = ""
        ULTIMO_ERRO_IA.clear(); ULTIMO_ERRO_IA.update(em=agora(), provedor=prov, status=e.code, detalhe=corpo_erro)
        print(f"[agente] IA ({prov}) recusou: HTTP {e.code} {corpo_erro}")
        return None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
        ULTIMO_ERRO_IA.clear(); ULTIMO_ERRO_IA.update(em=agora(), provedor=prov, status=None, detalhe=f"{type(e).__name__}: {e}"[:400])
        print(f"[agente] IA ({prov}) falhou: {type(e).__name__}: {e}")
        return None


def ia_texto(sistema: str, usuario: str, max_tokens: int = 1200) -> Optional[str]:
    """[v7.2] Pedido livre à IA configurada (Melhoria: sugerir indicador). None = IA desligada ou falhou."""
    prov = provedor()
    if not prov:
        return None
    try:
        if prov == "groq":
            modelo = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
            corpo = {"model": modelo, "temperature": 0.3, "max_tokens": max_tokens,
                     "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]}
            if "gpt-oss" in modelo:
                corpo.update(max_tokens=max_tokens + 2500, reasoning_effort="low", include_reasoning=False)
            r = _http_json("https://api.groq.com/openai/v1/chat/completions", corpo,
                           {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]}, timeout=60)
            return (r.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or None
        r = _http_json("https://api.anthropic.com/v1/messages",
                       {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": max_tokens, "system": sistema,
                        "messages": [{"role": "user", "content": usuario}]},
                       {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"}, timeout=60)
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip() or None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
        print(f"[agente] IA ({prov}) falhou no pedido livre: {type(e).__name__}: {e}")
        return None


def _separar_fonte(texto: str, achados: list):
    m = re.search(r"\n?\s*FONTE:\s*(\d+)\s*$", texto)
    usado = achados[0]
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < len(achados):
            usado = achados[i]
        texto = texto[:m.start()].rstrip()
    return texto, usado


# ══════════════════════════════════════════════════════════════════════════
# Rotas — qualquer pessoa logada
# ══════════════════════════════════════════════════════════════════════════
class Pergunta(BaseModel):
    pergunta: str
    tela: Optional[str] = None     # sistema aberto no momento (só para o painel)
    historico: List[dict] = []     # [v6.4] últimas trocas da conversa: [{"p": pergunta, "r": resposta}]

    @field_validator("historico")
    @classmethod
    def _h(cls, v):
        out = []
        for h in (v or [])[-3:]:
            if isinstance(h, dict) and str(h.get("p") or "").strip():
                out.append({"p": str(h.get("p"))[:300], "r": str(h.get("r") or "")[:800]})
        return out

    @field_validator("pergunta")
    @classmethod
    def _limpa(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Escreva a pergunta.")
        return v[:500]


class Retorno(BaseModel):
    pergunta_id: str
    util: bool
    obs: Optional[str] = None


def _nova_pendencia(u: dict, pergunta: str, motivo: str, obs: str = "", artigo_id: Optional[str] = None,
                    pergunta_id: Optional[str] = None) -> None:
    pid = novo_id()
    P["gravar"](COL_PEN, pid, {"pergunta": pergunta, "motivo": motivo, "obs": (obs or "")[:500], "artigo_id": artigo_id,
                               "pergunta_id": pergunta_id, "status": "aberta", "em": agora(), **{"de": _quem(u)}})


def _abertas() -> list:
    return [p for p in P["listar"](COL_PEN) if p.get("status") == "aberta"]


def _email(x) -> str:
    return str(x or "").strip().lower()


def fase(p: dict) -> str:
    """sem_responsavel → com_responsavel → aguardando_aprovacao (quem alimenta publica ou devolve)."""
    if p.get("proposta") and p["proposta"].get("status") == "enviada":
        return "aguardando_aprovacao"
    if (p.get("atribuida") or {}).get("email"):
        return "com_responsavel"
    return "sem_responsavel"


def _comigo(u: dict) -> list:
    eu = _email(u.get("email"))
    return [p for p in _abertas() if _email((p.get("atribuida") or {}).get("email")) == eu and fase(p) == "com_responsavel"]


def _com_fase(p: dict) -> dict:
    return {**p, "fase": fase(p)}


# ══════════════════════════════════════════════════════════════════════════
# [v6.1] Artigos que vêm do MAPA DIGITAL (fonte única)
# ══════════════════════════════════════════════════════════════════════════
PREFIXO_MAPA = "mapa-"
_SIMB_NOME = {"inicio": "Início", "fim": "Fim", "decisao": "Decisão", "subprocesso": "Subprocesso", "espera": "Espera",
              "entrada": "Entrada de dados", "documento": "Documento", "sistema": "Sistema", "manual": "Operação manual"}
_PALAVRAS_FRACAS = set("para pelo pela pelos pelas como com sem que quando onde este esta isso essa esse uma umas uns dos das nos nas "
                       "ser sao tem mais menos todo toda todos todas cada sobre entre depois antes ainda pode deve".split())


def _txt(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def fluxo_em_texto(F: dict, limite: int = 1800) -> List[str]:
    """O fluxograma lido em ordem: cada símbolo com a raia (quem faz) e para onde vai cada seta."""
    nos = {n.get("id"): n for n in (F or {}).get("nos") or [] if isinstance(n, dict)}
    raias = {r.get("id"): _txt(r.get("nome")) for r in (F or {}).get("raias") or [] if isinstance(r, dict)}
    saidas: dict = {}
    entra = set()
    for l in (F or {}).get("ligacoes") or []:
        if l.get("de") in nos and l.get("para") in nos:
            saidas.setdefault(l["de"], []).append(l)
            entra.add(l["para"])
    nome = lambda n: _txt(n.get("texto")) or _SIMB_NOME.get(n.get("tipo"), "Etapa")
    ordem, vistos = [], set()
    inicios = [n for n in nos.values() if n.get("tipo") == "inicio"] + [n for n in nos.values() if n["id"] not in entra and n.get("tipo") != "inicio"]
    fila = sorted(inicios, key=lambda n: n.get("x", 0)) + sorted(nos.values(), key=lambda n: n.get("x", 0))
    def visitar(n):
        pilha = [n]
        while pilha:
            a = pilha.pop(0)
            if a["id"] in vistos:
                continue
            vistos.add(a["id"]); ordem.append(a)
            pilha[0:0] = [nos[l["para"]] for l in sorted(saidas.get(a["id"], []), key=lambda l: nos[l["para"]].get("x", 0)) if l["para"] not in vistos]
    for n in fila:
        visitar(n)
    linhas, tam = [], 0
    for n in ordem:
        if n.get("tipo") in ("nota", "conector"):
            continue
        quem = raias.get(n.get("raia"), "")
        segue = [(_txt(l.get("rotulo")) + ": " if _txt(l.get("rotulo")) else "") + nome(nos[l["para"]]) for l in saidas.get(n["id"], [])]
        t = (f"[{quem}] " if quem else "") + (nome(n) + "?" if n.get("tipo") == "decisao" and not nome(n).endswith("?") else nome(n))
        if segue:
            t += " → " + " | ".join(segue)
        if tam + len(t) > limite:
            linhas.append("…"); break
        linhas.append(t); tam += len(t)
    return linhas


def it_vigente(d: dict):
    """[v7.1] FONTE DA VERDADE = FLUXO INTERNO, na ordem do que VALE hoje:
    1) nova IT (TO BE) APROVADA · 2) IT oficial · 3) nova IT ainda em validação (só se não houver IT oficial)
    · 4) como é feito hoje (sem IT). A situação vai no cabeçalho do artigo, para a resposta deixar claro."""
    tb = d.get("tobe") or {}
    it = d.get("it") or {}
    tem_tobe = any(_txt(x.get("descricao")) for x in tb.get("passos") or [])
    aprov = _txt((d.get("validacao_tobe") or {}).get("parecer")).lower().startswith("aprov")
    tobe = ("tobe", tb.get("passos") or [], _txt(tb.get("codigo")) or _txt(it.get("codigo")), _txt(tb.get("versao")),
            "aprovada" if aprov else "nova IT ainda em validação", tb.get("regras") or "", tb.get("fluxo") or "", "tobe")
    if tem_tobe and aprov:
        return tobe
    if any(_txt(x.get("descricao")) for x in d.get("passos_it") or []):
        return ("it", d.get("passos_it") or [], _txt(it.get("codigo")), _txt(it.get("versao")), "oficial", "", it.get("obs") or "", "asis")
    if tem_tobe:
        return tobe
    return ("real", d.get("passos_real") or [], "", "", "como é feito hoje — ainda sem IT oficial", "", "", "asis")


def artigo_do_processo(reg: dict) -> Optional[dict]:
    """Artigo do agente montado só com o que está no Mapa. None = processo sem conteúdo."""
    d = (reg or {}).get("dados") or {}
    if not isinstance(d, dict) or d.get("_tipo") or not _txt(d.get("nome")):
        return None
    qual, passos, cod_it, ver, situacao, regras, excecoes, ver_fluxo = it_vigente(d)
    passos = [x for x in passos if isinstance(x, dict) and _txt(x.get("descricao"))]
    if not passos:
        return None
    cab = (f"Fonte oficial: Fluxo interno · {_txt(d.get('codigo'))} · " + (f"{cod_it} " if cod_it else "")
           + (f"rev. {ver} " if ver else "") + f"({situacao}). Este passo a passo é atualizado sozinho a cada alteração no Fluxo interno.")
    out = [cab]
    # [v7.1] de onde é e quem responde (Organograma): setor, subsetor e responsáveis
    org = (d.get("origem") or {})
    onde = " · ".join(t for t in (("Setor: " + _txt(d.get("area"))) if _txt(d.get("area")) else "",
                                  ("Subsetor: " + _txt(org.get("subsetor"))) if _txt(org.get("subsetor")) else "",
                                  ("Responsável: " + _txt(d.get("dono"))) if _txt(d.get("dono")) else "") if t)
    if onde:
        out.append(onde + ".")
    corr = [c for c in (d.get("ajustes_agente") or []) if isinstance(c, dict) and _txt(c.get("para"))]
    if corr:
        out.append("CORREÇÕES (valem mais que o resto do texto): " + " ".join(
            f"Onde se dizia “{_txt(c.get('de'))}”, o correto é: {_txt(c.get('para'))}." for c in corr))
    for i, x in enumerate(passos, 1):
        extra = " · ".join(t for t in (("Quem faz: " + _txt(x.get("quem"))) if _txt(x.get("quem")) else "",
                                        ("Onde: " + _txt(x.get("sistema"))) if _txt(x.get("sistema")) else "",
                                        ("Regra: " + _txt(x.get("regra"))) if _txt(x.get("regra")) else "") if t)
        out.append(f"{i}. {_txt(x.get('descricao'))}" + (f" ({extra})" if extra else ""))
    regras_l = [_txt(r) for r in str(regras).splitlines() if _txt(r)]
    if regras_l:
        out.append("Regras gerais: " + " ".join(regras_l))
    if _txt(excecoes):
        out.append("Exceções: " + _txt(excecoes))
    F = ((d.get("fluxo") or {}).get(ver_fluxo) or {})
    fl = fluxo_em_texto(F)
    if fl:
        out.append("Fluxograma (" + ("como será" if ver_fluxo == "tobe" else "como é hoje") + "): " + " ⇢ ".join(fl))
    texto_todo = " ".join([_txt(d.get("nome")), _txt(d.get("objetivo")), _txt((d.get("tobe") or {}).get("objetivo"))] + out)
    freq: dict = {}
    for w in palavras(texto_todo):
        if len(w) >= 4 and w not in _PALAVRAS_FRACAS:
            freq[w] = freq.get(w, 0) + 1
    chave = " ".join(sorted(freq, key=lambda w: -freq[w])[:40])
    titulo = _txt(d.get("nome")) + (f" — {cod_it}" if cod_it else "") + f" ({_txt(d.get('codigo'))})"
    return {"titulo": titulo[:160], "setor": _txt(d.get("area")), "passos": out,
            "palavras": " ".join(filter(None, [_txt(d.get("nome")), _txt(d.get("codigo")), cod_it, _txt(d.get("macroprocesso")), chave])),
            "ativo": True}


def sincronizar_processo(reg: dict, quem: str = "Processos") -> Optional[dict]:
    """Refaz o artigo do processo. Chamado a cada gravação no Mapa (nunca derruba a gravação)."""
    pid = (reg or {}).get("id")
    if not pid:
        return None
    aid = PREFIXO_MAPA + pid
    novo = artigo_do_processo(reg)
    art = P["ler"](COL_ART, aid)
    if not novo:
        if art and art.get("ativo"):
            art.update(ativo=False, atualizado_em=agora())
            P["gravar"](COL_ART, aid, art); esquecer_cache()
        return art
    por = {"nome": quem, "email": ""}
    origem = {"tipo": "mapa", "processo_id": pid, "codigo": _txt(((reg.get("dados") or {}).get("codigo"))), "versao_mapa": reg.get("versao")}
    if not art:
        art = {"id": aid, **novo, "versao": 1, "usos": 0, "criado_em": agora(), "criado_por": por,
               "atualizado_em": agora(), "atualizado_por": por, "versoes": [], "origem": origem}
    elif _conteudo(art) != novo:
        anterior = {**_conteudo(art), "versao": art.get("versao", 1), "em": art.get("atualizado_em"), "por": art.get("atualizado_por")}
        art.update(novo, versao=int(art.get("versao", 1)) + 1, atualizado_em=agora(), atualizado_por=por, origem=origem,
                   versoes=([anterior] + list(art.get("versoes") or []))[:MAX_VERSOES])
    else:
        art["origem"] = origem
        P["gravar"](COL_ART, aid, art)
        return art
    P["gravar"](COL_ART, aid, art)
    esquecer_cache()
    _aposentar_manual(reg, art)
    fechar_pendencias_respondidas_pela_it()
    return art


def _aposentar_manual(reg: dict, art_mapa: dict) -> None:
    """O artigo manual que virou este processo sai de cena: quem responde agora é a IT do Mapa."""
    oa = (((reg or {}).get("dados") or {}).get("origem_agente") or {}).get("artigo_id")
    ligados = [a["id"] for a in P["listar"](COL_ART) if a.get("virou_processo") == (reg or {}).get("id") and a.get("ativo", True)]
    for x in ligados:
        if x != oa:
            _aposentar_um(x, art_mapa)
    if not oa or oa == art_mapa.get("id"):
        return
    _aposentar_um(oa, art_mapa)


def _aposentar_um(oa: str, art_mapa: dict) -> None:
    man = P["ler"](COL_ART, oa)
    if man and man.get("ativo", True) and not str(oa).startswith(PREFIXO_MAPA):
        man.update(ativo=False, atualizado_em=agora(), substituido_por=art_mapa.get("id"))
        P["gravar"](COL_ART, oa, man)
        P["gravar"](COL_HIS, novo_id(), {"em": agora(), "por": {"nome": "Processos", "email": ""}, "acao": "desativou", "artigo_id": oa,
                                         "titulo": man.get("titulo"), "versao": man.get("versao", 1),
                                         "detalhe": f"virou IT no Mapa ({(art_mapa.get('origem') or {}).get('codigo')}) — o agente responde por ela"})
        esquecer_cache()


def _it_que_responde(pergunta: str) -> Optional[dict]:
    achados = buscar(pergunta, 1)
    return achados[0] if achados and str(achados[0].get("id", "")).startswith(PREFIXO_MAPA) else None


def fechar_pendencias_respondidas_pela_it() -> int:
    """Pergunta que estava sem resposta e que agora a IT do Mapa responde: a pendência fecha sozinha.
    (👎 numa resposta continua nas Pendências: é ajuste na IT, não falta de IT.)"""
    n = 0
    for p in _abertas():
        if p.get("motivo") != "sem_resposta":
            continue
        a = _it_que_responde(p.get("pergunta", ""))
        if a:
            p.update(status="resolvida", resolvida_em=agora(), resolvida_por={"nome": "Processos", "email": ""}, artigo_id=a["id"],
                     nota=f"respondida pela IT do Mapa ({(a.get('origem') or {}).get('codigo') or a.get('titulo')})")
            P["gravar"](COL_PEN, p["id"], p)
            n += 1
    return n


def _chave_assunto(t: str) -> str:
    return " ".join(sorted(w for w in set(palavras(t)) if len(w) >= 4 and w not in _PALAVRAS_FRACAS)) or sem_acento(t).strip().lower()


def falta_virar_it() -> dict:
    """O que o agente sabe ou foi perguntado e ainda não tem IT no Mapa."""
    procs = {r.get("id"): r for r in (H.get("processos", lambda: [])() or [])}
    artigos_man = [a for a in artigos_salvos() if a.get("ativo", True) and not str(a.get("id", "")).startswith(PREFIXO_MAPA)]
    grupos: dict = {}
    for p in _abertas():
        if p.get("motivo") != "sem_resposta":
            continue
        g = grupos.setdefault(_chave_assunto(p.get("pergunta", "")), {"pergunta": p.get("pergunta"), "vezes": 0, "ultima": "", "ids": [],
                                                                          "processo": None, "de": []})
        g["vezes"] += 1; g["ids"].append(p["id"])
        if (p.get("em") or "") >= g["ultima"]:
            g["ultima"] = p.get("em") or ""; g["pergunta"] = p.get("pergunta")
        nome = (p.get("de") or {}).get("nome")
        if nome and nome not in g["de"]:
            g["de"].append(nome)
        pid = p.get("processo_id")
        if pid and pid in procs:
            d = procs[pid].get("dados") or {}
            g["processo"] = {"id": pid, "codigo": d.get("codigo"), "nome": d.get("nome")}
    def ja_no_mapa(a):
        pid = a.get("virou_processo")
        if pid and pid in procs:
            d = procs[pid].get("dados") or {}
            return {"id": pid, "codigo": d.get("codigo"), "nome": d.get("nome")}
        return None
    lista_procs = sorted(({"id": i, "codigo": (r.get("dados") or {}).get("codigo"), "nome": (r.get("dados") or {}).get("nome"),
                           "area": (r.get("dados") or {}).get("area"),
                           "tem_it": any(_txt(x.get("descricao")) for x in ((r.get("dados") or {}).get("tobe") or {}).get("passos") or [])}
                          for i, r in procs.items() if (r.get("dados") or {}).get("codigo")), key=lambda x: str(x["codigo"]))
    return {"processos": lista_procs,
            "artigos": [{"id": a["id"], "titulo": a.get("titulo"), "setor": a.get("setor"), "passos": len(a.get("passos") or []),
                         "resposta": list(a.get("passos") or []),   # o que o agente responde hoje — conferir antes de importar
                         "usos": a.get("usos") or 0, "atualizado_em": a.get("atualizado_em"), "processo": ja_no_mapa(a)} for a in artigos_man],
            "perguntas": sorted(sorted(grupos.values(), key=lambda g: g["ultima"], reverse=True), key=lambda g: -g["vezes"])}


def _ligar_a_processo(payload: "CriarProcesso", u: dict) -> dict:
    procs = {r.get("id"): r for r in (H.get("processos", lambda: [])() or [])}
    reg = procs.get(payload.processo_id)
    if not reg:
        raise HTTPException(status_code=404, detail="Processo não encontrado no Mapa.")
    d = reg.get("dados") or {}
    art = P["ler"](COL_ART, payload.artigo_id) if payload.artigo_id else None
    tb = d.setdefault("tobe", {})
    vazia = not any(_txt(x.get("descricao")) for x in tb.get("passos") or [])
    levou = 0
    if art and H.get("salvar_processo"):   # os passos do artigo viram a IT (vazia) ou entram no fim dela — já amarrados
        novos = [{"id": novo_id(), "descricao": _txt(t), "rotulo": "", "quem": "", "sistema": "", "regra": "", "mudanca": "Novo"}
                 for t in (art.get("passos") or []) if _txt(t)]
        tb["passos"] = novos if vazia else list(tb.get("passos") or []) + novos
        levou = len(novos)
        am = d.setdefault("amarracao", {})
        if not (am.get("tobe") or {}).get("ativo") and not ((d.get("fluxo") or {}).get("tobe") or {}).get("nos"):
            am["tobe"] = {"ativo": True, "espelho": None}
        d["origem_agente"] = {"artigo_id": art["id"], "pendencias": payload.pendencias, "por": _quem(u), "em": agora()}
        res, novo = H["salvar_processo"](reg["id"], d, None, u.get("nome") or u.get("email") or "")
        if res == "ok" and novo:
            reg = novo
    if art:
        art["virou_processo"] = reg["id"]
        P["gravar"](COL_ART, art["id"], art)
    for pen_id in payload.pendencias:
        pen = P["ler"](COL_PEN, pen_id)
        if pen and pen.get("status") == "aberta":
            pen["processo_id"] = reg["id"]
            P["gravar"](COL_PEN, pen_id, pen)
    _historico(u, "ligou ao Mapa", {"id": (art or {}).get("id") or "", "titulo": d.get("nome"), "versao": 1},
               f"{d.get('codigo')} · " + ((f"{levou} passo(s) do artigo viraram a IT" if vazia else f"{levou} passo(s) do artigo acrescentados no fim da IT")
                                         if levou else "assunto ligado ao processo"))
    sincronizar_processo(reg, "Processos")
    esquecer_cache()
    return {"processo": {"id": reg["id"], "codigo": d.get("codigo"), "nome": d.get("nome")}, "passos": levou, "acrescentou": levou > 0 and not vazia}


def total_falta_it() -> int:
    try:
        f = falta_virar_it()
        return len(f["artigos"]) + len(f["perguntas"])
    except Exception:   # noqa: BLE001
        return 0


class CriarProcesso(BaseModel):
    nome: str = ""
    area: Optional[str] = ""
    artigo_id: Optional[str] = None
    pendencias: List[str] = []
    processo_id: Optional[str] = None   # ligar a um processo que já existe no Mapa (em vez de criar outro)

    @field_validator("nome")
    @classmethod
    def _n(cls, v):
        return (v or "").strip()[:120]


@router.get("/falta-it")
def agente_falta_it(request: Request):
    _exigir_alimentar(request)
    fechou = fechar_pendencias_respondidas_pela_it()
    return {**falta_virar_it(), "fechadas_agora": fechou}


@router.post("/falta-it/criar-processo")
def agente_criar_processo(payload: CriarProcesso, request: Request):
    """Leva o assunto para o Mapa: processo novo, com a IT (TO BE) já amarrada ao fluxograma."""
    u = _exigir_alimentar(request)
    if payload.processo_id:
        return _ligar_a_processo(payload, u)
    if not payload.nome:
        raise HTTPException(status_code=400, detail="Dê um nome ao processo.")
    if not H.get("criar_processo"):
        raise HTTPException(status_code=501, detail="Criar processo pelo agente não está ligado neste servidor.")
    procs = H.get("processos", lambda: [])() or []
    n = max([int(m.group(1)) for r in procs for m in [re.search(r"(\d+)$", str((r.get("dados") or {}).get("codigo") or ""))] if m] + [0])
    codigo = f"PROC-{n + 1:03d}"
    art = P["ler"](COL_ART, payload.artigo_id) if payload.artigo_id else None
    if art and str(art.get("id", "")).startswith(PREFIXO_MAPA):
        raise HTTPException(status_code=400, detail="Este artigo já é a IT de um processo do Mapa.")
    passos = [{"id": novo_id(), "descricao": _txt(t), "rotulo": "", "quem": "", "sistema": "", "regra": "", "mudanca": "Novo"}
              for t in ((art or {}).get("passos") or []) if _txt(t)]
    dados = {"codigo": codigo, "nome": payload.nome, "area": _txt(payload.area) or _txt((art or {}).get("setor")),
             "criticidade": "Média", "status": "Em levantamento",
             "tobe": {"codigo": "", "versao": "", "objetivo": "", "regras": "", "fluxo": "", "passos": passos, "pendencias": []},
             "amarracao": {"tobe": {"ativo": True, "espelho": None}, "asis": {"ativo": True, "espelho": None}},
             "origem_agente": {"artigo_id": (art or {}).get("id"), "pendencias": payload.pendencias, "por": _quem(u), "em": agora()}}
    reg = H["criar_processo"](dados, u.get("nome") or u.get("email") or "", u.get("email"))
    pid = reg.get("id")
    if art:
        art["virou_processo"] = pid
        P["gravar"](COL_ART, art["id"], art)
    for pen_id in payload.pendencias:
        pen = P["ler"](COL_PEN, pen_id)
        if pen and pen.get("status") == "aberta":
            pen["processo_id"] = pid
            P["gravar"](COL_PEN, pen_id, pen)
    _historico(u, "levou para o Mapa", {"id": (art or {}).get("id") or "", "titulo": payload.nome, "versao": 1},
               f"{codigo} criado em Processos" + (" com os passos do artigo" if passos else ""))
    sincronizar_processo(reg, "Processos")
    esquecer_cache()
    return {"processo": {"id": pid, "codigo": codigo, "nome": payload.nome}, "passos": len(passos)}


def ultimo_artigo_txt(aid: str) -> str:
    a = P["ler"](COL_ART, aid) or {}
    return " ".join(a.get("passos") or [])


def remover_processo(pid: str) -> None:
    art = P["ler"](COL_ART, PREFIXO_MAPA + pid)
    if art and art.get("ativo"):
        art.update(ativo=False, atualizado_em=agora())
        P["gravar"](COL_ART, art["id"], art); esquecer_cache()


def _bloquear_se_mapa(art: dict) -> None:
    if (art.get("origem") or {}).get("tipo") == "mapa" or str(art.get("id", "")).startswith(PREFIXO_MAPA):
        raise HTTPException(status_code=409, detail=f"Este artigo vem de Processos ({(art.get('origem') or {}).get('codigo') or 'processo'}). "
                                                    "Altere em Processos — o agente atualiza sozinho.")


@router.post("/sincronizar-mapa")
def agente_sincronizar_mapa(request: Request):
    """Refaz todos os artigos do Mapa (usado uma vez ao ligar a v6.1, ou para conferir)."""
    _exigir_alimentar(request)
    lst = H.get("processos", lambda: [])() or []
    feitos = [a for a in (sincronizar_processo(r) for r in lst) if a]
    vivos = {r.get("id") for r in lst}
    for a in P["listar"](COL_ART):
        if str(a.get("id", "")).startswith(PREFIXO_MAPA) and a["id"][len(PREFIXO_MAPA):] not in vivos:
            remover_processo(a["id"][len(PREFIXO_MAPA):])
    return {"artigos": [{"id": a["id"], "titulo": a.get("titulo"), "ativo": a.get("ativo"), "versao": a.get("versao")} for a in feitos]}


# ══════════════════════════════════════════════════════════════════════════
# [v6.4] ✏️ AJUSTAR DE → PARA (quem tem a ⚙️)
#   O texto errado está na fonte (IT do Mapa / artigo)? troca lá mesmo — IT, fluxograma e agente atualizam juntos.
#   Não está (a IA tirou de outro jeito)? vira CORREÇÃO oficial da fonte: aparece no Mapa e a IA obedece.
# ══════════════════════════════════════════════════════════════════════════
class Ajuste(BaseModel):
    artigo_id: str
    de: str
    para: str
    pergunta_id: Optional[str] = None

    @field_validator("de", "para")
    @classmethod
    def _dp(cls, v):
        v = re.sub(r"\s+", " ", (v or "")).strip()
        if not v:
            raise ValueError("Preencha o DE (o que está errado) e o PARA (o correto).")
        return v[:1200]


def _trocar(texto: str, de: str, para: str):
    """Troca a 1ª ocorrência de `de` em `texto` (sem ligar para maiúsculas e espaços). → (novo_texto, trocou?)"""
    padrao = r"\s+".join(re.escape(w) for w in de.split())
    novo, n = re.subn(padrao, lambda _m: para, str(texto or ""), count=1, flags=re.IGNORECASE)
    return novo, bool(n)


def _ajustar_processo(art: dict, de: str, para: str, u: dict) -> dict:
    pid = (art.get("origem") or {}).get("processo_id") or str(art["id"])[len(PREFIXO_MAPA):]
    reg = next((r for r in (H.get("processos", lambda: [])() or []) if r.get("id") == pid), None)
    if not reg or not H.get("salvar_processo"):
        raise HTTPException(status_code=404, detail="Processo do Mapa não encontrado para ajustar.")
    d = json.loads(json.dumps(reg.get("dados") or {}))
    qual, passos, cod_it, *_ = it_vigente(d)
    onde = None
    for i, x in enumerate(passos):
        for campo in ("descricao", "regra"):
            novo, ok_ = _trocar(x.get(campo), de, para)
            if ok_:
                x[campo] = novo; onde = f"passo {i + 1} da IT" + (" (regra do passo)" if campo == "regra" else ""); break
        if onde:
            break
    if not onde:
        alvo = d.setdefault("tobe", {}) if qual == "tobe" else d.setdefault("it", {})
        for campo, nome in (("regras", "regras gerais"), ("fluxo" if qual == "tobe" else "obs", "exceções")):
            novo, ok_ = _trocar(alvo.get(campo), de, para)
            if ok_:
                alvo[campo] = novo; onde = nome + " da IT"; break
    modo = "texto"
    if not onde:   # a IA não copiou o texto da IT: fica como correção oficial do processo
        d.setdefault("ajustes_agente", []).append({"id": novo_id(), "de": de, "para": para, "por": _quem(u), "em": agora()})
        onde, modo = "correções da IT", "correcao"
    res, novo_reg = H["salvar_processo"](pid, d, None, f"Agente · {u.get('nome') or u.get('email')}")
    if res != "ok":
        raise HTTPException(status_code=409, detail="Não consegui gravar no Mapa agora. Tente de novo.")
    sincronizar_processo(novo_reg or {**reg, "dados": d}, f"Agente · {u.get('nome') or u.get('email')}")
    return {"modo": modo, "onde": f"{onde} · {(d.get('codigo') or '')}".strip(" ·")}


def _ajustar_artigo(art: dict, de: str, para: str, u: dict) -> dict:
    passos = list(art.get("passos") or [])
    onde, modo = None, "texto"
    for i, t in enumerate(passos):
        novo, ok_ = _trocar(t, de, para)
        if ok_:
            passos[i] = novo; onde = f"passo {i + 1} do artigo"; break
    if not onde:
        passos.append(f"Correção: onde se dizia “{de}”, o correto é: {para}")
        onde, modo = "correção no fim do artigo", "correcao"
    anterior = {**_conteudo(art), "versao": art.get("versao", 1), "em": art.get("atualizado_em"), "por": art.get("atualizado_por")}
    art.update(passos=passos, versao=int(art.get("versao", 1)) + 1, atualizado_em=agora(), atualizado_por=_quem(u),
               versoes=([anterior] + list(art.get("versoes") or []))[:MAX_VERSOES])
    P["gravar"](COL_ART, art["id"], art)
    esquecer_cache()
    return {"modo": modo, "onde": onde}


_ONDE_FONTE = {"organograma": "Organograma", "atividade": "Atividades", "diagnostico": "Diagnóstico",
               "melhoria": "Melhoria Contínua", "indicador": "Melhoria Contínua · indicador"}


def _ajustar_gerado(art: dict, de: str, para: str, u: dict) -> dict:
    """Artigo gerado das fontes (Organograma, Atividades, Melhoria…): a correção fica guardada no agente e entra
    no topo do artigo toda vez que ele é refeito — a IA obedece a correção acima do resto."""
    cid = novo_id()
    P["gravar"](COL_COR, cid, {"id": cid, "artigo_id": art["id"], "titulo": art.get("titulo"), "de": de, "para": para,
                               "por": _quem(u), "em": agora(), "ativo": True, "origem": art.get("origem") or {}})
    onde = _ONDE_FONTE.get((art.get("origem") or {}).get("tipo"), "fonte")
    return {"modo": "correcao", "onde": f"correção oficial do agente sobre {onde} · {art.get('titulo')}"}


def _corrigir_resposta(pergunta_id: str, de: str, para: str, u: dict) -> None:
    """A resposta dada àquela pergunta passa a ser a corrigida (DE → PARA) — e vale para quem perguntar a mesma coisa."""
    perg = P["ler"](COL_PERG, pergunta_id)
    if not perg:
        return
    ch = perg.get("chave") or chave_pergunta(perg.get("pergunta", ""))
    texto = perg.get("texto") or ""
    if not ch or not texto:
        return
    novo, ok_ = _trocar(texto, de, para)
    if not ok_:
        novo = texto.rstrip() + f"\n\nCorreção: onde se dizia “{de}”, o correto é: {para}"
    esquecer_cache()
    antes = P["ler"](COL_RESP, ch) or {}
    ids = list((antes.get("versoes") or {}).keys()) or [perg.get("artigo_id")]
    vers = {i: v for i, v in _versoes_atuais([i for i in ids if i]).items() if v is not None}
    if not vers:
        return
    fonte_ = antes.get("fonte") or {"id": perg.get("artigo_id")}
    if fonte_.get("id") in vers:
        fonte_ = {**fonte_, "versao": vers[fonte_["id"]]}
    P["gravar"](COL_RESP, ch, {"id": ch, "pergunta": perg.get("pergunta"), "texto": novo, "titulo": antes.get("titulo"), "fonte": fonte_,
                               "versoes": vers, "ruim": False, "aprovada": True, "ajustada_por": _quem(u), "em": agora(),
                               "de": antes.get("de") or perg.get("de")})


@router.post("/ajustar")
def agente_ajustar(payload: Ajuste, request: Request):
    u = _exigir_alimentar(request)
    if payload.de.lower() == payload.para.lower():
        raise HTTPException(status_code=400, detail="O DE e o PARA estão iguais.")
    art = artigo(payload.artigo_id)
    if not art:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    eh_mapa = (art.get("origem") or {}).get("tipo") in ("mapa", "processo_ficha") or str(art.get("id", "")).startswith(PREFIXO_MAPA)
    if eh_mapa:
        r = _ajustar_processo(art, payload.de, payload.para, u)
    elif art.get("gerado"):
        r = _ajustar_gerado(art, payload.de, payload.para, u)
    else:
        r = _ajustar_artigo(art, payload.de, payload.para, u)
    esquecer_cache()
    _historico(u, "ajustou", {"id": art["id"], "titulo": art.get("titulo"), "versao": art.get("versao", 1)},
               f"DE “{payload.de[:120]}” → PARA “{payload.para[:120]}” ({r['onde']})")
    if payload.pergunta_id:
        _corrigir_resposta(payload.pergunta_id, payload.de, payload.para, u)
        for p in _abertas():
            if p.get("pergunta_id") == payload.pergunta_id:
                p.update(status="resolvida", resolvida_em=agora(), resolvida_por=_quem(u), nota=f"ajustado: {r['onde']}")
                P["gravar"](COL_PEN, p["id"], p)
    return r


@router.post("/ia-teste")
def agente_ia_teste(request: Request):
    """Diagnóstico da IA (só quem tem a ⚙️): chama a IA com uma pergunta de teste e mostra o motivo se falhar."""
    _exigir_alimentar(request)
    prov = provedor()
    if not prov:
        return {"ok": False, "provedor": "", "motivo": "IA desligada: falta AGENTE_IA (groq/anthropic) e a chave no Render."}
    t = perguntar_ia("Responda só: OK", [{"titulo": "Teste", "passos": ["Responda OK."]}])
    return {"ok": bool(t), "provedor": prov, "modelo": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b") if prov == "groq" else os.environ.get("ANTHROPIC_MODEL", ""),
            "resposta": (t or "")[:200], "erro": None if t else dict(ULTIMO_ERRO_IA)}


@router.get("/me")
def agente_me(request: Request):
    u = _usuario(request)
    pode = pode_alimentar(u)
    return {"pode_alimentar": pode, "pendencias": len(_abertas()) if pode else 0, "comigo": len(_comigo(u)),
            "falta_it": total_falta_it() if pode else 0,
            "ia": provedor() or "base", "nome": (u.get("nome") or u.get("email") or "").split(" ")[0],
            "sugestoes": [a.get("titulo") for a in sorted((a for a in artigos_salvos() if a.get("ativo", True)),
                                                           key=lambda a: -(a.get("usos") or 0))[:4]]}


def exemplos_para(pergunta: str, achados: list, limite: int = 2) -> list:
    """[v6.4] Respostas aprovadas (👍) de perguntas parecidas, dos mesmos artigos e da MESMA versão deles.
    Se o artigo (a IT) mudou depois, o exemplo deixa de valer sozinho."""
    vers = {a.get("id"): a.get("versao", 1) for a in achados}
    alvo = set(palavras(pergunta))
    cands = []
    for e in P["listar"](COL_EX):
        if e.get("artigo_id") in vers and e.get("artigo_versao") == vers[e.get("artigo_id")]:
            comuns = len(alvo & set(palavras(e.get("pergunta", ""))))
            if comuns:
                cands.append((comuns, e))
    cands.sort(key=lambda x: -x[0])
    return [e for _, e in cands[:limite]]


def chave_pergunta(t: str) -> str:
    """A mesma pergunta escrita de outro jeito ("Quem é o gestor da Logística?" / "quem e gestor logistica") → mesma chave."""
    ws = sorted(set(palavras(t)))
    return "q-" + hashlib.sha1(" ".join(ws).encode("utf-8")).hexdigest()[:20] if ws else ""


def _versoes_atuais(ids) -> dict:
    por_id = {a.get("id"): a for a in artigos()}
    return {i: (por_id[i].get("versao", 1) if i in por_id and por_id[i].get("ativo", True) else None) for i in ids}


def resposta_guardada(pergunta: str) -> Optional[dict]:
    """[v7.3] Alguém já perguntou isso e a resposta não foi reprovada (👎), e nada mudou nas fontes desde então:
    vale a MESMA resposta para todo mundo. Mudou a fonte (ou alguém ajustou)? A resposta é refeita."""
    ch = chave_pergunta(pergunta)
    r = P["ler"](COL_RESP, ch) if ch else None
    if not r or r.get("ruim") or not r.get("texto"):
        return None
    vers = r.get("versoes") or {}
    if not vers or _versoes_atuais(vers.keys()) != vers:
        return None
    return r


def guardar_resposta(pergunta: str, texto: str, usados: list, fonte_: dict, titulo: str, u: dict, aprovada: bool = False) -> None:
    ch = chave_pergunta(pergunta)
    if not ch:
        return
    P["gravar"](COL_RESP, ch, {"id": ch, "pergunta": pergunta, "texto": texto, "titulo": titulo, "fonte": fonte_,
                               "versoes": {a.get("id"): a.get("versao", 1) for a in usados}, "ruim": False, "aprovada": aprovada,
                               "em": agora(), "de": _quem(u)})


@router.post("/perguntar")
def agente_perguntar(payload: Pergunta, request: Request):
    u = _usuario(request)
    if not payload.historico and provedor():
        g = resposta_guardada(payload.pergunta)
        if g:
            pid = novo_id()
            resp = {"pergunta_id": pid, "tipo": "resposta", "modo": "ia", "titulo": g.get("titulo"), "texto": g["texto"],
                    "fonte": g.get("fonte"), "mesma_resposta": True, "aprovada": bool(g.get("aprovada"))}
            P["gravar"](COL_PERG, pid, {"pergunta": payload.pergunta, "em": agora(), "de": _quem(u), "tela": payload.tela,
                                        "resultado": "resposta", "artigo_id": (g.get("fonte") or {}).get("id"),
                                        "artigo_versao": (g.get("fonte") or {}).get("versao"), "texto": g["texto"][:2000],
                                        "modo": "ia", "util": None, "chave": g.get("id")})
            return resp
    achados = buscar(payload.pergunta)
    if not achados and payload.historico:   # [v6.4] continuação ("e se o cliente não atender?"): busca com o assunto da conversa
        achados = buscar(payload.historico[-1]["p"] + " " + payload.pergunta)
    pid = novo_id()
    resp: dict = {"pergunta_id": pid}
    if achados:
        texto = perguntar_ia(payload.pergunta, achados, payload.historico, exemplos_para(payload.pergunta, achados) if provedor() else None)
        if texto and texto.strip().upper().startswith("NAO_SEI"):
            achados = []
        elif texto:
            texto, usado = _separar_fonte(texto, achados)
            resp.update(tipo="resposta", modo="ia", titulo=usado.get("titulo"), texto=texto, fonte=fonte(usado))
            if not payload.historico:
                guardar_resposta(payload.pergunta, texto, achados, fonte(usado), usado.get("titulo"), u)
        if achados and "tipo" not in resp:
            a = achados[0]
            resp.update(tipo="resposta", modo="base", titulo=a.get("titulo"), passos=a.get("passos") or [], fonte=fonte(a))
    if not achados:
        resp.update(tipo="nao_sei")
        _nova_pendencia(u, payload.pergunta, "sem_resposta", pergunta_id=pid)
    P["gravar"](COL_PERG, pid, {"pergunta": payload.pergunta, "em": agora(), "de": _quem(u), "tela": payload.tela,
                                "resultado": resp["tipo"], "artigo_id": (resp.get("fonte") or {}).get("id"),
                                "artigo_versao": (resp.get("fonte") or {}).get("versao"),
                                "texto": (resp.get("texto") or "")[:2000] if resp.get("modo") == "ia" else "",
                                "modo": resp.get("modo"), "util": None,
                                "chave": chave_pergunta(payload.pergunta) if resp.get("modo") == "ia" and not payload.historico else ""})
    if resp.get("fonte"):   # contador de uso (ordena as sugestões)
        art = next((a for a in artigos() if a.get("id") == resp["fonte"]["id"] and not a.get("gerado")), None)
        if art:
            art["usos"] = int(art.get("usos") or 0) + 1
            P["gravar"](COL_ART, art["id"], art)
    return resp


@router.post("/retorno")
def agente_retorno(payload: Retorno, request: Request):
    u = _usuario(request)
    perg = P["ler"](COL_PERG, payload.pergunta_id)
    if not perg or (perg.get("de") or {}).get("email") != u.get("email"):
        raise HTTPException(status_code=404, detail="Pergunta não encontrada.")
    if perg.get("util") is not None:
        return {"ok": True}
    perg["util"] = payload.util
    perg["obs"] = (payload.obs or "")[:500]
    P["gravar"](COL_PERG, payload.pergunta_id, perg)
    if payload.util and perg.get("modo") == "ia" and perg.get("texto") and perg.get("artigo_id"):   # [v6.4] 👍 vira exemplo aprovado
        P["gravar"](COL_EX, payload.pergunta_id, {"id": payload.pergunta_id, "pergunta": perg.get("pergunta"), "texto": perg.get("texto"),
                                                  "artigo_id": perg.get("artigo_id"), "artigo_versao": perg.get("artigo_versao"),
                                                  "por": _quem(u), "em": agora()})
    r = P["ler"](COL_RESP, perg["chave"]) if perg.get("chave") else None
    if r and (r.get("texto") or "")[:2000] == (perg.get("texto") or "")[:2000]:   # [v7.3] a resposta que vale para todos
        if payload.util:
            r.update(aprovada=True, aprovada_por=_quem(u), aprovada_em=agora())
        else:   # 👎: não vale mais para ninguém — a próxima pergunta igual é respondida de novo, e vai para as Pendências
            r.update(ruim=True, reprovada_por=_quem(u), reprovada_em=agora(), obs=(payload.obs or "")[:500])
        P["gravar"](COL_RESP, r["id"], r)
    if not payload.util:
        _nova_pendencia(u, perg.get("pergunta", ""), "nao_resolveu", payload.obs or "", perg.get("artigo_id"), payload.pergunta_id)
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════
# Rotas — quem alimenta (Administrador e perfil Gerente)
# ══════════════════════════════════════════════════════════════════════════
class Artigo(BaseModel):
    titulo: str
    setor: Optional[str] = ""
    passos: List[str]
    palavras: Optional[str] = ""
    ativo: bool = True
    pendencia_id: Optional[str] = None   # veio de uma pendência: ela é dada como resolvida
    motivo: Optional[str] = None         # o que mudou (vai para o histórico)

    @field_validator("titulo")
    @classmethod
    def _t(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Dê um título ao artigo.")
        return v[:160]

    @field_validator("passos")
    @classmethod
    def _p(cls, v):
        v = [str(x).strip()[:600] for x in (v or []) if str(x).strip()]
        if not v:
            raise ValueError("Escreva pelo menos um passo.")
        return v[:30]


class Restaurar(BaseModel):
    versao: int


def _historico(u: dict, acao: str, art: dict, detalhe: str = "") -> None:
    P["gravar"](COL_HIS, novo_id(), {"em": agora(), "por": _quem(u), "acao": acao, "artigo_id": art.get("id"),
                                     "titulo": art.get("titulo"), "versao": art.get("versao", 1), "detalhe": detalhe[:300]})


def _fechar_pendencia(pid: Optional[str], u: dict, artigo_id: str) -> str:
    """Resolve a pendência; devolve o crédito ("resposta de Fulano") quando veio de um responsável."""
    if not pid:
        return ""
    p = P["ler"](COL_PEN, pid)
    if not p or p.get("status") != "aberta":
        return ""
    credito = ""
    if fase(p) == "aguardando_aprovacao":
        p["proposta"].update(status="aprovada", aprovada_em=agora(), aprovada_por=_quem(u))
        credito = f"resposta de {(p['proposta'].get('por') or {}).get('nome', '')}, aprovada"
    p.update(status="resolvida", resolvida_em=agora(), resolvida_por=_quem(u), artigo_id=artigo_id)
    P["gravar"](COL_PEN, pid, p)
    return credito


def _conteudo(a: dict) -> dict:
    return {k: a.get(k) for k in ("titulo", "setor", "passos", "palavras", "ativo")}


@router.get("/artigos")
def agente_artigos(request: Request):
    _exigir_alimentar(request)
    lst = sorted(P["listar"](COL_ART), key=lambda a: a.get("atualizado_em") or "", reverse=True)
    return {"artigos": [{**a, "versoes": [{"versao": v.get("versao"), "em": v.get("em"), "por": (v.get("por") or {}).get("nome")}
                                          for v in (a.get("versoes") or [])]} for a in lst]}


@router.post("/artigos")
def agente_artigo_criar(payload: Artigo, request: Request):
    u = _exigir_alimentar(request)
    aid = novo_id()
    art = {"id": aid, **payload.model_dump(exclude={"pendencia_id", "motivo"}), "versao": 1, "usos": 0,
           "criado_em": agora(), "criado_por": _quem(u), "atualizado_em": agora(), "atualizado_por": _quem(u), "versoes": []}
    P["gravar"](COL_ART, aid, art)
    credito = _fechar_pendencia(payload.pendencia_id, u, aid)
    _historico(u, "criou", art, payload.motivo or credito or ("respondeu uma pendência" if payload.pendencia_id else ""))
    esquecer_cache()
    return art


@router.put("/artigos/{aid}")
def agente_artigo_editar(aid: str, payload: Artigo, request: Request):
    u = _exigir_alimentar(request)
    art = P["ler"](COL_ART, aid)
    if not art:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    _bloquear_se_mapa(art)
    novo = payload.model_dump(exclude={"pendencia_id", "motivo"})
    if novo == _conteudo(art):
        return art
    anterior = {**_conteudo(art), "versao": art.get("versao", 1), "em": art.get("atualizado_em"), "por": art.get("atualizado_por")}
    art.update(novo, versao=int(art.get("versao", 1)) + 1, atualizado_em=agora(), atualizado_por=_quem(u),
               versoes=([anterior] + list(art.get("versoes") or []))[:MAX_VERSOES])
    P["gravar"](COL_ART, aid, art)
    acao = "desativou" if anterior["ativo"] and not art["ativo"] else ("reativou" if not anterior["ativo"] and art["ativo"] else "editou")
    credito = _fechar_pendencia(payload.pendencia_id, u, aid)
    _historico(u, acao, art, " · ".join(x for x in (payload.motivo or "", credito) if x))
    esquecer_cache()
    return art


@router.post("/artigos/{aid}/restaurar")
def agente_artigo_restaurar(aid: str, payload: Restaurar, request: Request):
    u = _exigir_alimentar(request)
    art = P["ler"](COL_ART, aid)
    if not art:
        raise HTTPException(status_code=404, detail="Artigo não encontrado.")
    _bloquear_se_mapa(art)
    alvo = next((v for v in (art.get("versoes") or []) if int(v.get("versao", 0)) == payload.versao), None)
    if not alvo:
        raise HTTPException(status_code=404, detail="Essa versão não está mais guardada.")
    anterior = {**_conteudo(art), "versao": art.get("versao", 1), "em": art.get("atualizado_em"), "por": art.get("atualizado_por")}
    art.update(_conteudo(alvo), versao=int(art.get("versao", 1)) + 1, atualizado_em=agora(), atualizado_por=_quem(u),
               versoes=([anterior] + list(art.get("versoes") or []))[:MAX_VERSOES])
    P["gravar"](COL_ART, aid, art)
    _historico(u, "restaurou", art, f"voltou ao conteúdo da versão {payload.versao}")
    esquecer_cache()
    return art


@router.get("/pendencias")
def agente_pendencias(request: Request):
    _exigir_alimentar(request)
    return {"pendencias": [_com_fase(p) for p in sorted(_abertas(), key=lambda p: p.get("em") or "", reverse=True)]}


# ── Atribuir a uma pessoa do ORGANOGRAMA: o responsável do setor escreve a resposta ──
class Atribuir(BaseModel):
    email: str
    nota: Optional[str] = None


class Proposta(BaseModel):
    titulo: str
    passos: List[str]
    palavras: Optional[str] = ""

    @field_validator("titulo")
    @classmethod
    def _t(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Dê um título à resposta.")
        return v[:160]

    @field_validator("passos")
    @classmethod
    def _p(cls, v):
        v = [str(x).strip()[:600] for x in (v or []) if str(x).strip()]
        if not v:
            raise ValueError("Escreva pelo menos um passo.")
        return v[:30]


class Devolver(BaseModel):
    motivo: str


def responsaveis() -> list:
    """Pessoas do Organograma (sem as vagas), com o setor e se têm login no PQI pelo e-mail."""
    contas = {_email(k): v for k, v in (H["usuarios"]() or {}).items()}
    saida = []
    for s in H["setores"]() or []:
        d = s.get("dados") or {}
        for p in d.get("pessoas") or []:
            if p.get("vaga") or not str(p.get("nome") or "").strip():
                continue
            em = _email(p.get("email"))
            conta = contas.get(em) if em else None
            saida.append({"setor_id": s.get("id"), "setor": d.get("nome") or "", "subsetor": p.get("subsetor") or "",
                          "nome": p.get("nome"), "cargo": p.get("cargo") or "", "nivel": p.get("nivel") or "",
                          "email": em, "tem_login": bool(conta and conta.get("ativo", True))})
    return sorted(saida, key=lambda x: (x["setor"].lower(), x["nivel"] or "Z", x["nome"].lower()))


@router.get("/responsaveis")
def agente_responsaveis(request: Request):
    _exigir_alimentar(request)
    return {"pessoas": responsaveis()}


@router.post("/pendencias/{pid}/atribuir")
def agente_pendencia_atribuir(pid: str, payload: Atribuir, request: Request):
    u = _exigir_alimentar(request)
    p = P["ler"](COL_PEN, pid)
    if not p or p.get("status") != "aberta":
        raise HTTPException(status_code=404, detail="Pendência não encontrada ou já fechada.")
    alvo = next((x for x in responsaveis() if x["email"] and x["email"] == _email(payload.email)), None)
    if not alvo:
        raise HTTPException(status_code=400, detail="Essa pessoa não está no Organograma com este e-mail.")
    if not alvo["tem_login"]:
        raise HTTPException(status_code=400, detail=f"{alvo['nome']} não tem login ativo no PQI com o e-mail do Organograma.")
    p["atribuida"] = {k: alvo[k] for k in ("email", "nome", "cargo", "setor", "setor_id")}
    p["atribuida"].update(em=agora(), por=_quem(u), nota=(payload.nota or "")[:300])
    if p.get("proposta"):
        p["proposta"]["status"] = "substituida"
    P["gravar"](COL_PEN, pid, p)
    return _com_fase(p)


@router.get("/comigo")
def agente_comigo(request: Request):
    u = _usuario(request)
    nomes = {a.get("id"): a for a in artigos()}
    lst = sorted(_comigo(u), key=lambda p: (p.get("atribuida") or {}).get("em") or "", reverse=True)
    return {"pendencias": [{**_com_fase(p), "artigo": ({k: nomes[p["artigo_id"]].get(k) for k in ("titulo", "passos", "palavras")}
                                                        if p.get("artigo_id") in nomes else None)} for p in lst]}


@router.post("/pendencias/{pid}/proposta")
def agente_pendencia_proposta(pid: str, payload: Proposta, request: Request):
    """O responsável escreve a resposta; ela só vira artigo depois que quem alimenta aprova."""
    u = _usuario(request)
    p = P["ler"](COL_PEN, pid)
    if not p or p.get("status") != "aberta":
        raise HTTPException(status_code=404, detail="Pendência não encontrada ou já fechada.")
    if _email((p.get("atribuida") or {}).get("email")) != _email(u.get("email")):
        raise HTTPException(status_code=403, detail="Esta pendência não está com você.")
    p["proposta"] = {**payload.model_dump(), "status": "enviada", "em": agora(), "por": _quem(u)}
    P["gravar"](COL_PEN, pid, p)
    return _com_fase(p)


@router.post("/pendencias/{pid}/devolver")
def agente_pendencia_devolver(pid: str, payload: Devolver, request: Request):
    u = _exigir_alimentar(request)
    p = P["ler"](COL_PEN, pid)
    if not p or fase(p) != "aguardando_aprovacao":
        raise HTTPException(status_code=404, detail="Não há resposta aguardando aprovação nesta pendência.")
    p["proposta"].update(status="devolvida", devolvida_em=agora(), devolvida_por=_quem(u), motivo_devolucao=(payload.motivo or "")[:300])
    P["gravar"](COL_PEN, pid, p)
    return _com_fase(p)


@router.post("/pendencias/{pid}/descartar")
def agente_pendencia_descartar(pid: str, request: Request):
    u = _exigir_alimentar(request)
    p = P["ler"](COL_PEN, pid)
    if not p:
        raise HTTPException(status_code=404, detail="Pendência não encontrada.")
    p.update(status="descartada", resolvida_em=agora(), resolvida_por=_quem(u))
    P["gravar"](COL_PEN, pid, p)
    return {"ok": True}


@router.get("/historico")
def agente_historico(request: Request):
    _exigir_alimentar(request)
    return {"historico": sorted(P["listar"](COL_HIS), key=lambda h: h.get("em") or "", reverse=True)[:200]}


@router.get("/painel")
def agente_painel(request: Request, dias: int = 30):
    _exigir_alimentar(request)
    dias = max(1, min(int(dias or 30), 180))
    desde = (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")
    pergs = P["desde"](COL_PERG, "em", desde)
    total = len(pergs)
    respondidas = sum(1 for p in pergs if p.get("resultado") == "resposta")
    nomes = {a.get("id"): a.get("titulo") for a in artigos()}
    usos: dict = {}
    negativos: dict = {}
    for p in pergs:
        aid = p.get("artigo_id")
        if aid:
            usos[aid] = usos.get(aid, 0) + 1
            if p.get("util") is False:
                negativos[aid] = negativos.get(aid, 0) + 1
    limite_rev = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    return {
        "dias": dias, "perguntas": total, "respondidas": respondidas,
        "sem_resposta": sum(1 for p in pergs if p.get("resultado") == "nao_sei"),
        "uteis": sum(1 for p in pergs if p.get("util") is True),
        "nao_resolveu": sum(1 for p in pergs if p.get("util") is False),
        "mais_usados": [{"titulo": nomes.get(k, "(artigo removido)"), "n": n} for k, n in sorted(usos.items(), key=lambda x: -x[1])[:6]],
        "mais_negativos": [{"titulo": nomes.get(k, "(artigo removido)"), "n": n} for k, n in sorted(negativos.items(), key=lambda x: -x[1])[:3]],
        "sem_revisao": [a.get("titulo") for a in artigos_salvos() if a.get("ativo", True) and (a.get("atualizado_em") or "") < limite_rev],
        "artigos_gerados": sum(1 for a in artigos() if a.get("gerado")),
        "artigos_ativos": sum(1 for a in artigos() if a.get("ativo", True)),
        "ia": provedor() or "base",
    }
