"""
empresa.py — EMPRESA · FASE 0 (v7.0)

Um lugar só para a empresa "existir no papel": SETORES e PESSOAS (Organograma),
ATIVIDADES de cada setor ligadas a pessoas, a IT de cada atividade e o FLUXOGRAMA.
Quando todo setor fecha os 4 passos, a Fase 0 está pronta e a melhoria (PQI)
trabalha em cima do que existe.

NÃO tem banco próprio: lê e grava nos mesmos lugares de antes —
  • setores e pessoas ........ coleção do Organograma (um documento por setor)
  • atividades, IT, fluxograma  processos do Mapa ("Processos"): cada atividade é um processo
  • atividades levantadas .... inventário do Diagnóstico (aparecem para confirmar)
  • "lista completa" ......... o ✓ 100% mapeado da área (documento _tipo=arquitetura do Mapa)
Assim nada do que já existe se perde, e os editores completos (organograma, fluxograma,
passos da IT) continuam abrindo como antes, a partir daqui.

As rotas ficam em /api/empresa/* e são protegidas pelo módulo "empresa" (modulos.py).
"""
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/empresa")
H: dict = {}

EDITAR = 3


def ligar(app, **ganchos) -> None:
    """ganchos: usuario_atual, niveis, setores_do, processos, criar_processo, salvar_processo,
    inventario, nome, sincronizar, ia, ia_ligada"""
    H.update(ganchos)
    app.include_router(router)


# ─── utilidades ──────────────────────────────────────────────────────────
def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def tx(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def nrm(v) -> str:
    s = unicodedata.normalize("NFD", tx(v).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def lista_nomes(v) -> List[str]:
    return [tx(x) for x in re.split(r"\s*[,;]\s*", str(v or "")) if tx(x)]


def _u(request: Request) -> dict:
    return H["usuario_atual"](request)


def _nivel(u: dict) -> int:
    return int((H["niveis"](u) or {}).get("empresa", 0))


def _setor(u: dict, sid: str) -> dict:
    for d in H["setores_do"](u):
        if d["id"] == sid:
            return d
    raise HTTPException(404, "Setor não encontrado (ou você não tem acesso a ele).")


def _processos():
    todos = H["processos"]() or []
    cfg = next((p for p in todos if (p.get("dados") or {}).get("_tipo") == "arquitetura"), None)
    procs = [p for p in todos if isinstance(p.get("dados"), dict) and not p["dados"].get("_tipo")]
    return procs, cfg


def _proc(pid: str) -> dict:
    procs, _ = _processos()
    p = next((x for x in procs if x["id"] == pid), None)
    if not p:
        raise HTTPException(404, "Atividade não encontrada (pode ter sido excluída em Processos).")
    return p


def _do_setor(p: dict, setor: dict) -> bool:
    d = p.get("dados") or {}
    o = d.get("origem") or {}
    nome = nrm((setor.get("dados") or {}).get("nome"))
    return o.get("dep") == setor["id"] or (nome and nrm(d.get("area")) == nome)


def _tem(lst) -> list:
    return [x for x in (lst or []) if isinstance(x, dict) and tx(x.get("descricao"))]


def situacao_it(d: dict) -> dict:
    """IT da atividade: a nova (TO BE) se tem passos, senão a IT oficial; documento externo também conta."""
    it = d.get("it") or {}
    tb = d.get("tobe") or {}
    if _tem(tb.get("passos")):
        aprov = tx((d.get("validacao_tobe") or {}).get("parecer")).lower().startswith("aprov")
        return {"tem": True, "qual": "tobe", "passos": len(_tem(tb.get("passos"))), "codigo": tx(tb.get("codigo")) or tx(it.get("codigo")),
                "versao": tx(tb.get("versao")), "situacao": "nova IT aprovada" if aprov else "nova IT em validação"}
    if _tem(d.get("passos_it")):
        return {"tem": True, "qual": "it", "passos": len(_tem(d.get("passos_it"))), "codigo": tx(it.get("codigo")),
                "versao": tx(it.get("versao")), "situacao": "IT oficial"}
    if tx(it.get("link")) or tx(it.get("codigo")):
        return {"tem": True, "qual": "doc", "passos": 0, "codigo": tx(it.get("codigo")), "versao": tx(it.get("versao")),
                "situacao": "IT em documento" + (" (link)" if tx(it.get("link")) else "")}
    return {"tem": False, "qual": "", "passos": 0, "codigo": "", "versao": "", "situacao": "sem IT",
            "hoje": len(_tem(d.get("passos_real")))}


def situacao_fluxo(d: dict) -> dict:
    f = d.get("fluxo") or {}
    asis = len((f.get("asis") or {}).get("nos") or [])
    tobe = len((f.get("tobe") or {}).get("nos") or [])
    return {"tem": bool(asis or tobe), "simbolos": asis or tobe, "qual": "asis" if asis else ("tobe" if tobe else "")}


def _atividade(p: dict) -> dict:
    d = p["dados"]
    o = d.get("origem") or {}
    return {"id": p["id"], "versao": p.get("versao"), "codigo": tx(d.get("codigo")), "nome": tx(d.get("nome")),
            "pessoas": lista_nomes(d.get("dono")), "subsetor": tx(o.get("subsetor")), "macroprocesso": tx(d.get("macroprocesso")),
            "criticidade": tx(d.get("criticidade")), "it": situacao_it(d), "fluxo": situacao_fluxo(d),
            "objetivo": tx(d.get("objetivo")), "atualizado_em": p.get("atualizado_em"), "atualizado_por": p.get("atualizado_por")}


def _pct(feito: float, total: float) -> int:
    return int(round(100 * feito / total)) if total else 0


def resumo_setor(setor: dict, procs: list, cfg: Optional[dict]) -> dict:
    dados = setor.get("dados") or {}
    nome = tx(dados.get("nome"))
    pessoas = [{"id": x.get("id"), "nome": tx(x.get("nome")), "cargo": tx(x.get("cargo")), "subsetor": tx(x.get("subsetor")),
                "nivel": tx(x.get("nivel"))}
               for x in (dados.get("pessoas") or []) if isinstance(x, dict) and tx(x.get("nome")) and not x.get("vaga")]
    vagas = sum(1 for x in (dados.get("pessoas") or []) if isinstance(x, dict) and x.get("vaga"))
    ativs = [_atividade(p) for p in procs if _do_setor(p, setor)]
    ativs.sort(key=lambda a: (a["macroprocesso"].lower(), a["codigo"]))
    # atividades levantadas no Diagnóstico que ainda não viraram atividade aqui
    ja = {nrm(a["nome"]) for a in ativs} | {nrm(((p["dados"].get("origem") or {}).get("atividade"))) for p in procs if _do_setor(p, setor)}
    levantadas = []
    for i in H["inventario"](setor["id"]) or []:
        if isinstance(i, dict) and tx(i.get("atividade")) and nrm(i.get("atividade")) not in ja:
            ja.add(nrm(i.get("atividade")))
            levantadas.append({"nome": tx(i.get("atividade")), "subsetor": tx(i.get("subsetor")), "pessoas": lista_nomes(i.get("executores")),
                               "descricao": tx(i.get("descricao"))})
    area = next((a for a in ((cfg or {}).get("dados") or {}).get("areas") or [] if nrm(a.get("nome")) == nrm(nome)), None) or {}
    completa = bool(area.get("mapeado"))
    n = len(ativs)
    com_p = sum(1 for a in ativs if a["pessoas"])
    com_it = sum(1 for a in ativs if a["it"]["tem"])
    com_fx = sum(1 for a in ativs if a["fluxo"]["tem"])
    passos = {
        "pessoas": 100 if pessoas else 0,
        # atividades: cada uma com pessoa; só chega a 100% quando o setor confirma que a lista está completa
        "atividades": 0 if not n else (_pct(com_p, n + len(levantadas)) if completa else min(90, _pct(com_p, n + len(levantadas)))),
        "it": _pct(com_it, n + len(levantadas)),
        "fluxo": _pct(com_fx, n + len(levantadas)),
    }
    total = round(sum(passos.values()) / 4)
    return {"id": setor["id"], "nome": nome, "cor": dados.get("cor") or "", "descricao": tx(dados.get("descricao")),
            "responsavel": tx(dados.get("responsavel")),
            "subsetores": [s if isinstance(s, str) else (s or {}).get("nome", "") for s in (dados.get("subsetores") or [])],
            "pessoas": pessoas, "vagas": vagas, "atividades": ativs, "levantadas": levantadas, "lista_completa": completa,
            "lista_completa_por": tx(area.get("mapeado_por")), "lista_completa_em": tx(area.get("mapeado_em")),
            "contagem": {"atividades": n, "levantadas": len(levantadas), "com_pessoas": com_p, "com_it": com_it, "com_fluxo": com_fx},
            "passos": passos, "pct": total, "pronto": total == 100}


# ─── leitura ─────────────────────────────────────────────────────────────
@router.get("/visao")
def visao(request: Request):
    u = _u(request)
    procs, cfg = _processos()
    setores = [resumo_setor(s, procs, cfg) for s in H["setores_do"](u) if tx((s.get("dados") or {}).get("nome"))]
    setores.sort(key=lambda s: s["nome"].lower())
    nomes = {nrm(s["nome"]) for s in setores}
    ids = {s["id"] for s in setores}
    # processos cuja área não é um setor do Organograma: aparecem para não sumir nada
    fora = {}
    if H.get("ve_tudo", lambda u: True)(u):
        for p in procs:
            d = p["dados"]
            if (d.get("origem") or {}).get("dep") in ids or nrm(d.get("area")) in nomes:
                continue
            fora.setdefault(tx(d.get("area")) or "(sem área)", []).append(_atividade(p))
    total = round(sum(s["pct"] for s in setores) / len(setores)) if setores else 0
    tot = lambda k: sum(s["contagem"][k] for s in setores)
    return {"setores": setores,
            "fora": [{"area": a, "atividades": l} for a, l in sorted(fora.items())],
            "fase0": {"pct": total, "pronta": bool(setores) and all(s["pronto"] for s in setores), "setores": len(setores),
                      "setores_prontos": sum(1 for s in setores if s["pronto"]),
                      "pessoas": sum(len(s["pessoas"]) for s in setores), "atividades": tot("atividades"), "levantadas": tot("levantadas"),
                      "com_it": tot("com_it"), "com_fluxo": tot("com_fluxo"), "com_pessoas": tot("com_pessoas")},
            "nivel": _nivel(u), "ia": bool(H.get("ia_ligada", lambda: False)())}


# ─── atividades ──────────────────────────────────────────────────────────
class NovaAtividade(BaseModel):
    setor_id: str
    nome: str
    pessoas: List[str] = []
    subsetor: str = ""
    objetivo: str = ""


def proximo_codigo(procs: list) -> str:
    n = max([int(m.group(1)) for p in procs for m in [re.search(r"(\d+)$", tx((p.get("dados") or {}).get("codigo")))] if m] + [0])
    return f"PROC-{n + 1:03d}"


@router.post("/atividades")
def criar_atividade(payload: NovaAtividade, request: Request):
    u = _u(request)
    setor = _setor(u, payload.setor_id)
    nome = tx(payload.nome)
    if not nome:
        raise HTTPException(400, "Dê um nome à atividade.")
    procs, _ = _processos()
    igual = next((p for p in procs if _do_setor(p, setor) and nrm(p["dados"].get("nome")) == nrm(nome)), None)
    if igual:
        raise HTTPException(409, f"Já existe a atividade “{tx(igual['dados'].get('nome'))}” neste setor ({tx(igual['dados'].get('codigo'))}).")
    snome = tx((setor.get("dados") or {}).get("nome"))
    dados = {"codigo": proximo_codigo(procs), "nome": nome, "area": snome, "dono": ", ".join(tx(x) for x in payload.pessoas if tx(x)),
             "objetivo": tx(payload.objetivo), "criticidade": "Média", "status": "Em levantamento",
             "origem": {"dep": setor["id"], "dep_nome": snome, "atividade": nome, "subsetor": tx(payload.subsetor), "via": "empresa"}}
    reg = H["criar_processo"](dados, H["nome"](request), u.get("email"))
    return {"atividade": _atividade(reg)}


def _gravar(p: dict, dados: dict, request: Request) -> dict:
    res, reg = H["salvar_processo"](p["id"], dados, p.get("versao"), H["nome"](request))
    if res == "nao_existe":
        raise HTTPException(404, "Atividade não encontrada (pode ter sido excluída).")
    if res == "conflito":
        raise HTTPException(409, "Alguém alterou esta atividade agora há pouco. Atualize a tela e tente de novo.")
    try:
        H.get("sincronizar", lambda r: None)(reg)
    except Exception as e:   # noqa: BLE001  (o agente nunca derruba a gravação)
        print(f"[empresa] agente não sincronizou: {e}")
    return reg


def _setor_da_atividade(u: dict, p: dict) -> None:
    """Quem tem acesso só a alguns setores mexe só nas atividades deles."""
    if not H.get("ve_tudo", lambda u: True)(u) and not any(_do_setor(p, s) for s in H["setores_do"](u)):
        raise HTTPException(403, "Você não tem acesso ao setor desta atividade.")


@router.get("/atividades/{pid}")
def ler_atividade(pid: str, request: Request):
    """Uma atividade com os passos da IT (para o editor da IT na tela da Empresa)."""
    u = _u(request)
    p = _proc(pid)
    _setor_da_atividade(u, p)
    d = p["dados"]
    campos = ("descricao", "quem", "sistema", "regra")
    return {"atividade": _atividade(p),
            "it": {k: tx((d.get("it") or {}).get(k)) for k in ("codigo", "titulo", "versao", "data", "link")},
            "passos_it": [{k: tx(x.get(k)) for k in campos} for x in _tem(d.get("passos_it"))],
            "passos_tobe": [{k: tx(x.get(k)) for k in campos} for x in _tem((d.get("tobe") or {}).get("passos"))],
            "passos_hoje": [{k: tx(x.get(k)) for k in campos} for x in _tem(d.get("passos_real"))],
            "historico": [{"em": h.get("em"), "por": h.get("por"), "versao": h.get("versao"), "passos": len(_tem(h.get("passos")))}
                          for h in (d.get("it_historico") or [])][::-1]}


class Pessoas(BaseModel):
    pessoas: List[str] = []


@router.put("/atividades/{pid}/pessoas")
def definir_pessoas(pid: str, payload: Pessoas, request: Request):
    u = _u(request)
    p = _proc(pid)
    _setor_da_atividade(u, p)
    d = dict(p["dados"])
    novas = [tx(x) for x in payload.pessoas if tx(x)]
    if _nivel(u) < EDITAR and lista_nomes(d.get("dono")) and set(map(nrm, lista_nomes(d.get("dono")))) - set(map(nrm, novas)):
        raise HTTPException(403, "Seu perfil é “Adicionar”: dá para incluir pessoas, mas tirar quem já estava é só para quem tem “Editar”.")
    d["dono"] = ", ".join(dict.fromkeys(novas))
    return {"atividade": _atividade(_gravar(p, d, request))}


class Renomear(BaseModel):
    nome: str = ""
    objetivo: Optional[str] = None


@router.put("/atividades/{pid}")
def renomear(pid: str, payload: Renomear, request: Request):
    u = _u(request)
    p = _proc(pid)
    _setor_da_atividade(u, p)
    if _nivel(u) < EDITAR:
        raise HTTPException(403, "Renomear uma atividade é só para quem tem perfil “Editar”.")
    d = dict(p["dados"])
    if tx(payload.nome):
        d["nome"] = tx(payload.nome)
    if payload.objetivo is not None:
        d["objetivo"] = tx(payload.objetivo)
    return {"atividade": _atividade(_gravar(p, d, request))}


class ListaCompleta(BaseModel):
    completa: bool = True


@router.post("/setores/{sid}/lista-completa")
def lista_completa(sid: str, payload: ListaCompleta, request: Request):
    u = _u(request)
    setor = _setor(u, sid)
    nome = tx((setor.get("dados") or {}).get("nome"))
    _, cfg = _processos()
    quem = H["nome"](request)
    if not cfg:
        cfg = H["criar_processo"]({"_tipo": "arquitetura", "areas": []}, quem, u.get("email"))
    d = dict(cfg.get("dados") or {})
    areas = [dict(a) for a in (d.get("areas") or []) if isinstance(a, dict)]
    a = next((x for x in areas if nrm(x.get("nome")) == nrm(nome)), None)
    if not a:
        a = {"nome": nome, "responsavel": "", "mapeado": False, "mapeado_por": "", "mapeado_em": "", "obs": ""}
        areas.append(a)
    a.update(mapeado=bool(payload.completa), mapeado_por=quem if payload.completa else "", mapeado_em=agora() if payload.completa else "")
    d["areas"] = areas
    res, _reg = H["salvar_processo"](cfg["id"], d, cfg.get("versao"), quem)
    if res != "ok":
        raise HTTPException(409, "Não consegui gravar agora (alguém mexeu ao mesmo tempo). Tente de novo.")
    return {"sucesso": True, "completa": bool(payload.completa)}


# ─── IT ──────────────────────────────────────────────────────────────────
class Passo(BaseModel):
    descricao: str = ""
    quem: str = ""
    sistema: str = ""
    regra: str = ""


class GravarIT(BaseModel):
    codigo: str = ""
    titulo: str = ""
    versao: str = ""
    passos: List[Passo] = []
    origem: str = "manual"   # manual · ia


def _id() -> str:
    import secrets
    return secrets.token_hex(5)


@router.put("/atividades/{pid}/it")
def gravar_it(pid: str, payload: GravarIT, request: Request):
    """Grava a IT oficial da atividade (passos da IT). A versão anterior fica guardada em it_historico."""
    u = _u(request)
    p = _proc(pid)
    _setor_da_atividade(u, p)
    d = json.loads(json.dumps(p["dados"]))
    antes = _tem(d.get("passos_it"))
    if antes and _nivel(u) < EDITAR:
        raise HTTPException(403, "Esta atividade já tem IT. Ajustar uma IT existente é só para quem tem perfil “Editar”.")
    novos = [x for x in payload.passos if tx(x.descricao)]
    if not novos:
        raise HTTPException(400, "A IT precisa de pelo menos um passo.")
    it = dict(d.get("it") or {})
    if antes:
        hist = list(d.get("it_historico") or [])
        hist.append({"em": agora(), "por": H["nome"](request), "codigo": tx(it.get("codigo")), "versao": tx(it.get("versao")),
                     "titulo": tx(it.get("titulo")), "passos": d.get("passos_it")})
        d["it_historico"] = hist[-20:]
    velhos = d.get("passos_it") or []
    passos = []
    for i, x in enumerate(novos):   # reaproveita os ids na mesma posição: o "como é feito hoje" continua ligado
        base = dict(velhos[i]) if i < len(velhos) and isinstance(velhos[i], dict) else {"id": _id(), "obs": ""}
        base.update(descricao=tx(x.descricao), quem=tx(x.quem), sistema=tx(x.sistema), regra=tx(x.regra))
        passos.append(base)
    d["passos_it"] = passos
    versao_ant = tx(it.get("versao"))
    if tx(payload.versao):
        nova_versao = tx(payload.versao)
    elif antes:
        m = re.match(r"^(\d+)", versao_ant)
        nova_versao = str(int(m.group(1)) + 1) if m else (versao_ant + "+1" if versao_ant else "2")
    else:
        nova_versao = "1"
    it.update(codigo=tx(payload.codigo) or tx(it.get("codigo")) or ("IT-" + tx(d.get("codigo")).replace("PROC-", "")),
              titulo=tx(payload.titulo) or tx(it.get("titulo")) or tx(d.get("nome")), versao=nova_versao,
              data=agora()[:10], link=it.get("link", ""), obs=it.get("obs", ""))
    if payload.origem == "ia":
        it["gerada_com_ia"] = {"em": agora(), "por": H["nome"](request)}
    d["it"] = it
    return {"atividade": _atividade(_gravar(p, d, request))}


class PedidoIA(BaseModel):
    processo_id: str
    pedido: str = ""


INSTRUCOES_IT = """Você escreve Instruções de Trabalho (IT) para a King Star Colchões, em português do Brasil.
Uma IT é o passo a passo oficial de UMA atividade: frases curtas, no imperativo, uma ação por passo, na ordem em que acontecem.
Use só o que foi informado. Se faltar algo, escreva o passo de forma geral, sem inventar sistema, número, prazo ou nome.
Responda SOMENTE com JSON, sem texto antes ou depois, no formato:
{"titulo": "...", "passos": [{"descricao": "...", "quem": "...", "sistema": "...", "regra": "..."}]}
"quem" = cargo ou pessoa que faz (das pessoas informadas); "sistema" = onde faz (ERP, planilha, e-mail…), se informado; "regra" = condição ou cuidado do passo, se houver.
Entre 4 e 15 passos."""


def _contexto_ia(p: dict) -> str:
    d = p["dados"]
    o = d.get("origem") or {}
    linhas = [f"ATIVIDADE: {tx(d.get('nome'))}", f"SETOR: {tx(d.get('area'))}" + (f" · subsetor {tx(o.get('subsetor'))}" if tx(o.get("subsetor")) else "")]
    if tx(d.get("objetivo")):
        linhas.append("OBJETIVO: " + tx(d.get("objetivo")))
    if lista_nomes(d.get("dono")):
        linhas.append("QUEM FAZ: " + ", ".join(lista_nomes(d.get("dono"))))
    for rot, campo in (("ENTRADAS", "entradas"), ("SAÍDAS", "saidas"), ("SISTEMAS", "sistemas"), ("GATILHO", "gatilho")):
        if tx(d.get(campo)):
            linhas.append(f"{rot}: {tx(d.get(campo))}")
    it = _tem(d.get("passos_it"))
    if it:
        linhas.append("IT ATUAL (a ajustar):")
        linhas += [f"{i}. {tx(x.get('descricao'))}" + (f" (quem: {tx(x.get('quem'))})" if tx(x.get("quem")) else "")
                   + (f" (onde: {tx(x.get('sistema'))})" if tx(x.get("sistema")) else "") + (f" (regra: {tx(x.get('regra'))})" if tx(x.get("regra")) else "")
                   for i, x in enumerate(it, 1)]
    real = _tem(d.get("passos_real"))
    if real:
        linhas.append("COMO É FEITO HOJE (levantamento):")
        linhas += [f"{i}. {tx(x.get('descricao'))}" + (f" (quem: {tx(x.get('quem'))})" if tx(x.get("quem")) else "") for i, x in enumerate(real[:30], 1)]
    return "\n".join(linhas)


def ler_json_ia(texto: str) -> Optional[dict]:
    if not texto:
        return None
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except ValueError:
        return None
    passos = [{k: tx((x or {}).get(k)) for k in ("descricao", "quem", "sistema", "regra")} for x in (j.get("passos") or []) if isinstance(x, dict)]
    passos = [x for x in passos if x["descricao"]][:30]
    return {"titulo": tx(j.get("titulo")), "passos": passos} if passos else None


@router.post("/it-ia")
def it_com_ia(payload: PedidoIA, request: Request):
    """Proposta de IT (nova ou ajustada) pela IA. NÃO grava: a pessoa revisa e aplica."""
    u = _u(request)
    p = _proc(payload.processo_id)
    _setor_da_atividade(u, p)
    if not H.get("ia_ligada", lambda: False)():
        raise HTTPException(501, "A IA não está ligada neste servidor. No Render, configure AGENTE_IA=groq e GROQ_API_KEY (plano gratuito do Groq).")
    pedido = tx(payload.pedido)
    usuario = _contexto_ia(p) + ("\n\nPEDIDO DE QUEM ESTÁ ESCREVENDO: " + pedido if pedido else "")
    usuario += "\n\n" + ("Ajuste a IT atual conforme o pedido, mantendo o que não precisa mudar." if _tem(p["dados"].get("passos_it"))
                         else "Escreva a IT desta atividade.")
    texto = H["ia"](INSTRUCOES_IT, usuario)
    j = ler_json_ia(texto or "")
    if not j:
        raise HTTPException(502, "A IA não devolveu uma IT válida agora. Tente de novo (ou descreva melhor a atividade).")
    return {"proposta": j, "ajuste": bool(_tem(p["dados"].get("passos_it")))}
