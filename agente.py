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
  GROQ_API_KEY / GROQ_MODEL           (Groq tem plano gratuito)
  ANTHROPIC_API_KEY / ANTHROPIC_MODEL (a mesma chave do resumo do Diagnóstico)
  AGENTE_PERFIS_ALIMENTAM   nomes de perfil, separados por vírgula (padrão: "Gerente")

Coleções no Firestore: agente_artigos · agente_pendencias · agente_perguntas · agente_historico
"""
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

import db_diario as db

router = APIRouter(prefix="/api/agente")
H: dict = {}   # funções do main.py: usuario_atual, nome_perfil

COL_ART = "agente_artigos"
COL_PEN = "agente_pendencias"
COL_PERG = "agente_perguntas"
COL_HIS = "agente_historico"

MAX_VERSOES = 20          # versões anteriores guardadas dentro de cada artigo (para "voltar versão")
MIN_PONTOS = 3            # pontuação mínima para considerar que um artigo responde a pergunta
_CACHE_SEG = 60


def ligar(app, usuario_atual: Callable, nome_perfil: Callable,
          setores: Optional[Callable] = None, usuarios: Optional[Callable] = None) -> None:
    """setores(): setores do Organograma ([{id, dados:{nome, pessoas:[{nome,cargo,email,vaga…}]}}]);
    usuarios(): {email: {nome, ativo}} de quem tem login no PQI."""
    H.update(usuario_atual=usuario_atual, nome_perfil=nome_perfil,
             setores=setores or (lambda: []), usuarios=usuarios or (lambda: {}))
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
_cache = {"t": 0.0, "artigos": []}


def esquecer_cache() -> None:
    _cache["t"] = 0.0


def artigos() -> list:
    if time.time() - _cache["t"] > _CACHE_SEG:
        _cache.update(t=time.time(), artigos=P["listar"](COL_ART))
    return _cache["artigos"]


def pontuar(pergunta: str, art: dict) -> int:
    """Quantas palavras da pergunta aparecem no artigo (título e palavras-chave valem mais)."""
    alvo_forte = set(palavras(art.get("titulo", "") + " " + art.get("palavras", "")))
    alvo_fraco = set(palavras(" ".join(art.get("passos") or [])))
    pts = 0
    for w in set(palavras(pergunta)):
        if w in alvo_forte:
            pts += 2
        elif w in alvo_fraco:
            pts += 1
        elif len(w) >= 5 and any(x.startswith(w[:5]) for x in alvo_forte):   # plural/variação: "rasgada" × "rasgado"
            pts += 1
    return pts


def buscar(pergunta: str, limite: int = 3) -> list:
    ativos = [a for a in artigos() if a.get("ativo", True)]
    notas = sorted(((pontuar(pergunta, a), a) for a in ativos), key=lambda x: -x[0])
    return [a for n, a in notas if n >= MIN_PONTOS][:limite]


def fonte(a: dict) -> dict:
    return {"id": a.get("id"), "titulo": a.get("titulo"), "versao": a.get("versao", 1),
            "atualizado_em": a.get("atualizado_em"), "por": (a.get("atualizado_por") or {}).get("nome")}


# ══════════════════════════════════════════════════════════════════════════
# IA externa (opcional)
# ══════════════════════════════════════════════════════════════════════════
INSTRUCOES = (
    "Você é o Agente do PQI, o assistente interno da King Star Colchões. Responda em português do Brasil, "
    "de forma curta e prática, em passos numerados quando for um procedimento. Use SOMENTE os artigos abaixo — "
    "eles são a fonte oficial. Não invente telas, botões, prazos nem regras que não estejam neles. "
    "Se os artigos não respondem a pergunta, responda exatamente: NAO_SEI. "
    "Na última linha, escreva FONTE: seguido do número do artigo que você usou (ex.: FONTE: 2)."
)


def _http_json(url: str, corpo: dict, cab: dict, timeout: int = 40) -> dict:
    req = urllib.request.Request(url, data=json.dumps(corpo).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **cab})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def provedor() -> str:
    p = os.environ.get("AGENTE_IA", "").strip().lower()
    if p == "groq" and os.environ.get("GROQ_API_KEY"):
        return "groq"
    if p == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return ""


def perguntar_ia(pergunta: str, achados: list) -> Optional[str]:
    """Texto da IA, "NAO_SEI", ou None se a IA está desligada/falhou (aí vale a resposta da base)."""
    prov = provedor()
    if not prov:
        return None
    base = "\n\n".join(f"ARTIGO {i + 1}: {a.get('titulo')}\n" + "\n".join(f"- {p}" for p in (a.get("passos") or []))
                       for i, a in enumerate(achados))
    usuario = f"ARTIGOS:\n{base}\n\nPERGUNTA: {pergunta}"
    try:
        if prov == "groq":
            r = _http_json("https://api.groq.com/openai/v1/chat/completions",
                           {"model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"), "temperature": 0.1, "max_tokens": 700,
                            "messages": [{"role": "system", "content": INSTRUCOES}, {"role": "user", "content": usuario}]},
                           {"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]})
            return (r.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or None
        r = _http_json("https://api.anthropic.com/v1/messages",
                       {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 700, "system": INSTRUCOES,
                        "messages": [{"role": "user", "content": usuario}]},
                       {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip() or None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
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


@router.get("/me")
def agente_me(request: Request):
    u = _usuario(request)
    pode = pode_alimentar(u)
    return {"pode_alimentar": pode, "pendencias": len(_abertas()) if pode else 0, "comigo": len(_comigo(u)),
            "ia": provedor() or "base", "nome": (u.get("nome") or u.get("email") or "").split(" ")[0],
            "sugestoes": [a.get("titulo") for a in sorted((a for a in artigos() if a.get("ativo", True)),
                                                           key=lambda a: -(a.get("usos") or 0))[:4]]}


@router.post("/perguntar")
def agente_perguntar(payload: Pergunta, request: Request):
    u = _usuario(request)
    achados = buscar(payload.pergunta)
    pid = novo_id()
    resp: dict = {"pergunta_id": pid}
    if achados:
        texto = perguntar_ia(payload.pergunta, achados)
        if texto and texto.strip().upper().startswith("NAO_SEI"):
            achados = []
        elif texto:
            texto, usado = _separar_fonte(texto, achados)
            resp.update(tipo="resposta", modo="ia", titulo=usado.get("titulo"), texto=texto, fonte=fonte(usado))
        if achados and "tipo" not in resp:
            a = achados[0]
            resp.update(tipo="resposta", modo="base", titulo=a.get("titulo"), passos=a.get("passos") or [], fonte=fonte(a))
    if not achados:
        resp.update(tipo="nao_sei")
        _nova_pendencia(u, payload.pergunta, "sem_resposta", pergunta_id=pid)
    P["gravar"](COL_PERG, pid, {"pergunta": payload.pergunta, "em": agora(), "de": _quem(u), "tela": payload.tela,
                                "resultado": resp["tipo"], "artigo_id": (resp.get("fonte") or {}).get("id"),
                                "modo": resp.get("modo"), "util": None})
    if resp.get("fonte"):   # contador de uso (ordena as sugestões)
        art = next((a for a in artigos() if a.get("id") == resp["fonte"]["id"]), None)
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
        "sem_revisao": [a.get("titulo") for a in artigos() if a.get("ativo", True) and (a.get("atualizado_em") or "") < limite_rev],
        "artigos_ativos": sum(1 for a in artigos() if a.get("ativo", True)),
        "ia": provedor() or "base",
    }
