"""
melhoria.py — MELHORIA CONTÍNUA (v7.2) · parte 1

Toda melhoria nasce de algo que EXISTE no Fluxo interno (um processo/atividade mapeado)
e anda em PDCA: Planejar → Fazer → Checar → Padronizar.

  • CARD DE MELHORIA (coleção melhoria_cards): o quê e onde, triagem (problema ou mudança de forma),
    ganho esperado, indicador, data da mudança, plano de ação, ganho (R$ e tempo), ATAs e anotações.
  • INDICADOR (coleção melhoria_indicadores): pertence ao PROCESSO (serve para várias melhorias).
    Pode ser SOLICITADO a um gestor, que preenche ou DELEGA. Guarda os LANÇAMENTOS (data, valor).
  • PROVA (comparar): antes × depois da data da mudança. Com ≥ 4 valores de cada lado, a melhoria
    é COMPROVADA quando a média depois sai da variação normal do antes (média ± 2 desvios), no
    sentido bom do indicador. Senão: "pode ser variação normal". Com menos: "ainda sem prova".
  • GANHO: custo (R$ unitário antes × depois × quantidade/mês) e tempo (min antes × depois × vezes/mês).

Lê o Fluxo interno (Organograma + processos) só para mostrar; não grava nele.
"""
import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/melhoria")
H: dict = {}
COL_CARD = "melhoria_cards"
COL_IND = "melhoria_indicadores"
ETAPAS = ["planejar", "fazer", "checar", "padronizar", "concluida", "cancelada"]
MIN_PROVA = 4
EDITAR = 3


def ligar(app, **ganchos) -> None:
    """ganchos: usuario_atual, niveis, nome, setores_do, processos, docs_listar, docs_criar, docs_salvar,
    docs_ler, docs_excluir, ia, ia_ligada, agora (opcional)"""
    H.update(ganchos)
    app.include_router(router)


# ─── utilidades ──────────────────────────────────────────────────────────
def tx(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def nrm(v) -> str:
    s = unicodedata.normalize("NFD", tx(v).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def agora() -> str:
    return (H.get("agora") or (lambda: datetime.now(timezone.utc).isoformat()))()


def hoje() -> date:
    return date.fromisoformat(agora()[:10])


def num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    s = tx(v).replace("R$", "").replace("%", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        f = float(s)
        return f if math.isfinite(f) else None
    except ValueError:
        return None


def data_iso(v) -> Optional[str]:
    """Aceita 2026-11-12, 12/11/2026, 12/11/26, 12/11 (ano corrente)."""
    s = tx(v)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        a, mm, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", s)
        if not m:
            return None
        d, mm = int(m.group(1)), int(m.group(2))
        a = int(m.group(3)) if m.group(3) else hoje().year
        if a < 100:
            a += 2000
    try:
        return date(a, mm, d).isoformat()
    except ValueError:
        return None


def _u(request: Request) -> dict:
    return H["usuario_atual"](request)


def _nivel(u: dict) -> int:
    return int((H["niveis"](u) or {}).get("melhoria", 0))


def _eu(u: dict) -> dict:
    return {"nome": tx(u.get("nome")) or tx(u.get("email")), "email": tx(u.get("email")).lower()}


# ─── PROVA: antes × depois ───────────────────────────────────────────────
def _media(v: list) -> Optional[float]:
    return sum(v) / len(v) if v else None


def _dp(v: list) -> float:
    if len(v) < 2:
        return 0.0
    m = _media(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def comparar(medicoes: list, data_mudanca: str, sentido: str = "maior") -> dict:
    """Linha de base (antes da data da mudança) × resultado (a partir dela)."""
    pts = sorted(((data_iso(m.get("data")), num(m.get("valor"))) for m in medicoes or [] if data_iso(m.get("data")) and num(m.get("valor")) is not None),
                 key=lambda x: x[0])
    if not data_iso(data_mudanca):
        v = [x for _, x in pts]
        return {"situacao": "sem_mudanca", "texto": "Ainda sem data da mudança: tudo o que foi lançado conta como linha de base (o “antes”).",
                "antes": {"n": len(v), "media": _media(v), "dp": _dp(v)}, "depois": {"n": 0, "media": None}, "variacao_pct": None}
    dm = data_iso(data_mudanca)
    antes = [x for d, x in pts if d < dm]
    depois = [x for d, x in pts if d >= dm]
    ma, md, dpa = _media(antes), _media(depois), _dp(antes)
    var = ((md - ma) / abs(ma) * 100) if (ma not in (None, 0) and md is not None) else None
    bom = (lambda d: d > 0) if sentido != "menor" else (lambda d: d < 0)
    r = {"antes": {"n": len(antes), "media": ma, "dp": dpa}, "depois": {"n": len(depois), "media": md}, "variacao_pct": var,
         "limite_inf": (ma - 2 * dpa) if ma is not None else None, "limite_sup": (ma + 2 * dpa) if ma is not None else None}
    if len(antes) < MIN_PROVA or len(depois) < MIN_PROVA:
        faltam = []
        if len(antes) < MIN_PROVA:
            faltam.append(f"{MIN_PROVA - len(antes)} valor(es) de ANTES da mudança")
        if len(depois) < MIN_PROVA:
            faltam.append(f"{MIN_PROVA - len(depois)} valor(es) DEPOIS da mudança")
        r.update(situacao="poucos_dados", texto="Ainda sem prova: faltam " + " e ".join(faltam) + ".")
        return r
    dif = md - ma
    if not bom(dif) and dif != 0:
        r.update(situacao="piorou", texto="O indicador PIOROU depois da mudança.")
    elif dif == 0:
        r.update(situacao="variacao_normal", texto="Sem diferença entre antes e depois.")
    elif (dpa == 0) or (md > ma + 2 * dpa if sentido != "menor" else md < ma - 2 * dpa):
        r.update(situacao="comprovada", texto="Melhoria COMPROVADA: a média depois saiu da variação normal do antes (média ± 2 desvios).")
    else:
        r.update(situacao="variacao_normal", texto="Melhorou, mas ainda dentro da variação normal do antes — pode ser acaso. Continue lançando.")
    return r


def ganhos(g: dict) -> dict:
    """Custo: (antes − depois) × quantidade/mês. Tempo: (min antes − min depois) × vezes/mês, em horas e em R$."""
    g = g or {}
    c, t = g.get("custo") or {}, g.get("tempo") or {}
    out = {}
    a, d, q = num(c.get("antes")), num(c.get("depois")), num(c.get("qtd_mes"))
    if a is not None and d is not None:
        out["custo"] = {"reducao_pct": ((a - d) / a * 100) if a else None, "por_unidade": a - d,
                        "mes": (a - d) * q if q is not None else None, "ano": (a - d) * q * 12 if q is not None else None}
    ma, md, vz, ch = num(t.get("min_antes")), num(t.get("min_depois")), num(t.get("vezes_mes")), num(t.get("custo_hora"))
    if ma is not None and md is not None:
        h = (ma - md) * vz / 60 if vz is not None else None
        out["tempo"] = {"reducao_pct": ((ma - md) / ma * 100) if ma else None, "min_por_vez": ma - md, "horas_mes": h,
                        "reais_mes": h * ch if (h is not None and ch is not None) else None,
                        "reais_ano": h * ch * 12 if (h is not None and ch is not None) else None}
    return out


# ─── lembrete de lançamento ──────────────────────────────────────────────
DIAS_FREQ = {"diaria": 1, "semanal": 7, "quinzenal": 15, "mensal": 31}


def proximo_lancamento(ind: dict) -> Optional[str]:
    if ind.get("status") != "ativo":
        return None
    dias = DIAS_FREQ.get(ind.get("frequencia") or "semanal", 7)
    datas = sorted(data_iso(m.get("data")) for m in ind.get("medicoes") or [] if data_iso(m.get("data")))
    base = date.fromisoformat(datas[-1]) if datas else date.fromisoformat((ind.get("ativado_em") or agora())[:10])
    return (base + timedelta(days=dias)).isoformat()


# ─── Fluxo interno (só leitura) ──────────────────────────────────────────
def _resumo_processo(p: dict) -> dict:
    d = p.get("dados") or {}
    o = d.get("origem") or {}
    real = [x for x in d.get("passos_real") or [] if isinstance(x, dict) and tx(x.get("descricao"))]
    it = [x for x in d.get("passos_it") or [] if isinstance(x, dict) and tx(x.get("descricao"))]
    ids_it = {x.get("id") for x in it}
    usados = {x.get("it_ref") for x in real if x.get("it_ref")}
    horas = sum((num(x.get("tempo_min")) or 0) * (num(x.get("vezes_mes")) or 0) for x in real) / 60
    return {"id": p["id"], "codigo": tx(d.get("codigo")), "nome": tx(d.get("nome")), "area": tx(d.get("area")),
            "setor_id": o.get("dep") or "", "subsetor": tx(o.get("subsetor")), "dono": tx(d.get("dono")),
            "tem_it": bool(it) or any(tx(x.get("descricao")) for x in (d.get("tobe") or {}).get("passos") or []),
            "passos": len(real), "horas_mes": round(horas, 1),
            "gaps": {"sem_it": sum(1 for x in real if not x.get("it_ref")) if it else 0,
                     "divergente": sum(1 for x in real if x.get("aderencia") == "Divergente"),
                     "nao_feito": len(ids_it - usados) if real else 0}}


def _processos() -> List[dict]:
    return [_resumo_processo(p) for p in H["processos"]() or [] if isinstance(p.get("dados"), dict) and not p["dados"].get("_tipo")]


def _setores(u: dict) -> List[dict]:
    out = []
    for s in H["setores_do"](u):
        d = s.get("dados") or {}
        if not tx(d.get("nome")):
            continue
        out.append({"id": s["id"], "nome": tx(d.get("nome")), "responsavel": tx(d.get("responsavel")),
                    "subsetores": [x if isinstance(x, str) else (x or {}).get("nome", "") for x in d.get("subsetores") or []],
                    "pessoas": [{"nome": tx(p.get("nome")), "cargo": tx(p.get("cargo")), "email": tx(p.get("email")).lower(),
                                 "nivel": tx(p.get("nivel")), "subsetor": tx(p.get("subsetor"))}
                                for p in d.get("pessoas") or [] if isinstance(p, dict) and tx(p.get("nome")) and not p.get("vaga")]})
    out.sort(key=lambda s: s["nome"].lower())
    return out


@router.get("/base")
def base(request: Request):
    u = _u(request)
    return {"eu": _eu(u), "nivel": _nivel(u), "ia": bool(H.get("ia_ligada", lambda: False)()),
            "setores": _setores(u), "processos": _processos()}


# ─── documentos ──────────────────────────────────────────────────────────
def _listar(col):
    return [d for d in H["docs_listar"](col) if isinstance(d.get("dados"), dict)]


def _ler(col, did):
    d = H["docs_ler"](col, did)
    if not d:
        raise HTTPException(404, "Não encontrado (pode ter sido excluído).")
    return d


def _gravar(col, did, dados, versao, request):
    res, reg = H["docs_salvar"](col, did, dados, versao, H["nome"](request))
    if res == "nao_existe":
        raise HTTPException(404, "Não encontrado (pode ter sido excluído).")
    if res == "conflito":
        raise HTTPException(409, "Alguém alterou isto agora há pouco. Atualize a tela e tente de novo.")
    return reg


def _hist(dados: dict, quem: str, texto: str) -> None:
    dados["historico"] = (list(dados.get("historico") or []) + [{"em": agora(), "por": quem, "texto": texto}])[-200:]


class Doc(BaseModel):
    dados: dict
    versao: Optional[int] = None


# ─── cards de melhoria ───────────────────────────────────────────────────
NOME_ETAPA = {"planejar": "Planejar", "fazer": "Fazer", "checar": "Checar", "padronizar": "Padronizar", "concluida": "Concluída", "cancelada": "Cancelada"}


@router.get("/cards")
def cards_listar(request: Request):
    _u(request)
    return {"cards": _listar(COL_CARD)}


@router.post("/cards")
def card_criar(payload: Doc, request: Request):
    u = _u(request)
    d = dict(payload.dados or {})
    if not tx(d.get("titulo")):
        raise HTTPException(400, "Dê um título à melhoria (o que você quer melhorar).")
    if not tx(d.get("processo_id")):
        raise HTTPException(400, "Escolha o processo do Fluxo interno: toda melhoria nasce de algo que já está mapeado.")
    proc = next((p for p in _processos() if p["id"] == d["processo_id"]), None)
    if not proc:
        raise HTTPException(400, "Processo não encontrado no Fluxo interno.")
    n = max([int(m.group(1)) for c in _listar(COL_CARD) for m in [re.search(r"(\d+)$", tx((c.get("dados") or {}).get("codigo")))] if m] + [0])
    d.update(codigo=f"MC-{n + 1:03d}", etapa="planejar", processo_codigo=proc["codigo"], processo_nome=proc["nome"],
             setor_nome=proc["area"], setor_id=proc["setor_id"], subsetor=d.get("subsetor") or proc["subsetor"],
             criado_por=_eu(u), responsavel=d.get("responsavel") or _eu(u)["nome"])
    for k in ("acoes", "atas", "anotacoes"):
        d.setdefault(k, [])
    _hist(d, H["nome"](request), "Melhoria criada")
    return H["docs_criar"](COL_CARD, d, H["nome"](request), u.get("email"))


@router.put("/cards/{cid}")
def card_salvar(cid: str, payload: Doc, request: Request):
    _u(request)
    atual = _ler(COL_CARD, cid)
    novo = dict(payload.dados or {})
    antes = atual.get("dados") or {}
    for k in ("codigo", "criado_por", "historico"):   # o servidor cuida destes
        novo[k] = antes.get(k)
    if novo.get("etapa") not in ETAPAS:
        novo["etapa"] = antes.get("etapa", "planejar")
    quem = H["nome"](request)
    if novo["etapa"] != antes.get("etapa"):
        _hist(novo, quem, f"Etapa: {NOME_ETAPA.get(antes.get('etapa'), '—')} → {NOME_ETAPA[novo['etapa']]}")
    if tx(novo.get("data_mudanca")) != tx(antes.get("data_mudanca")):
        _hist(novo, quem, f"Data da mudança: {tx(novo.get('data_mudanca')) or '—'}")
    if tx(novo.get("indicador_id")) != tx(antes.get("indicador_id")) or bool(novo.get("sem_indicador")) != bool(antes.get("sem_indicador")):
        _hist(novo, quem, "Seguiu SEM indicador (melhoria não comprovada)" if novo.get("sem_indicador") else "Indicador da melhoria definido")
    return _gravar(COL_CARD, cid, novo, payload.versao, request)


@router.delete("/cards/{cid}")
def card_excluir(cid: str, request: Request):
    if _nivel(_u(request)) < EDITAR:
        raise HTTPException(403, "Excluir é só para quem tem perfil “Editar”.")
    if not H["docs_excluir"](COL_CARD, cid, H["nome"](request)):
        raise HTTPException(404, "Não encontrado.")
    return {"sucesso": True}


@router.get("/cards/{cid}/resultado")
def card_resultado(cid: str, request: Request):
    _u(request)
    c = _ler(COL_CARD, cid)["dados"]
    ind = H["docs_ler"](COL_IND, c.get("indicador_id")) if tx(c.get("indicador_id")) else None
    comp = comparar((ind or {}).get("dados", {}).get("medicoes"), c.get("data_mudanca"), (ind or {}).get("dados", {}).get("sentido", "maior")) if ind else None
    return {"comparacao": comp, "ganhos": ganhos(c.get("ganho"))}


# ─── indicadores ─────────────────────────────────────────────────────────
@router.get("/indicadores")
def ind_listar(request: Request):
    _u(request)
    out = []
    for d in _listar(COL_IND):
        x = d["dados"]
        ms = sorted((m for m in x.get("medicoes") or [] if data_iso(m.get("data"))), key=lambda m: data_iso(m.get("data")))
        out.append({**d, "ultimo": ms[-1] if ms else None, "proximo": proximo_lancamento(x)})
    return {"indicadores": out}


def _limpar_ind(x: dict) -> dict:
    x = dict(x or {})
    if x.get("sentido") not in ("maior", "menor"):
        x["sentido"] = "maior"
    if x.get("frequencia") not in DIAS_FREQ:
        x["frequencia"] = "semanal"
    x.setdefault("medicoes", [])
    return x


@router.post("/indicadores")
def ind_criar(payload: Doc, request: Request):
    u = _u(request)
    x = _limpar_ind(payload.dados)
    if not tx(x.get("processo_id")):
        raise HTTPException(400, "O indicador é de um processo do Fluxo interno: escolha o processo.")
    x["status"] = "ativo" if tx(x.get("nome")) else "solicitado"   # sem nome = pedido (alguém vai definir)
    if x["status"] == "ativo":
        x["ativado_em"] = agora()
    x["criado_por"] = _eu(u)
    _hist(x, H["nome"](request), "Indicador criado" if x["status"] == "ativo" else "Indicador solicitado")
    return H["docs_criar"](COL_IND, x, H["nome"](request), u.get("email"))


@router.put("/indicadores/{iid}")
def ind_salvar(iid: str, payload: Doc, request: Request):
    _u(request)
    atual = _ler(COL_IND, iid)
    antes = atual["dados"]
    x = _limpar_ind(payload.dados)
    for k in ("medicoes", "historico", "pedido", "criado_por", "ativado_em"):   # lançamentos e pedido têm rotas próprias
        x[k] = antes.get(k)
    quem = H["nome"](request)
    if tx(x.get("nome")) and antes.get("status") != "ativo":
        x["status"], x["ativado_em"] = "ativo", agora()
        _hist(x, quem, "Indicador preenchido e ATIVADO")
    else:
        x["status"] = antes.get("status")
        _hist(x, quem, "Indicador alterado")
    return _gravar(COL_IND, iid, x, payload.versao, request)


class Lancamentos(BaseModel):
    valores: List[dict]          # [{data, valor, obs}]


@router.post("/indicadores/{iid}/medicoes")
def ind_lancar(iid: str, payload: Lancamentos, request: Request):
    u = _u(request)
    for _ in range(3):   # se alguém lançou junto, tenta de novo com a versão nova
        atual = _ler(COL_IND, iid)
        x = dict(atual["dados"])
        if x.get("status") != "ativo":
            raise HTTPException(400, "Este indicador ainda não foi preenchido. Preencha (ou peça ao gestor) antes de lançar valores.")
        novas, erros = [], []
        for i, v in enumerate(payload.valores or [], 1):
            d, n = data_iso(v.get("data")), num(v.get("valor"))
            if not d or n is None:
                erros.append(f"linha {i}: data ou valor inválido")
                continue
            novas.append({"id": f"m{datetime.now().timestamp():.0f}{i}", "data": d, "valor": n, "obs": tx(v.get("obs")),
                          "por": _eu(u)["nome"], "em": agora()})
        if not novas:
            raise HTTPException(400, "Nada para lançar. " + "; ".join(erros))
        novas = list({n["data"]: n for n in novas}.values())   # a mesma data duas vezes na colagem: vale a última
        meds = [m for m in x.get("medicoes") or [] if data_iso(m.get("data")) not in {n["data"] for n in novas}] + novas   # mesma data: vale o novo
        meds.sort(key=lambda m: data_iso(m.get("data")))
        x["medicoes"] = meds
        _hist(x, H["nome"](request), f"{len(novas)} valor(es) lançado(s)")
        res, reg = H["docs_salvar"](COL_IND, iid, x, atual.get("versao"), H["nome"](request))
        if res == "ok":
            return {"indicador": reg, "lancados": len(novas), "erros": erros}
    raise HTTPException(409, "Muita gente lançando ao mesmo tempo. Tente de novo.")


@router.delete("/indicadores/{iid}/medicoes/{mid}")
def ind_apagar_lancamento(iid: str, mid: str, request: Request):
    if _nivel(_u(request)) < EDITAR:
        raise HTTPException(403, "Apagar um lançamento é só para quem tem perfil “Editar”.")
    atual = _ler(COL_IND, iid)
    x = dict(atual["dados"])
    x["medicoes"] = [m for m in x.get("medicoes") or [] if m.get("id") != mid]
    _hist(x, H["nome"](request), "Lançamento apagado")
    return {"indicador": _gravar(COL_IND, iid, x, atual.get("versao"), request)}


class Pedido(BaseModel):
    para_nome: str
    para_email: str = ""
    mensagem: str = ""


@router.post("/indicadores/{iid}/pedido")
def ind_pedir(iid: str, payload: Pedido, request: Request):
    """Pede a um gestor que defina (ou preencha) o indicador."""
    u = _u(request)
    atual = _ler(COL_IND, iid)
    x = dict(atual["dados"])
    if not tx(payload.para_nome):
        raise HTTPException(400, "Escolha o gestor que vai definir o indicador.")
    x["pedido"] = {"por": _eu(u), "em": agora(), "para": {"nome": tx(payload.para_nome), "email": tx(payload.para_email).lower()},
                   "mensagem": tx(payload.mensagem), "delegado": None}
    if x.get("status") != "ativo":
        x["status"] = "solicitado"
    _hist(x, H["nome"](request), f"Pedido de indicador para {tx(payload.para_nome)}")
    return {"indicador": _gravar(COL_IND, iid, x, atual.get("versao"), request)}


@router.post("/indicadores/{iid}/delegar")
def ind_delegar(iid: str, payload: Pedido, request: Request):
    """O gestor passa o pedido a alguém do time."""
    u = _u(request)
    atual = _ler(COL_IND, iid)
    x = dict(atual["dados"])
    p = x.get("pedido") or {}
    me = _eu(u)["email"]
    dono = (p.get("delegado") or p.get("para") or {}).get("email", "")
    if dono and dono != me and _nivel(u) < EDITAR:
        raise HTTPException(403, "Só quem recebeu o pedido (ou quem tem perfil “Editar”) pode delegar.")
    p["delegado"] = {"nome": tx(payload.para_nome), "email": tx(payload.para_email).lower(), "por": _eu(u)["nome"], "em": agora(),
                     "mensagem": tx(payload.mensagem)}
    x["pedido"] = p
    if x.get("status") == "solicitado":
        x["status"] = "em_preenchimento"
    _hist(x, H["nome"](request), f"Pedido delegado a {tx(payload.para_nome)}")
    return {"indicador": _gravar(COL_IND, iid, x, atual.get("versao"), request)}


# ─── minhas pendências ───────────────────────────────────────────────────
@router.get("/pendencias")
def pendencias(request: Request):
    u = _u(request)
    me, meu_nome = _eu(u)["email"], nrm(_eu(u)["nome"])
    eu = lambda pess: bool(pess) and ((tx(pess.get("email")).lower() == me and me) or (nrm(pess.get("nome")) == meu_nome and meu_nome))
    out = []
    hj = hoje().isoformat()
    for d in _listar(COL_IND):
        x = d["dados"]
        p = x.get("pedido") or {}
        alvo = p.get("delegado") or p.get("para")
        if x.get("status") in ("solicitado", "em_preenchimento") and eu(alvo):
            out.append({"tipo": "pedido_indicador", "id": d["id"], "titulo": f"Definir o indicador do processo {tx(x.get('processo_nome'))}",
                        "detalhe": f"Pedido por {(p.get('por') or {}).get('nome', '—')}" + (f": “{tx(p.get('mensagem'))}”" if tx(p.get("mensagem")) else ""),
                        "em": p.get("em")})
        prox = proximo_lancamento(x)
        if prox and prox <= hj and (eu({"nome": x.get("quem_mede"), "email": x.get("quem_mede_email")}) or eu(x.get("criado_por"))):
            out.append({"tipo": "lancar", "id": d["id"], "titulo": f"Lançar o valor de “{tx(x.get('nome'))}”",
                        "detalhe": f"Frequência {x.get('frequencia')} · era para {prox[8:10]}/{prox[5:7]}", "em": prox})
    for d in _listar(COL_CARD):
        c = d["dados"]
        if c.get("etapa") in ("concluida", "cancelada"):
            continue
        for a in c.get("acoes") or []:
            if a.get("status") != "Feita" and nrm(a.get("quem")) == meu_nome and meu_nome:
                atras = bool(data_iso(a.get("quando"))) and data_iso(a.get("quando")) < hj
                out.append({"tipo": "acao", "id": d["id"], "titulo": f"{c.get('codigo')}: {tx(a.get('o_que'))}",
                            "detalhe": ("ATRASADA · " if atras else "") + (f"até {tx(a.get('quando'))}" if tx(a.get("quando")) else "sem prazo"),
                            "em": data_iso(a.get("quando")) or ""})
    out.sort(key=lambda p: p.get("em") or "")
    return {"pendencias": out}


# ─── sugestões vindas do Fluxo interno ───────────────────────────────────
@router.get("/sugestoes")
def sugestoes(request: Request):
    _u(request)
    procs = _processos()
    com_ind = {tx((d.get("dados") or {}).get("processo_id")) for d in _listar(COL_IND)}
    com_card = {tx((d.get("dados") or {}).get("processo_id")) for d in _listar(COL_CARD) if (d.get("dados") or {}).get("etapa") not in ("concluida", "cancelada")}
    out = []
    for p in procs:
        g = p["gaps"]
        if g["divergente"] or g["sem_it"] or g["nao_feito"]:
            out.append({"tipo": "gap", "processo_id": p["id"], "titulo": f"{p['codigo']} {p['nome']}",
                        "texto": f"{g['divergente']} passo(s) feito(s) diferente da IT · {g['sem_it']} sem IT · {g['nao_feito']} da IT não feito(s)",
                        "peso": g["divergente"] * 3 + g["sem_it"] * 2 + g["nao_feito"]})
        if p["horas_mes"] >= 10:
            out.append({"tipo": "tempo", "processo_id": p["id"], "titulo": f"{p['codigo']} {p['nome']}",
                        "texto": f"Consome {p['horas_mes']:.0f} h/mês (tempo × vezes por mês em Como é feito hoje)", "peso": p["horas_mes"] / 5})
        if not p["tem_it"] and p["passos"]:
            out.append({"tipo": "sem_it", "processo_id": p["id"], "titulo": f"{p['codigo']} {p['nome']}",
                        "texto": "Feito sem Instrução de Trabalho — cada um faz de um jeito", "peso": 4})
        if p["id"] not in com_ind and p["passos"]:
            out.append({"tipo": "sem_indicador", "processo_id": p["id"], "titulo": f"{p['codigo']} {p['nome']}",
                        "texto": "Sem indicador: hoje não dá para dizer se está bom ou ruim", "peso": 1})
    for s in out:
        s["ja_tem_melhoria"] = s["processo_id"] in com_card
    out.sort(key=lambda s: -s["peso"])
    return {"sugestoes": out[:80]}


# ─── IA: sugerir indicador ───────────────────────────────────────────────
class PedidoIA(BaseModel):
    processo_id: str
    ganhos: List[str] = []
    descricao: str = ""


SUGESTOES_PADRAO = {
    "tempo": {"nome": "Tempo médio por execução", "o_que_mede": "Minutos do início ao fim de cada execução", "unidade": "min", "sentido": "menor"},
    "dinheiro": {"nome": "Custo por unidade", "o_que_mede": "Quanto se gasta por item/pedido/execução", "unidade": "R$", "sentido": "menor"},
    "qualidade": {"nome": "% com erro ou retrabalho", "o_que_mede": "Execuções com erro, devolução ou retrabalho ÷ total", "unidade": "%", "sentido": "menor"},
    "organizacao": {"nome": "Transferências entre pessoas", "o_que_mede": "Quantas vezes o trabalho passa de mão em mão por execução", "unidade": "qtd", "sentido": "menor"},
}


@router.post("/ia/indicador")
def ia_indicador(payload: PedidoIA, request: Request):
    _u(request)
    p = next((x for x in _processos() if x["id"] == payload.processo_id), None)
    if not p:
        raise HTTPException(404, "Processo não encontrado.")
    padrao = [dict(SUGESTOES_PADRAO[g], frequencia="semanal") for g in (payload.ganhos or ["tempo", "qualidade"]) if g in SUGESTOES_PADRAO]
    if not H.get("ia_ligada", lambda: False)():
        return {"sugestoes": padrao, "fonte": "padrao"}
    texto = H["ia"]("""Você ajuda a escolher INDICADORES de processo para a King Star Colchões (português do Brasil).
Responda SOMENTE com JSON: {"sugestoes":[{"nome":"...","o_que_mede":"...","unidade":"%|min|R$|qtd|h","sentido":"maior|menor","frequencia":"diaria|semanal|mensal"}]}
Dê de 2 a 3 indicadores simples, que alguém consiga medir sem sistema novo (contando, cronometrando ou tirando de relatório).""",
                    f"PROCESSO: {p['codigo']} {p['nome']} (setor {p['area']})\nPASSOS: {p['passos']} · HORAS/MÊS: {p['horas_mes']}\n"
                    f"GANHO ESPERADO: {', '.join(payload.ganhos) or 'não informado'}\nO QUE SE QUER MELHORAR: {tx(payload.descricao) or '—'}")
    try:
        j = json.loads(re.search(r"\{.*\}", texto or "", re.S).group(0))
        sug = [{k: tx(s.get(k)) for k in ("nome", "o_que_mede", "unidade", "sentido", "frequencia")} for s in j.get("sugestoes") or [] if tx(s.get("nome"))]
        if sug:
            return {"sugestoes": sug[:3], "fonte": "ia"}
    except Exception:   # noqa: BLE001
        pass
    return {"sugestoes": padrao, "fonte": "padrao"}
