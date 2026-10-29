"""
qualidade.py — módulo QUALIDADE (o Mapa de Entregas sem papel)

Fluxo (cada etapa grava quem fez, no login da pessoa)
  1. PCL importa o PDF do Mapa de Entregas (fonte da verdade). Cada PV é
     procurado na BASE da Carteira (todas as fotos, da mais recente para trás):
     PV fora da base ou itens que não batem viram ALERTA.
  2. MONTAGEM DO PALETE (carga = palete): leitura do código (EAN/QR) ou, enquanto
     o SKU não tem EAN, confirmação tocando. Falta / item a mais exigem motivo e
     viram PENDÊNCIA no PCL.
  3. CONFERÊNCIA NA DOCA: OUTRA pessoa lê tudo de novo; o sistema compara com a
     montagem. Bateu → carga VALIDADA. Não bateu → pendência no PCL e a carga só
     é validada depois de resolvida.
  4. PCL resolve as pendências: resolvido sem novo PDF, refazer a etapa, ou pedir
     NOVO PDF (item sai / entra). O novo PDF confronta o que foi pedido e a carga
     volta para a montagem, inteira.
  5. RETORNO: lista de todas as cargas validadas e ainda não baixadas. Por
     cliente: entregue ou retornou; por peça: motivo, estado e DESTINO (CD, AS,
     fornecedor, perda…). Concluído o retorno, a carga vai para o PCL apontar no
     ERP; o PCL marca "apontado" e a carga é BAIXADA (2 passos).
  6. GAP: (a) trocas do Mapa — o sistema e o conferente apontam, o analista
     registra a classificação real; (b) toda peça retornada entra para ser
     TABULADA (classificação real, responsável, destino final).

Coleções do Firestore (prefixo qld_):
  qld_cargas      1 documento por carga (número do Protheus)
  qld_cadastros   "produtos" (SKU → EAN/QR) · "placas" (placa → motorista) · "listas"

Economia do plano grátis: as cargas recentes e as abertas ficam em memória (lidas
uma vez); toda gravação passa por aqui e atualiza a memória.
"""
import base64
import gzip
import hashlib
import json
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db_diario as db

MODULO = "qualidade"
COL_CARGAS = "qld_cargas"
COL_CAD = "qld_cadastros"
DIAS_EM_MEMORIA = 60

router = APIRouter(prefix="/api/qualidade")

# funções do main.py (login e permissão por aba) — ligadas em ligar()
H: dict = {}


def ligar(app, usuario_atual: Callable, exigir_aba: Callable, nivel_aba: Callable) -> None:
    H.update(usuario_atual=usuario_atual, exigir_aba=exigir_aba, nivel_aba=nivel_aba)
    app.include_router(router)


# ══════════════════════════════════════════════════════════════════════════
# Persistência (trocável nos testes)
# ══════════════════════════════════════════════════════════════════════════
def _fs_get(col: str, doc_id: str) -> Optional[dict]:
    s = db.get_db().collection(col).document(doc_id).get(timeout=db.TIMEOUT_FS)
    return s.to_dict() if s.exists else None


def _fs_set(col: str, doc_id: str, dados: dict) -> None:
    db.get_db().collection(col).document(doc_id).set(dados, timeout=db.TIMEOUT_FS)


def _fs_periodo(col: str, campo: str, de: str, ate: str) -> list:
    q = db.get_db().collection(col).where(campo, ">=", de).where(campo, "<=", ate)
    return [d.to_dict() for d in q.stream(timeout=db.TIMEOUT_FS)]


def _fs_status_abertos(col: str) -> list:
    q = db.get_db().collection(col).where("aberta", "==", True)
    return [d.to_dict() for d in q.stream(timeout=db.TIMEOUT_FS)]


# ══════════════════════════════════════════════════════════════════════════
# utilidades
# ══════════════════════════════════════════════════════════════════════════
def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def hoje() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def sem(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip().upper()


def num_chave(pv) -> str:
    d = re.sub(r"\D", "", str(pv or "")).lstrip("0")
    return d or str(pv or "").strip()


def data_iso(br: str) -> str:
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", str(br or "").strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else hoje()


def iso_ok(s: Optional[str], padrao: str) -> str:
    return s if s and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else padrao


def quem(request: Request) -> dict:
    u = H["usuario_atual"](request)
    return {"por": u.get("nome") or u.get("email"), "email": u.get("email")}


def exigir(request: Request, abas, minimo: int, acao: str) -> None:
    H["exigir_aba"](request, MODULO, abas, minimo, acao)


def nivel(request: Request, aba: str) -> int:
    try:
        return int(H["nivel_aba"](request, MODULO, aba))
    except Exception:
        return 0


TRAVA = threading.RLock()   # reentrante: as rotas seguram a trava e chamam carga() → _carregar()


# ══════════════════════════════════════════════════════════════════════════
# CADASTROS (SKU → EAN/QR · placa → motorista · listas)
# ══════════════════════════════════════════════════════════════════════════
LISTAS_PADRAO = {
    "classificacoes": ["Avaria de transporte", "Defeito de fabricação (troca autorizada pelo fabricante)", "Liberalidade",
                       "Adaptação / conforto", "Troca não efetuada", "Sem defeito / sem avaria", "Outro"],
    "responsaveis": ["Transportadora / motorista", "CD (montagem / manuseio)", "Fábrica / fornecedor", "Cliente",
                     "Comercial (liberalidade)", "Outro"],
    "avarias": ["Rasgado", "Amassado", "Sujo / manchado", "Molhado", "Embalagem violada", "Quebrado", "Outro"],
    "destinos": ["CD — volta ao estoque", "AS — assistência (recuperar)", "Fornecedor / fábrica", "Perda / descarte", "Outro"],
    "motivos_retorno": ["Cliente ausente", "Cliente recusou", "Avaria no transporte", "Endereço não localizado",
                        "Produto errado / divergente", "Falta de tempo na rota", "Outro"],
    "motivos_divergencia": ["Item não encontrado no estoque", "Item avariado no carregamento", "Erro de separação",
                            "Item a mais (não é desta carga)", "Código ilegível", "Outro"],
    "motivos_ajuste": ["Item avariado", "Falta de estoque", "Erro de separação", "Pedido alterado", "Cliente pediu", "Outro"],
    "clientes_transferencia": "0NG5EE",
    # FASE 1 (False): a carga importada vai DIRETO para o Retorno. FASE 2 (True): montagem → doca → retorno.
    "exigir_conferencia": False,
}
CAD: dict = {"produtos": None, "placas": None, "listas": None}


def _cad(nome: str) -> dict:
    if CAD[nome] is None:
        d = _fs_get(COL_CAD, nome) or {}
        if nome == "listas":
            d = {**LISTAS_PADRAO, **{k: v for k, v in d.items() if k in LISTAS_PADRAO}}
        else:
            d = {"itens": d.get("itens") or {}}
        CAD[nome] = d
    return CAD[nome]


def _cad_salvar(nome: str) -> None:
    _fs_set(COL_CAD, nome, CAD[nome])


def produtos() -> dict:
    return _cad("produtos")["itens"]


def placas() -> dict:
    return _cad("placas")["itens"]


def listas() -> dict:
    return _cad("listas")


def indice_ean() -> dict:
    return {str(e): sku for sku, p in produtos().items() for e in (p.get("eans") or [])}


def tem_ean(sku: Optional[str]) -> bool:
    return bool(sku and (produtos().get(sku) or {}).get("eans"))


def exige_qr(sku: Optional[str]) -> bool:
    return bool(sku and (produtos().get(sku) or {}).get("qr"))


def exigir_conferencia() -> bool:
    return bool(listas().get("exigir_conferencia"))


def clientes_transf() -> set:
    return {x.strip().upper() for x in re.split(r"[,;\s]+", str(listas().get("clientes_transferencia") or "")) if x.strip()}


# ══════════════════════════════════════════════════════════════════════════
# BASE DA CARTEIRA — todas as fotos importadas, da mais recente para trás
# ══════════════════════════════════════════════════════════════════════════
CART = {"dias": None, "lidos_em": 0.0, "pv": {}, "lidas": {}}   # pv → registro mais recente · data → importado_em lido


def _cart_dias() -> list:
    if CART["dias"] is None or time.time() - CART["lidos_em"] > 300:
        CART["dias"] = sorted(db.cart_dias_listar(), key=lambda x: x.get("data") or "", reverse=True)
        CART["lidos_em"] = time.time()
    return CART["dias"]


def _descompactar(partes: list, formato: str):
    bruto = base64.b64decode("".join(partes))
    txt = bruto.decode("utf-8") if formato == "json" else gzip.decompress(bruto).decode("utf-8")
    return json.loads(txt)


def _cart_ler_dia(dia: dict) -> None:
    data = dia.get("data")
    r = db.cart_dia_ler(data)
    if not r:
        return
    foto = _descompactar(r.get("partes") or [], (r.get("meta") or {}).get("formato") or "gzip")
    for p in foto.get("pedidos") or []:
        k = num_chave(p.get("p"))
        atual = CART["pv"].get(k)
        if atual and atual["data"] >= data:
            continue
        CART["pv"][k] = {"data": data, "chave": p.get("k"), "pedido": p.get("p"), "loja": p.get("l"),
                         "cliente": p.get("c"), "nome": p.get("n"),
                         "itens": [{"sku": str(i[1] or ""), "desc": str(i[2] or ""), "qtd": float(i[5] or 0)} for i in (p.get("it") or [])]}
    CART["lidas"][data] = dia.get("importado_em") or ""


def buscar_na_carteira(pvs) -> dict:
    """{pv: registro} — procura cada PV na base inteira (mais recente primeiro)."""
    faltam = {num_chave(p) for p in pvs if p}
    for dia in _cart_dias():
        data = dia.get("data")
        if CART["lidas"].get(data) != (dia.get("importado_em") or ""):   # dia novo, ou reimportado
            if data in CART["lidas"]:
                for k in [k for k, v in CART["pv"].items() if v["data"] == data]:
                    CART["pv"].pop(k, None)
            _cart_ler_dia(dia)
        if all(k in CART["pv"] for k in faltam):
            break
    return {k: CART["pv"][k] for k in faltam if k in CART["pv"]}


def casar_itens(itens_mapa: list, itens_cart: list) -> tuple:
    """Liga cada item do Mapa ao item da Carteira (SKU). Devolve (itens com sku, alertas)."""
    pool = [{**i, "resta": i["qtd"]} for i in itens_cart]
    saida, alertas = [], []
    for it in itens_mapa:
        dm = sem(it["desc"])
        cand = [c for c in pool if c["resta"] > 0 and (sem(c["desc"]) == dm)] or \
               [c for c in pool if c["resta"] > 0 and (sem(c["desc"]).startswith(dm) or (len(sem(c["desc"])) >= 8 and dm.startswith(sem(c["desc"]))))]
        if not cand:
            saida.append({**it, "sku": None})
            alertas.append(f"Item do Mapa não está no PV da Carteira: {it['desc']} ×{it['qtd']}")
            continue
        c = cand[0]
        usa = min(c["resta"], it["qtd"])
        c["resta"] -= usa
        if it["qtd"] > usa:
            alertas.append(f"Quantidade no Mapa ({it['qtd']}) maior que na Carteira ({int(usa)}): {it['desc']}")
        saida.append({**it, "sku": c["sku"], "desc_carteira": c["desc"]})
    for c in pool:
        if c["resta"] > 0 and c["resta"] < c["qtd"]:
            alertas.append(f"Carteira tem {int(c['qtd'])} e o Mapa só {int(c['qtd'] - c['resta'])}: {c['sku']} {c['desc']}")
        elif c["resta"] > 0:
            alertas.append(f"Item do PV na Carteira que não está no Mapa: {c['sku']} {c['desc']} ×{int(c['resta'])}")
    return saida, alertas


# ══════════════════════════════════════════════════════════════════════════
# CARGAS em memória
# ══════════════════════════════════════════════════════════════════════════
CARGAS: dict = {}
_CARREGADO = {"ok": False, "desde": ""}


def _carregar() -> None:
    if _CARREGADO["ok"]:
        return
    with TRAVA:
        if _CARREGADO["ok"]:
            return
        desde = (datetime.now() - timedelta(days=DIAS_EM_MEMORIA)).strftime("%Y-%m-%d")
        for c in _fs_periodo(COL_CARGAS, "data_iso", desde, "9999-12-31") + _fs_status_abertos(COL_CARGAS):
            if c and c.get("numero"):
                CARGAS[c["numero"]] = migrar(c)
        _CARREGADO.update(ok=True, desde=desde)


def no_periodo(de: str, ate: str) -> list:
    """Cargas com data do Mapa entre de e ate (lê do Firestore o que for mais antigo que a memória)."""
    _carregar()
    if de < _CARREGADO["desde"]:
        with TRAVA:
            for c in _fs_periodo(COL_CARGAS, "data_iso", de, min(ate, _CARREGADO["desde"])):
                if c and c.get("numero") and c["numero"] not in CARGAS:
                    CARGAS[c["numero"]] = migrar(c)
    return [c for c in CARGAS.values() if de <= (c.get("data_iso") or "") <= ate]


def migrar(c: dict) -> dict:
    """Cargas gravadas pela 1ª versão do módulo (conferencia/ajustes/"conferida") → formato atual."""
    if c and "montagem" not in c:
        conf = c.pop("conferencia", None)
        c["montagem"], c["doca"] = conf, None
        c["pendencias"] = [{**a, "origem": "manual", "pdf": a.get("tipo"), "status": "aguardando_pdf" if a.get("status") == "aberto" else a.get("status")}
                           for a in c.pop("ajustes", None) or []]
        c.setdefault("etapas_anteriores", [])
        c.setdefault("apontamento", None)
        c.pop("conferencias_anteriores", None)
        if c.get("status") in ("conferida", "validada") and not (c.get("retorno") or {}).get("itens"):
            c["status"], c["validacao"] = "importada", None
        if c.get("status") == "conferida":
            c["status"] = "importada"
        recalcular(c)
    elif c and c.get("status") == "importada" and not c.get("montagem") and not exigir_conferencia():
        recalcular(c)   # fase 1: carga importada antes da chave → vai para o Retorno
    return c


def carga(numero: str) -> dict:
    _carregar()
    c = CARGAS.get(numero)
    if c is None:
        c = _fs_get(COL_CARGAS, numero)
        if not c:
            raise HTTPException(status_code=404, detail=f"Carga {numero} não encontrada.")
        CARGAS[numero] = c
    return migrar(c)


def gravar(c: dict) -> None:
    c["aberta"] = c.get("status") != "baixada"
    c["atualizado_em"] = agora()
    c["resumo"] = resumo(c)
    _fs_set(COL_CARGAS, c["numero"], c)
    CARGAS[c["numero"]] = c


def hist(c: dict, q: dict, acao: str, det: str = "") -> None:
    c.setdefault("historico", []).append({"em": agora(), "por": q["por"], "email": q["email"], "acao": acao, "det": det[:400]})
    c["historico"] = c["historico"][-400:]


STATUS = {
    "importada": "Aguardando montagem", "em_montagem": "Em montagem", "montagem_div": "Montagem com divergência (PCL)",
    "montada": "Montada — aguardando doca", "em_conferencia": "Em conferência na doca", "doca_div": "Doca com divergência (PCL)",
    "aguardando_pdf": "Aguardando novo PDF", "validada": "Validada — aguardando retorno", "em_retorno": "Em retorno",
    "retorno_concluido": "Retorno concluído — apontar no ERP", "baixada": "Baixada",
}
FASE_RETORNO = ("validada", "em_retorno", "retorno_concluido", "baixada")


def _etapa_pub(e: Optional[dict]) -> Optional[dict]:
    if not e:
        return None
    fim = e.get("fim") or {}
    leitores = sorted({v.get("por") for v in (e.get("leituras") or {}).values() if v.get("por")})
    return {"por": e.get("por"), "inicio": e.get("inicio"), "leitores": leitores, "lidas": len(e.get("leituras") or {}),
            "fim_em": fim.get("em"), "fim_por": fim.get("por"), "ok": fim.get("ok"), "divergencias": len(fim.get("divergencias") or [])}


def resumo(c: dict) -> dict:
    un = c.get("unidades") or []
    mt, dc, ret = c.get("montagem") or {}, c.get("doca") or {}, c.get("retorno") or {}
    peds = c.get("pedidos") or []
    trocas = [p for p in peds if p.get("troca")]
    itens_ret = ret.get("itens") or []
    nao_ent = {num_chave(x["pv"]) for x in itens_ret if x.get("origem") == "nao_entregue"}
    return {
        "pedidos": len(peds), "unidades": len(un),
        "montadas": len(mt.get("leituras") or {}), "conferidas": len(dc.get("leituras") or {}),
        "por_codigo": sum(1 for x in (mt.get("leituras") or {}).values() if x.get("modo") in ("ean", "qr")),
        "alertas": sum(len(p.get("alertas") or []) for p in peds),
        "pv_fora": sum(1 for p in peds if not (p.get("carteira") or {}).get("achado")),
        "trocas": len(trocas), "gap_pendentes": sum(1 for p in trocas if gap_flags(p) and not (p.get("gap") or {}).get("analise")),
        "pend_abertas": sum(1 for a in c.get("pendencias") or [] if a.get("status") == "aberta"),
        "pend_pdf": sum(1 for a in c.get("pendencias") or [] if a.get("status") == "aguardando_pdf"),
        "retornadas": sum(1 for x in itens_ret if x.get("origem") == "nao_entregue"),
        "retiradas": sum(1 for x in itens_ret if x.get("origem") == "retirada"),
        "avariadas": sum(1 for x in itens_ret if x.get("estado") == "avariada"),
        "pedidos_nao_entregues": len(nao_ent),
        "a_tabular": sum(1 for x in itens_ret if not x.get("tabulacao")),
    }


def publico(c: dict) -> dict:
    ret, ap = c.get("retorno") or {}, c.get("apontamento") or {}
    return {k: c.get(k) for k in ("numero", "data_mapa", "data_iso", "placa", "transportadora", "motorista", "cubagem",
                                   "status", "versao", "transferencia", "atualizado_em")} | {
        "resumo": c.get("resumo") or resumo(c), "montagem": _etapa_pub(c.get("montagem")), "doca": _etapa_pub(c.get("doca")),
        "validacao": c.get("validacao"),
        "retorno": {"por": ret.get("por"), "inicio": ret.get("inicio"), "fim_em": (ret.get("fim") or {}).get("em"),
                    "fim_por": (ret.get("fim") or {}).get("por")} if ret else None,
        "apontamento": ap or None,
    }


def gap_flags(p: dict) -> list:
    g = p.get("gap") or {}
    return list(g.get("flags") or []) + (["conferente"] if g.get("conferente") else [])


# ══════════════════════════════════════════════════════════════════════════
# PENDÊNCIAS (PCL) e avanço da carga
# ══════════════════════════════════════════════════════════════════════════
def recalcular(c: dict) -> None:
    """Decide o status da carga pelo estado das etapas e das pendências (não mexe na fase de retorno)."""
    if c.get("status") in FASE_RETORNO:
        return
    pend = c.get("pendencias") or []
    mt, dc = c.get("montagem"), c.get("doca")
    if any(a.get("status") == "aguardando_pdf" for a in pend):
        c["status"] = "aguardando_pdf"
    elif any(a.get("status") == "aberta" for a in pend):
        c["status"] = "doca_div" if dc and dc.get("fim") else "montagem_div"
    elif dc and dc.get("fim"):
        c["status"] = "validada"
        if not c.get("validacao"):
            fim = dc["fim"]
            c["validacao"] = {"em": agora(), "por": fim.get("por"), "email": fim.get("email"),
                              "como": "doca conferida" if fim.get("ok") else "pendências da doca resolvidas no PCL"}
    elif dc:
        c["status"] = "em_conferencia"
    elif mt and mt.get("fim") and exigir_conferencia():
        c["status"] = "montada"
    elif mt and not mt.get("fim") and exigir_conferencia():
        c["status"] = "em_montagem"
    elif not exigir_conferencia():
        c["status"] = "validada"   # FASE 1: sem montagem/doca, a carga importada já espera o retorno
        if not c.get("validacao"):
            c["validacao"] = {"em": agora(), "por": "", "email": "", "como": "fase 1 — importada direto para o retorno"}
    else:
        c["status"] = "importada"


def _nova_pend(origem: str, tipo: str, pv: str, desc: str, qtd: int, motivo: str, obs: str, q: dict, status: str = "aberta", **extra) -> dict:
    return {"id": uuid.uuid4().hex[:8], "origem": origem, "tipo": tipo, "pv": pv or "", "desc": (desc or "")[:200], "qtd": int(qtd or 1),
            "motivo": (motivo or "")[:120], "obs": (obs or "")[:300], "por": q["por"], "email": q["email"], "em": agora(), "status": status, **extra}


def _arquivar_etapa(c: dict, nome: str, motivo: str) -> None:
    e = c.get(nome)
    if e:
        c.setdefault("etapas_anteriores", []).append({**e, "etapa": nome, "versao": c.get("versao"), "arquivada_em": agora(), "motivo_arquivo": motivo})
        c["etapas_anteriores"] = c["etapas_anteriores"][-20:]
    c[nome] = None


# ══════════════════════════════════════════════════════════════════════════
# IMPORTAÇÃO do Mapa (com versões e confronto dos pedidos de novo PDF)
# ══════════════════════════════════════════════════════════════════════════
def _assinatura(peds: list) -> str:
    base = [(p.get("pv"), p.get("pv_retira"), [(sem(i["desc"]), i["qtd"]) for i in p.get("entregar") or []],
             [(sem(i["desc"]), i["qtd"]) for i in p.get("col2") or []]) for p in peds]
    return hashlib.sha1(json.dumps(base, sort_keys=True).encode()).hexdigest()[:16]


def _flags_gap(p: dict, obs_repetidas: set) -> list:
    if not p.get("troca"):
        return []
    f = []
    pr = p.get("protocolo")
    if not pr:
        f.append("sem_protocolo")
    else:
        tipo, mot = sem(pr.get("tipo")), sem(pr.get("motivo"))
        if not tipo:
            f.append("sem_tipo")
        if "SEM DEFEITO" in mot and re.search(r"AVARIA|FABRICANTE|AUTORIZADA", tipo):
            f.append("contraditoria")
    if any(sem(o) in obs_repetidas for o in p.get("obs") or [] if not sem(o).startswith("OBSERVACAO")):
        f.append("obs_padrao")
    return f


def _unidades(peds: list) -> list:
    un, n = [], 0
    for p in peds:
        for i, it in enumerate(p.get("entregar") or []):
            for u in range(int(it.get("qtd") or 0)):
                n += 1
                un.append({"id": f"u{n}", "pv": p.get("pv"), "seq": p.get("seq"), "i": i, "u": u + 1, "de": int(it.get("qtd") or 0),
                           "sku": it.get("sku"), "desc": it.get("desc")})
    return un


def _qtd_por_item(peds: list) -> dict:
    d: dict = {}
    for p in peds:
        for it in p.get("entregar") or []:
            k = (num_chave(p.get("pv")), sem(it["desc"]))
            d[k] = d.get(k, 0) + int(it.get("qtd") or 0)
    return d


def _confrontar(c: dict, antigos: list, novos: list) -> list:
    """Confere cada pendência 'aguardando novo PDF' contra a nova versão; devolve as mudanças não solicitadas."""
    va, vn = _qtd_por_item(antigos), _qtd_por_item(novos)

    def acha(d, pv, desc):
        k, ds = num_chave(pv), sem(desc)
        return sum(v for (p, s), v in d.items() if p == k and (s == ds or s.startswith(ds) or ds.startswith(s)))
    explicadas = set()
    for a in c.get("pendencias") or []:
        if a.get("status") == "aguardando_pdf":
            antes, depois = acha(va, a["pv"], a["desc"]), acha(vn, a["pv"], a["desc"])
            ok = depois <= antes - int(a["qtd"]) if a.get("pdf") == "sair" else depois >= antes + int(a["qtd"])
            a.update(status="confirmado" if ok else "nao_executado", verificado_em=agora(), versao_verificada=c.get("versao"),
                     nota=f"Antes {antes}, depois {depois} no novo PDF.")
            explicadas.add((num_chave(a["pv"]), sem(a["desc"])))
        elif a.get("status") == "aberta":
            a.update(status="superada", nota="Novo PDF importado antes de resolver — a carga volta para a montagem.", verificado_em=agora())
    mud = []
    for k in set(va) | set(vn):
        if va.get(k, 0) != vn.get(k, 0) and not any(k[0] == e[0] and (k[1].startswith(e[1]) or e[1].startswith(k[1])) for e in explicadas):
            mud.append({"pv": k[0], "desc": k[1], "antes": va.get(k, 0), "depois": vn.get(k, 0)})
    return mud


class Importacao(BaseModel):
    data_mapa: str
    hora: Optional[str] = ""
    arquivo: Optional[str] = ""
    cargas: list


def importar_mapa(payload: dict, q: dict) -> dict:
    _carregar()
    cargas_in = payload.get("cargas") or []
    if not cargas_in:
        raise HTTPException(status_code=400, detail="Nenhuma carga encontrada no arquivo.")
    todos_pvs = set()
    for c in cargas_in:
        for p in c.get("pedidos") or []:
            todos_pvs.update(x for x in (p.get("pv"), p.get("pv_retira")) if x)
    try:
        base = buscar_na_carteira(todos_pvs)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Não consegui ler a base da Carteira ({e.__class__.__name__}: {e}). Nada foi gravado.")
    cont: dict = {}   # observações repetidas no arquivo (texto padrão do cadastro) → GAP
    for c in cargas_in:
        for p in c.get("pedidos") or []:
            for o in set(sem(x) for x in p.get("obs") or [] if not sem(x).startswith("OBSERVACAO")):
                cont[o] = cont.get(o, 0) + 1
    repetidas = {o for o, n in cont.items() if n >= 3}
    transf = clientes_transf()
    rel = {"novas": [], "versoes": [], "iguais": [], "bloqueadas": [], "alertas": 0, "pv_fora": 0}
    with TRAVA:
        for ci in cargas_in:
            num = str(ci.get("numero") or "").strip()
            if not re.fullmatch(r"\d{1,12}", num):
                continue
            peds = []
            for p in ci.get("pedidos") or []:
                p = {k: p.get(k) for k in ("seq", "tipo", "troca", "pv", "nf", "serie", "pv_retira", "nf_troca", "serie_troca",
                                          "cliente", "nome", "endereco", "compl", "bairro", "cidade", "cep", "origem", "despesas",
                                          "entregar", "col2", "pseudo", "obs", "protocolo")}
                p["entregar"] = [{"desc": str(i.get("desc") or "")[:200], "qtd": int(i.get("qtd") or 0)} for i in p.get("entregar") or []]
                p["col2"] = [{"desc": str(i.get("desc") or "")[:200], "qtd": int(i.get("qtd") or 0)} for i in p.get("col2") or []]
                alertas = []
                reg = base.get(num_chave(p.get("pv")))
                if reg:
                    p["entregar"], al = casar_itens(p["entregar"], reg["itens"])
                    alertas += al
                    p["carteira"] = {"achado": True, "data": reg["data"], "chave": reg["chave"], "cliente": reg.get("cliente")}
                else:
                    p["entregar"] = [{**i, "sku": None} for i in p["entregar"]]
                    p["carteira"] = {"achado": False}
                    alertas.append(f"PV {p.get('pv')} não está na base da Carteira.")
                if p.get("pv_retira"):
                    rr = base.get(num_chave(p["pv_retira"]))
                    if rr:
                        p["col2"], _ = casar_itens(p["col2"], rr["itens"])
                    else:
                        p["col2"] = [{**i, "sku": None} for i in p["col2"]]
                        alertas.append(f"PV de retirada {p['pv_retira']} não está na base da Carteira.")
                p["alertas"] = alertas
                p["transferencia"] = sem(p.get("cliente")) in transf
                p["gap"] = {"flags": _flags_gap(p, repetidas), "conferente": None, "analise": None}
                peds.append(p)
            assin = _assinatura(peds)
            existente = CARGAS.get(num) or _fs_get(COL_CARGAS, num)
            if existente and existente.get("versoes") and existente["versoes"][-1].get("assinatura") == assin:
                rel["iguais"].append(num)
                continue
            if existente:
                migrar(existente)
            if existente and existente.get("status") in FASE_RETORNO and (existente.get("status") != "validada" or (existente.get("retorno") or {}).get("itens")):
                rel["bloqueadas"].append(num)   # o retorno já começou: não troca a versão por baixo dele
                continue
            pl = str(ci.get("placa") or "").upper()
            mot = (placas().get(pl) or {}).get("motorista") or ""
            base_carga = {"numero": num, "data_mapa": payload.get("data_mapa") or "", "data_iso": data_iso(payload.get("data_mapa")),
                          "placa": pl, "transportadora": str(ci.get("transportadora") or ""), "motorista": mot,
                          "cubagem": float(ci.get("cubagem") or 0), "transferencia": bool(peds) and all(p["transferencia"] for p in peds)}
            if existente:
                c = existente
                antigos = c.get("pedidos") or []
                c.update(base_carga)
                c["versao"] = int(c.get("versao") or 1) + 1
                gap_ant = {num_chave(p.get("pv")): p.get("gap") for p in antigos}   # GAP já analisado continua valendo
                for p in peds:
                    g = gap_ant.get(num_chave(p.get("pv")))
                    if g and (g.get("analise") or g.get("conferente")):
                        p["gap"] = {**p["gap"], "conferente": g.get("conferente"), "analise": g.get("analise")}
                c["mudancas_nao_solicitadas"] = _confrontar({**c, "pedidos": peds}, antigos, peds)
                _arquivar_etapa(c, "doca", f"novo PDF (v{c['versao']})")
                _arquivar_etapa(c, "montagem", f"novo PDF (v{c['versao']})")
                c["pedidos"], c["unidades"], c["validacao"], c["retorno"] = peds, _unidades(peds), None, None
                c["status"] = "importada"
                c["versoes"].append({"v": c["versao"], "em": agora(), "por": q["por"], "arquivo": payload.get("arquivo") or "", "assinatura": assin})
                hist(c, q, "nova versão do Mapa", f"v{c['versao']} · {len(c['mudancas_nao_solicitadas'])} mudança(s) não solicitada(s) · volta para a montagem")
                recalcular(c)
                rel["versoes"].append(num)
            else:
                c = {**base_carga, "versao": 1, "status": "importada", "pedidos": peds, "unidades": _unidades(peds),
                     "montagem": None, "doca": None, "validacao": None, "pendencias": [], "retorno": None, "apontamento": None,
                     "mudancas_nao_solicitadas": [], "etapas_anteriores": [],
                     "versoes": [{"v": 1, "em": agora(), "por": q["por"], "arquivo": payload.get("arquivo") or "", "assinatura": assin}],
                     "historico": [], "criado_em": agora()}
                hist(c, q, "Mapa importado", payload.get("arquivo") or "")
                recalcular(c)
                rel["novas"].append(num)
            rel["alertas"] += sum(len(p["alertas"]) for p in peds)
            rel["pv_fora"] += sum(1 for p in peds if not p["carteira"]["achado"])
            gravar(c)
    return rel


# ══════════════════════════════════════════════════════════════════════════
# MONTAGEM e CONFERÊNCIA NA DOCA (a mesma leitura, em duas etapas)
# ══════════════════════════════════════════════════════════════════════════
ETAPAS = {"montagem": {"aba": "montagem", "nome": "montagem do palete", "pode": ("importada", "em_montagem")},
          "doca": {"aba": "conferencia", "nome": "conferência na doca", "pode": ("montada", "em_conferencia")}}


def _ped(c: dict, seq_ou_pv: str) -> Optional[dict]:
    return next((p for p in c.get("pedidos") or [] if p.get("seq") == seq_ou_pv or num_chave(p.get("pv")) == num_chave(seq_ou_pv)), None)


def _exigir_status(c: dict, *ok) -> None:
    if c.get("status") not in ok:
        raise HTTPException(status_code=409, detail=f"A carga {c['numero']} está “{STATUS.get(c.get('status'), c.get('status'))}” — esta ação não vale agora.")


def _montadores(c: dict) -> set:
    mt = c.get("montagem") or {}
    s = {v.get("email") for v in (mt.get("leituras") or {}).values()}
    s |= {mt.get("email"), (mt.get("fim") or {}).get("email")}
    return {x for x in s if x}


def _etapa(c: dict, nome: str, q: dict) -> dict:
    _exigir_status(c, *ETAPAS[nome]["pode"])
    if nome == "doca" and q["email"] in _montadores(c):
        raise HTTPException(status_code=403, detail="Você participou da montagem deste palete. A conferência na doca precisa ser feita por OUTRA pessoa.")
    if not c.get(nome):
        c[nome] = {"inicio": agora(), "por": q["por"], "email": q["email"], "leituras": {}, "sobras": [], "fim": None}
        c["status"] = "em_montagem" if nome == "montagem" else "em_conferencia"
        hist(c, q, f"{ETAPAS[nome]['nome']} iniciada", f"versão {c.get('versao')}")
    return c[nome]


def ler_codigo(c: dict, nome: str, q: dict, codigo: str, unidade: Optional[str], pv: Optional[str], qr: Optional[str]) -> dict:
    """Uma leitura na montagem ou na doca. Devolve o que a tela deve fazer."""
    et = _etapa(c, nome, q)
    lidas = et["leituras"]
    un = c.get("unidades") or []
    if unidade and not codigo:   # confirmação tocando (só SKU sem EAN cadastrado)
        alvo = next((x for x in un if x["id"] == unidade), None)
        if not alvo:
            raise HTTPException(status_code=404, detail="Unidade não encontrada nesta carga.")
        if alvo["id"] in lidas:
            return {"resultado": "ja_lida", "unidade": alvo}
        if tem_ean(alvo.get("sku")):
            return {"resultado": "exige_codigo", "unidade": alvo, "mensagem": "Este produto já tem EAN cadastrado: leia o código de barras."}
        lidas[alvo["id"]] = {"modo": "manual", "codigo": "", "por": q["por"], "email": q["email"], "em": agora()}
        gravar(c)
        return {"resultado": "ok", "unidade": alvo, "modo": "manual"}
    codigo = re.sub(r"\s+", "", str(codigo or ""))
    if not codigo:
        raise HTTPException(status_code=400, detail="Leitura vazia.")
    sku = indice_ean().get(codigo)
    if not sku:
        opcoes = sorted({(x["sku"], x["desc"]) for x in un if x.get("sku") and not tem_ean(x["sku"])})
        return {"resultado": "desconhecido", "codigo": codigo, "opcoes": [{"sku": s, "desc": d} for s, d in opcoes]}
    livres = [x for x in un if x.get("sku") == sku and x["id"] not in lidas]
    if pv:
        livres = [x for x in livres if num_chave(x["pv"]) == num_chave(pv)]
    if not livres:
        desc = (produtos().get(sku) or {}).get("desc") or sku
        et["sobras"].append({"id": uuid.uuid4().hex[:8], "codigo": codigo, "sku": sku, "desc": desc, "por": q["por"], "email": q["email"], "em": agora()})
        gravar(c)
        tem_na_carga = any(x.get("sku") == sku for x in un)
        return {"resultado": "sobra", "codigo": codigo, "sku": sku, "desc": desc,
                "mensagem": ("Todas as unidades deste produto já foram lidas — é uma unidade A MAIS." if tem_na_carga else "Este produto NÃO é desta carga.")}
    pvs = sorted({num_chave(x["pv"]) for x in livres})
    if len(pvs) > 1:
        ops = []
        for k in pvs:
            p = _ped(c, k) or {}
            ops.append({"pv": p.get("pv"), "seq": p.get("seq"), "nome": p.get("nome"), "bairro": p.get("bairro"), "cidade": p.get("cidade"),
                        "faltam": sum(1 for x in livres if num_chave(x["pv"]) == k)})
        return {"resultado": "escolher_pedido", "codigo": codigo, "sku": sku, "opcoes": ops}
    alvo = livres[0]
    if exige_qr(sku):
        qr = re.sub(r"\s+", "", str(qr or ""))
        if not qr:
            return {"resultado": "exige_qr", "codigo": codigo, "sku": sku, "pv": alvo["pv"], "mensagem": "Este produto exige a leitura do QR da unidade."}
        if any(x.get("qr") == qr for x in lidas.values()):
            return {"resultado": "qr_repetido", "mensagem": "Este QR já foi lido nesta carga."}
    lidas[alvo["id"]] = {"modo": "qr" if qr else "ean", "codigo": codigo, "qr": qr or "", "por": q["por"], "email": q["email"], "em": agora()}
    gravar(c)
    return {"resultado": "ok", "unidade": alvo, "modo": "ean"}


def _divergencias(c: dict, nome: str) -> list:
    et = c[nome]
    lidas = et["leituras"]
    un = c.get("unidades") or []
    if nome == "montagem":
        esperadas = {x["id"] for x in un}
    else:   # a doca confere o que foi MONTADO
        esperadas = set((c.get("montagem") or {}).get("leituras") or {})
    por_id = {x["id"]: x for x in un}
    div: dict = {}

    def soma(tipo, pv, desc, extra=None):
        k = f"{tipo}|{pv}|{desc}"
        d = div.setdefault(k, {"chave": k, "tipo": tipo, "pv": pv, "desc": desc, "qtd": 0, **(extra or {})})
        d["qtd"] += 1
    for uid in sorted(esperadas - set(lidas)):
        x = por_id.get(uid) or {}
        soma("falta", x.get("pv", ""), x.get("desc", ""))
    if nome == "doca":
        for uid in sorted(set(lidas) - esperadas):   # lida na doca mas não montada
            x = por_id.get(uid) or {}
            soma("sobra", x.get("pv", ""), x.get("desc", ""), {"nota": "lida na doca e não na montagem"})
    for s in et.get("sobras") or []:
        soma("sobra", "", s["desc"], {"codigo": s["codigo"]})
    return list(div.values())


def finalizar_etapa(c: dict, nome: str, q: dict, respostas: list, obs: str) -> dict:
    et = _etapa(c, nome, q)
    todas = _divergencias(c, nome)
    resp = {d.get("chave"): d for d in respostas or []}
    if [d for d in todas if not (resp.get(d["chave"]) or {}).get("motivo")]:
        return {"resultado": "precisa_motivo", "divergencias": todas}
    for d in todas:
        d["motivo"] = resp[d["chave"]]["motivo"][:120]
        d["obs"] = str(resp[d["chave"]].get("obs") or "")[:300]
    et["fim"] = {"em": agora(), "por": q["por"], "email": q["email"], "ok": not todas, "divergencias": todas, "obs": str(obs or "")[:500]}
    origem = nome
    for d in todas:
        c.setdefault("pendencias", []).append(_nova_pend(origem, d["tipo"], d["pv"], d["desc"], d["qtd"], d["motivo"], d["obs"], q))
    rotulo = "palete OK" if nome == "montagem" else "doca OK — carga validada"
    hist(c, q, rotulo if not todas else f"{ETAPAS[nome]['nome']} fechada com divergência", f"{len(todas)} divergência(s) → PCL" if todas else "")
    recalcular(c)
    gravar(c)
    return {"resultado": "ok", "divergencias": todas, "status": c["status"]}


# ══════════════════════════════════════════════════════════════════════════
# RETORNO
# ══════════════════════════════════════════════════════════════════════════
def _candidatos_retorno(c: dict, origem: str) -> list:
    ret = c.get("retorno") or {"itens": []}
    cand = []
    for p in c.get("pedidos") or []:
        lista = p.get("entregar") if origem == "nao_entregue" else (p.get("col2") if p.get("troca") else [])
        for i, it in enumerate(lista or []):
            ja = sum(1 for r in ret["itens"] if r["origem"] == origem and r["pv"] == p.get("pv") and r["i"] == i)
            if ja < int(it.get("qtd") or 0):
                cand.append({"pv": p.get("pv"), "seq": p.get("seq"), "i": i, "sku": it.get("sku"), "desc": it.get("desc"),
                             "nome": p.get("nome"), "bairro": p.get("bairro"), "faltam": int(it.get("qtd") or 0) - ja})
    return cand


def ler_retorno(c: dict, q: dict, dados: dict) -> dict:
    _exigir_status(c, "validada", "em_retorno")
    L = listas()
    origem = dados.get("origem") if dados.get("origem") in ("nao_entregue", "retirada") else "nao_entregue"
    codigo = re.sub(r"\s+", "", str(dados.get("codigo") or ""))
    cand = _candidatos_retorno(c, origem)
    if codigo:
        sku = indice_ean().get(codigo)
        if not sku:
            return {"resultado": "desconhecido", "codigo": codigo, "mensagem": "EAN não cadastrado. Escolha o item na lista."}
        cand = [x for x in cand if x.get("sku") == sku]
    elif dados.get("item"):
        pv_, i_ = str(dados["item"]).split("|", 1)
        cand = [x for x in cand if num_chave(x["pv"]) == num_chave(pv_) and str(x["i"]) == i_]
        if cand and tem_ean(cand[0].get("sku")) and not dados.get("sem_codigo_ok"):
            return {"resultado": "exige_codigo", "mensagem": "Este produto tem EAN cadastrado: leia o código de barras."}
    if dados.get("pv"):
        cand = [x for x in cand if num_chave(x["pv"]) == num_chave(dados["pv"])]
    if not cand:
        return {"resultado": "nao_pertence", "mensagem": "Não há unidade pendente deste produto nesta carga para esse tipo de retorno."}
    pvs = {num_chave(x["pv"]): x for x in cand}
    if len(pvs) > 1:
        return {"resultado": "escolher_pedido", "opcoes": [{"pv": x["pv"], "nome": x["nome"], "bairro": x["bairro"]} for x in pvs.values()]}
    x = cand[0]
    # dados da peça: estado, destino e (não entregue) motivo — a tela pergunta o que faltar
    estado = dados.get("estado") if dados.get("estado") in ("ok", "avariada") else ""
    falta = []
    if origem == "nao_entregue" and not dados.get("motivo"):
        falta.append("motivo")
    if not estado:
        falta.append("estado")
    if estado == "avariada" and not dados.get("avaria"):
        falta.append("avaria")
    if not dados.get("destino"):
        falta.append("destino")
    if falta:
        return {"resultado": "precisa_dados", "falta": falta, "item": x, "origem": origem,
                "listas": {"motivos": L["motivos_retorno"], "avarias": L["avarias"], "destinos": L["destinos"]}}
    ret = c.get("retorno") or {"inicio": agora(), "por": q["por"], "email": q["email"], "itens": [], "fim": None}
    c["retorno"] = ret
    item = {"id": uuid.uuid4().hex[:8], "pv": x["pv"], "seq": x["seq"], "i": x["i"], "sku": x.get("sku"), "desc": x["desc"], "nome": x.get("nome"),
            "origem": origem, "motivo": str(dados.get("motivo") or "")[:120] if origem == "nao_entregue" else "Retirada na troca",
            "estado": estado, "avaria": str(dados.get("avaria") or "")[:120] if estado == "avariada" else "",
            "destino": str(dados.get("destino") or "")[:120], "obs": str(dados.get("obs") or "")[:300],
            "modo": "ean" if codigo else "manual", "codigo": codigo, "por": q["por"], "email": q["email"], "em": agora(), "tabulacao": None}
    ret["itens"].append(item)
    if c["status"] == "validada":
        c["status"] = "em_retorno"
        hist(c, q, "retorno iniciado")
    gravar(c)
    return {"resultado": "ok", "item": item}


def situacao_pedidos(c: dict) -> list:
    """Por cliente: entregue / parcial / não entregue · trocas: retirada feita?"""
    ret = (c.get("retorno") or {})
    itens = ret.get("itens") or []
    sem_ret = set(ret.get("retirada_nao_feita") or [])
    out = []
    for p in c.get("pedidos") or []:
        tot = sum(int(i.get("qtd") or 0) for i in p.get("entregar") or [])
        volt = [x for x in itens if x["pv"] == p.get("pv") and x["origem"] == "nao_entregue"]
        retir = [x for x in itens if x["pv"] == p.get("pv") and x["origem"] == "retirada"]
        a_retirar = sum(int(i.get("qtd") or 0) for i in p.get("col2") or []) if p.get("troca") else 0
        sit = "entregue" if not volt else ("nao_entregue" if len(volt) >= tot else "parcial")
        out.append({"seq": p.get("seq"), "pv": p.get("pv"), "nf": p.get("nf"), "nome": p.get("nome"), "bairro": p.get("bairro"), "cidade": p.get("cidade"),
                    "troca": bool(p.get("troca")), "pv_retira": p.get("pv_retira"), "situacao": sit, "unidades": tot, "retornaram": len(volt),
                    "a_retirar": a_retirar, "retiradas": len(retir),
                    "retirada": ("feita" if retir else ("nao_feita" if p.get("seq") in sem_ret else ("pendente" if a_retirar else "")))})
    return out


# ══════════════════════════════════════════════════════════════════════════
# PAINEL (por período)
# ══════════════════════════════════════════════════════════════════════════
def _conta(d: dict, k, n: int = 1) -> None:
    k = k or "—"
    d[k] = d.get(k, 0) + n


def painel(de: str, ate: str) -> dict:
    cs = no_periodo(de, ate)
    st: dict = {}
    skus, skus_ean = set(), set()
    un_mont = un_cod = 0
    div = {"montagem": {"faltas": 0, "sobras": 0}, "doca": {"faltas": 0, "sobras": 0}, "por_motivo": {}}
    pend = {"aberta": 0, "aguardando_pdf": 0, "confirmado": 0, "nao_executado": 0, "resolvido": 0, "refazer": 0, "cancelado": 0, "superada": 0}
    trocas = gap_flag = gap_analis = gap_errado = 0
    flags: dict = {}
    ret = {"cargas": 0, "pedidos": 0, "pedidos_entregues": 0, "pedidos_parciais": 0, "pedidos_nao_entregues": 0,
           "pecas": 0, "retiradas": 0, "avariadas": 0, "tabuladas": 0,
           "por_motivo": {}, "por_destino": {}, "por_avaria": {}, "por_placa": {}, "por_transportadora": {}, "por_motorista": {},
           "por_produto": {}, "por_classificacao": {}, "por_responsavel": {}}
    pessoas: dict = {}
    for c in cs:
        _conta(st, c.get("status"))
        for x in c.get("unidades") or []:
            if x.get("sku"):
                skus.add(x["sku"])
                if tem_ean(x["sku"]):
                    skus_ean.add(x["sku"])
        for e in (c.get("etapas_anteriores") or []) + [dict(c.get("montagem") or {}, etapa="montagem"), dict(c.get("doca") or {}, etapa="doca")]:
            fim = e.get("fim")
            if not fim:
                continue
            for d in fim.get("divergencias") or []:
                div[e["etapa"]]["faltas" if d["tipo"] == "falta" else "sobras"] += int(d.get("qtd") or 0)
                _conta(div["por_motivo"], d.get("motivo"), int(d.get("qtd") or 0))
            for v in (e.get("leituras") or {}).values():
                p = pessoas.setdefault(v.get("por") or "—", {"montagem": 0, "doca": 0, "retorno": 0})
                p[e["etapa"]] += 1
        mt = c.get("montagem") or {}
        if mt.get("fim"):
            lid = mt.get("leituras") or {}
            un_mont += len(lid)
            un_cod += sum(1 for v in lid.values() if v.get("modo") in ("ean", "qr"))
        for p in c.get("pedidos") or []:
            if p.get("troca"):
                trocas += 1
                g = p.get("gap") or {}
                fl = gap_flags(p)
                if fl:
                    gap_flag += 1
                    for f in fl:
                        _conta(flags, f)
                if g.get("analise"):
                    gap_analis += 1
                    if g["analise"].get("mapa_correto") is False:
                        gap_errado += 1
        for a in c.get("pendencias") or []:
            _conta(pend, a.get("status", "aberta"))
        r = c.get("retorno") or {}
        if r:
            ret["cargas"] += 1
            for s in situacao_pedidos(c):
                ret["pedidos"] += 1
                ret[{"entregue": "pedidos_entregues", "parcial": "pedidos_parciais", "nao_entregue": "pedidos_nao_entregues"}[s["situacao"]]] += 1
        for it in r.get("itens") or []:
            ret["pecas" if it["origem"] == "nao_entregue" else "retiradas"] += 1
            pessoas.setdefault(it.get("por") or "—", {"montagem": 0, "doca": 0, "retorno": 0})["retorno"] += 1
            if it["origem"] == "nao_entregue":
                _conta(ret["por_motivo"], it.get("motivo"))
            _conta(ret["por_destino"], it.get("destino"))
            if it.get("estado") == "avariada":
                ret["avariadas"] += 1
                _conta(ret["por_avaria"], it.get("avaria"))
                _conta(ret["por_placa"], c.get("placa"))
                _conta(ret["por_transportadora"], c.get("transportadora"))
                _conta(ret["por_motorista"], c.get("motorista") or "(sem motorista)")
                _conta(ret["por_produto"], it.get("desc"))
            tb = it.get("tabulacao")
            if tb:
                ret["tabuladas"] += 1
                _conta(ret["por_classificacao"], tb.get("classificacao"))
                _conta(ret["por_responsavel"], tb.get("responsavel"))
    todos = produtos()
    return {
        "de": de, "ate": ate, "cargas": len(cs), "status": st, "nomes_status": STATUS,
        "alertas": sum((c.get("resumo") or resumo(c)).get("alertas", 0) for c in cs),
        "pv_fora": sum((c.get("resumo") or resumo(c)).get("pv_fora", 0) for c in cs),
        "ean": {"skus_nas_cargas": len(skus), "skus_com_ean": len(skus_ean), "faltam": len(skus - skus_ean),
                "pct_skus": round(100 * len(skus_ean) / len(skus), 1) if skus else 0,
                "unidades_montadas": un_mont, "unidades_por_codigo": un_cod,
                "pct_unidades_codigo": round(100 * un_cod / un_mont, 1) if un_mont else 0,
                "skus_com_qr": sum(1 for p in todos.values() if p.get("qr")), "cadastrados_total": sum(1 for p in todos.values() if p.get("eans"))},
        "divergencias": div, "pendencias": pend,
        "gap": {"trocas": trocas, "apontadas": gap_flag, "analisadas": gap_analis, "mapa_errado": gap_errado,
                "pct_gap": round(100 * gap_errado / gap_analis, 1) if gap_analis else 0, "por_tipo": flags},
        "retorno": ret, "pessoas": pessoas,
    }


# ══════════════════════════════════════════════════════════════════════════
# ROTAS
# ══════════════════════════════════════════════════════════════════════════
class Leitura(BaseModel):
    codigo: Optional[str] = ""
    unidade: Optional[str] = ""
    pv: Optional[str] = ""
    qr: Optional[str] = ""


class Finalizar(BaseModel):
    divergencias: list = []
    obs: Optional[str] = ""


class VincularEan(BaseModel):
    codigo: str
    sku: str


class Ajuste(BaseModel):
    tipo: str            # sair | entrar
    pv: str
    desc: str
    qtd: int = 1
    motivo: str
    obs: Optional[str] = ""


class PendAcao(BaseModel):
    acao: str            # resolver | novo_pdf | cancelar
    nota: Optional[str] = ""
    pdf: Optional[str] = ""   # sair | entrar (novo_pdf)


class Texto(BaseModel):
    texto: Optional[str] = ""


class GapAnalise(BaseModel):
    classificacao: str
    mapa_correto: bool
    fonte: Optional[str] = ""
    nota: Optional[str] = ""


class Tabulacao(BaseModel):
    classificacao: str
    responsavel: str
    destino: Optional[str] = ""
    nota: Optional[str] = ""


class Concluir(BaseModel):
    obs: Optional[str] = ""
    retirada_nao_feita: list = []


class Produto(BaseModel):
    sku: str
    desc: Optional[str] = ""
    eans: list = []
    qr: bool = False


class AddEan(BaseModel):
    sku: str
    codigo: str


class Placa(BaseModel):
    placa: str
    motorista: str


def _etapa_url(et: str) -> str:
    if et not in ETAPAS:
        raise HTTPException(status_code=404, detail="Etapa inválida.")
    return et


@router.get("/cargas")
def rota_cargas(request: Request, de: str = "", ate: str = "", abertas: bool = True):
    """Cargas do período (data do Mapa) + todas as que ainda não foram baixadas."""
    ate = iso_ok(ate, hoje())
    de = iso_ok(de, (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d"))
    vistos = {c["numero"]: c for c in no_periodo(de, ate)}
    if abertas:
        vistos.update({c["numero"]: c for c in CARGAS.values() if c.get("aberta")})
    return sorted([publico(c) for c in vistos.values()], key=lambda x: (x.get("data_iso") or "", x.get("numero") or ""), reverse=True)


@router.get("/cargas/{numero}")
def rota_carga(numero: str, request: Request):
    c = carga(numero)
    return {**c, "resumo": resumo(c), "situacao_pedidos": situacao_pedidos(c), "montadores": sorted(_montadores(c)), "nomes_status": STATUS}


@router.post("/importar")
def rota_importar(payload: Importacao, request: Request):
    exigir(request, "pcl", 3, "Importar o Mapa de Entregas")
    if len(json.dumps(payload.cargas, ensure_ascii=False)) > 3_000_000:
        raise HTTPException(status_code=413, detail="Arquivo grande demais.")
    return importar_mapa(payload.model_dump(), quem(request))


@router.post("/cargas/{numero}/{et}/leitura")
def rota_leitura(numero: str, et: str, payload: Leitura, request: Request):
    et = _etapa_url(et)
    exigir(request, ETAPAS[et]["aba"], 2, f"Registrar a {ETAPAS[et]['nome']}")
    with TRAVA:
        return ler_codigo(carga(numero), et, quem(request), payload.codigo, payload.unidade, payload.pv, payload.qr)


@router.post("/cargas/{numero}/{et}/desfazer")
def rota_desfazer(numero: str, et: str, payload: Leitura, request: Request):
    et = _etapa_url(et)
    exigir(request, [ETAPAS[et]["aba"], "pcl"], 2, "Desfazer leitura")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "em_montagem" if et == "montagem" else "em_conferencia")
        e = c[et]
        if payload.unidade and payload.unidade in e["leituras"]:
            if e["leituras"][payload.unidade].get("email") != q["email"] and nivel(request, "pcl") < 3:
                raise HTTPException(status_code=403, detail="Só quem leu (ou o PCL) pode desfazer esta leitura.")
            e["leituras"].pop(payload.unidade)
        elif payload.codigo:
            e["sobras"] = [s for s in e.get("sobras") or [] if s.get("id") != payload.codigo]
        gravar(c)
        return {"ok": True}


@router.post("/cargas/{numero}/{et}/finalizar")
def rota_finalizar(numero: str, et: str, payload: Finalizar, request: Request):
    et = _etapa_url(et)
    exigir(request, ETAPAS[et]["aba"], 2, f"Fechar a {ETAPAS[et]['nome']}")
    with TRAVA:
        return finalizar_etapa(carga(numero), et, quem(request), payload.divergencias, payload.obs)


@router.post("/cargas/{numero}/vincular-ean")
def rota_vincular(numero: str, payload: VincularEan, request: Request):
    """Na montagem/doca: código desconhecido → a pessoa diz de qual item da carga ele é (fica cadastrado no nome dela)."""
    exigir(request, ["montagem", "conferencia", "cadastros"], 2, "Vincular EAN")
    q = quem(request)
    codigo = re.sub(r"\s+", "", payload.codigo)
    if not re.fullmatch(r"[0-9A-Za-z\-]{6,40}", codigo):
        raise HTTPException(status_code=400, detail="Código inválido.")
    with TRAVA:
        c = carga(numero)
        if indice_ean().get(codigo):
            raise HTTPException(status_code=409, detail="Este código já está cadastrado em outro produto.")
        desc = next((x["desc"] for x in c.get("unidades") or [] if x.get("sku") == payload.sku), None)
        if desc is None:
            raise HTTPException(status_code=400, detail="Este SKU não está nesta carga.")
        p = produtos().setdefault(payload.sku, {"desc": desc, "eans": [], "qr": False})
        p["eans"] = sorted(set(p.get("eans") or []) | {codigo})
        p.update(desc=p.get("desc") or desc, por=q["por"], em=agora(), origem=f"carga {numero}")
        _cad_salvar("produtos")
        hist(c, q, "EAN vinculado", f"{codigo} → {payload.sku} {desc}")
        gravar(c)
        return {"ok": True}


@router.post("/cargas/{numero}/ajustes")
def rota_ajuste(numero: str, payload: Ajuste, request: Request):
    """Pedido de ajuste (item sai / entra) — vira pendência 'aguardando novo PDF' no PCL."""
    exigir(request, ["montagem", "conferencia", "pcl"], 2, "Solicitar ajuste")
    q = quem(request)
    if payload.tipo not in ("sair", "entrar") or payload.qtd < 1 or not payload.motivo.strip() or not payload.desc.strip():
        raise HTTPException(status_code=400, detail="Ajuste incompleto: tipo, item, quantidade e motivo.")
    with TRAVA:
        c = carga(numero)
        if c.get("status") in FASE_RETORNO:
            raise HTTPException(status_code=409, detail="A carga já foi validada — ajuste não vale mais.")
        a = _nova_pend("manual", payload.tipo, payload.pv, payload.desc, payload.qtd, payload.motivo, payload.obs, q, status="aguardando_pdf", pdf=payload.tipo)
        c.setdefault("pendencias", []).append(a)
        hist(c, q, "ajuste solicitado", f"{'SAIR' if a['tipo'] == 'sair' else 'ENTRAR'} {a['qtd']}× {a['desc']} (PV {a['pv']}) — {a['motivo']}")
        recalcular(c)
        gravar(c)
        return a


@router.put("/cargas/{numero}/pendencias/{pid}")
def rota_pendencia(numero: str, pid: str, payload: PendAcao, request: Request):
    exigir(request, "pcl", 3, "Tratar pendência")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        a = next((x for x in c.get("pendencias") or [] if x["id"] == pid), None)
        if not a or a.get("status") not in ("aberta", "aguardando_pdf"):
            raise HTTPException(status_code=404, detail="Pendência aberta não encontrada.")
        nota = str(payload.nota or "")[:300]
        if payload.acao == "resolver":
            if a["status"] != "aberta":
                raise HTTPException(status_code=409, detail="Esta pendência espera o novo PDF — cancele se não for mais preciso.")
            if not nota:
                raise HTTPException(status_code=400, detail="Diga como foi resolvido.")
            a.update(status="resolvido", nota=nota, tratado_por=q["por"], tratado_em=agora())
        elif payload.acao == "novo_pdf":
            if payload.pdf not in ("sair", "entrar"):
                raise HTTPException(status_code=400, detail="Diga se o item SAI ou ENTRA no novo PDF.")
            a.update(status="aguardando_pdf", pdf=payload.pdf, nota=nota, tratado_por=q["por"], tratado_em=agora())
        elif payload.acao == "cancelar":
            a.update(status="cancelado", nota=nota, tratado_por=q["por"], tratado_em=agora())
        else:
            raise HTTPException(status_code=400, detail="Ação inválida.")
        hist(c, q, f"pendência: {payload.acao}", f"{a['tipo']} {a['qtd']}× {a['desc']} — {nota}")
        recalcular(c)
        gravar(c)
        return {"pendencia": a, "status": c["status"]}


@router.post("/cargas/{numero}/refazer/{et}")
def rota_refazer(numero: str, et: str, payload: Texto, request: Request):
    """PCL manda refazer a montagem (refaz também a doca) ou só a conferência na doca."""
    et = _etapa_url(et)
    exigir(request, "pcl", 3, "Refazer etapa")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        if c.get("status") in FASE_RETORNO or c.get("status") == "aguardando_pdf":
            raise HTTPException(status_code=409, detail="Agora não dá para refazer esta etapa.")
        for a in c.get("pendencias") or []:
            if a.get("status") == "aberta" and (et == "montagem" or a.get("origem") == "doca"):
                a.update(status="refazer", nota=f"{ETAPAS[et]['nome']} refeita", tratado_por=q["por"], tratado_em=agora())
        _arquivar_etapa(c, "doca", "refazer")
        if et == "montagem":
            _arquivar_etapa(c, "montagem", "refazer")
        hist(c, q, f"refazer {ETAPAS[et]['nome']}", payload.texto or "")
        recalcular(c)
        gravar(c)
        return publico(c)


@router.post("/cargas/{numero}/gap/{seq}")
def rota_gap_conferente(numero: str, seq: str, payload: Texto, request: Request):
    exigir(request, ["montagem", "conferencia", "gap"], 2, "Apontar GAP")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        p = _ped(c, seq)
        if not p or not p.get("troca"):
            raise HTTPException(status_code=404, detail="Troca não encontrada nesta carga.")
        p.setdefault("gap", {"flags": [], "conferente": None, "analise": None})
        p["gap"]["conferente"] = {"por": q["por"], "email": q["email"], "em": agora(), "nota": str(payload.texto or "")[:400]}
        hist(c, q, "GAP apontado", f"seq {seq} — {payload.texto or ''}")
        gravar(c)
        return p["gap"]


@router.put("/cargas/{numero}/gap/{seq}")
def rota_gap_analise(numero: str, seq: str, payload: GapAnalise, request: Request):
    exigir(request, "gap", 3, "Analisar o GAP")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        p = _ped(c, seq)
        if not p or not p.get("troca"):
            raise HTTPException(status_code=404, detail="Troca não encontrada nesta carga.")
        p.setdefault("gap", {"flags": [], "conferente": None, "analise": None})
        p["gap"]["analise"] = {"classificacao": payload.classificacao[:120], "mapa_correto": payload.mapa_correto,
                               "fonte": str(payload.fonte or "")[:200], "nota": str(payload.nota or "")[:500],
                               "mapa_tipo": (p.get("protocolo") or {}).get("tipo") or "", "por": q["por"], "email": q["email"], "em": agora()}
        hist(c, q, "GAP analisado", f"seq {seq}: {payload.classificacao} · Mapa {'certo' if payload.mapa_correto else 'ERRADO'}")
        gravar(c)
        return p["gap"]


@router.get("/gap")
def rota_gap_lista(request: Request, de: str = "", ate: str = ""):
    ate, de = iso_ok(ate, hoje()), iso_ok(de, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    trocas, retornos = [], []
    for c in no_periodo(de, ate):
        for p in c.get("pedidos") or []:
            if p.get("troca"):
                trocas.append({"carga": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "seq": p.get("seq"), "pv": p.get("pv"),
                               "pv_retira": p.get("pv_retira"), "nome": p.get("nome"), "protocolo": p.get("protocolo"), "obs": p.get("obs"),
                               "entregar": p.get("entregar"), "col2": p.get("col2"), "gap": p.get("gap"), "flags": gap_flags(p)})
        for it in (c.get("retorno") or {}).get("itens") or []:
            retornos.append({**it, "carga": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "motorista": c.get("motorista"),
                             "transportadora": c.get("transportadora"), "status_carga": c.get("status")})
    ordem = lambda x: (x.get("data_iso") or "", x["carga"])
    return {"trocas": sorted(trocas, key=ordem, reverse=True), "retornos": sorted(retornos, key=ordem, reverse=True)}


@router.put("/cargas/{numero}/retorno/{rid}/tabular")
def rota_tabular(numero: str, rid: str, payload: Tabulacao, request: Request):
    """Toda peça retornada é tabulada: classificação real, responsável e destino final."""
    exigir(request, "gap", 3, "Tabular peça retornada")
    q = quem(request)
    if not payload.classificacao.strip() or not payload.responsavel.strip():
        raise HTTPException(status_code=400, detail="Informe a classificação real e o responsável.")
    with TRAVA:
        c = carga(numero)
        it = next((x for x in (c.get("retorno") or {}).get("itens") or [] if x["id"] == rid), None)
        if not it:
            raise HTTPException(status_code=404, detail="Peça não encontrada.")
        it["tabulacao"] = {"classificacao": payload.classificacao[:120], "responsavel": payload.responsavel[:120],
                           "destino_final": (payload.destino or it.get("destino") or "")[:120], "nota": str(payload.nota or "")[:500],
                           "destino_alterado": bool(payload.destino and payload.destino != it.get("destino")),
                           "por": q["por"], "email": q["email"], "em": agora()}
        hist(c, q, "peça tabulada", f"{it['desc']}: {payload.classificacao} · {payload.responsavel}")
        gravar(c)
        return it


@router.post("/cargas/{numero}/retorno")
def rota_retorno(numero: str, payload: dict, request: Request):
    exigir(request, "retorno", 2, "Registrar retorno")
    with TRAVA:
        return ler_retorno(carga(numero), quem(request), payload or {})


@router.post("/cargas/{numero}/retorno/remover")
def rota_retorno_remover(numero: str, payload: Texto, request: Request):
    exigir(request, "retorno", 2, "Remover item do retorno")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "em_retorno")
        ret = c["retorno"]
        it = next((x for x in ret["itens"] if x["id"] == payload.texto), None)
        if not it:
            raise HTTPException(status_code=404, detail="Item não encontrado.")
        if it.get("email") != q["email"] and nivel(request, "pcl") < 3:
            raise HTTPException(status_code=403, detail="Só quem registrou (ou o PCL) pode remover.")
        ret["itens"].remove(it)
        gravar(c)
        return {"ok": True}


@router.post("/cargas/{numero}/retorno/concluir")
def rota_retorno_concluir(numero: str, payload: Concluir, request: Request):
    exigir(request, "retorno", 2, "Concluir o retorno")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "validada", "em_retorno")
        ret = c.get("retorno") or {"inicio": agora(), "por": q["por"], "email": q["email"], "itens": []}
        c["retorno"] = ret
        ret["retirada_nao_feita"] = [str(s) for s in payload.retirada_nao_feita or []]
        pend = [s for s in situacao_pedidos(c) if s["retirada"] == "pendente"]
        if pend:
            return {"resultado": "confirmar_retiradas", "trocas": pend}
        ret["fim"] = {"em": agora(), "por": q["por"], "email": q["email"], "obs": str(payload.obs or "")[:500]}
        c["status"] = "retorno_concluido"
        sit = situacao_pedidos(c)
        hist(c, q, "retorno concluído", f"{sum(1 for s in sit if s['situacao'] == 'entregue')} entregue(s) · "
                                        f"{sum(1 for s in sit if s['situacao'] != 'entregue')} com retorno · {len(ret['itens'])} peça(s) → PCL apontar no ERP")
        gravar(c)
        return {"resultado": "ok", "carga": publico(c)}


@router.post("/cargas/{numero}/apontar")
def rota_apontar(numero: str, payload: Texto, request: Request):
    """PCL alimentou o ERP com o retorno: a carga é BAIXADA."""
    exigir(request, "pcl", 3, "Apontar no ERP")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "retorno_concluido")
        c["apontamento"] = {"em": agora(), "por": q["por"], "email": q["email"], "obs": str(payload.texto or "")[:500]}
        c["status"] = "baixada"
        hist(c, q, "apontado no ERP — carga baixada", payload.texto or "")
        gravar(c)
        return publico(c)


@router.post("/cargas/{numero}/reabrir-retorno")
def rota_reabrir(numero: str, payload: Texto, request: Request):
    exigir(request, "pcl", 3, "Reabrir o retorno")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "retorno_concluido")
        c["retorno"]["fim"] = None
        c["status"] = "em_retorno"
        hist(c, q, "retorno reaberto", payload.texto or "")
        gravar(c)
        return publico(c)


@router.get("/pendencias")
def rota_pendencias(request: Request, de: str = "", ate: str = ""):
    ate, de = iso_ok(ate, hoje()), iso_ok(de, (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    vistos = {c["numero"]: c for c in no_periodo(de, ate)}
    vistos.update({c["numero"]: c for c in CARGAS.values() if c.get("aberta")})
    out = []
    for c in vistos.values():
        for a in c.get("pendencias") or []:
            if a.get("status") in ("aberta", "aguardando_pdf") or de <= (a.get("em") or "")[:10] <= ate:
                out.append({**a, "carga": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "motorista": c.get("motorista"),
                            "versao_atual": c.get("versao"), "status_carga": c.get("status")})
    return sorted(out, key=lambda x: x.get("em") or "", reverse=True)


@router.get("/skus")
def rota_skus(request: Request, dias: int = 60):
    """SKUs que apareceram nas cargas do período — base do cadastro de EAN/QR e do progresso."""
    de = (datetime.now() - timedelta(days=max(1, min(dias, 365)))).strftime("%Y-%m-%d")
    d: dict = {}
    pl: dict = {}
    for c in no_periodo(de, "9999-12-31"):
        if c.get("placa"):
            pl.setdefault(c["placa"], {"placa": c["placa"], "transportadora": c.get("transportadora"), "cargas": 0})["cargas"] += 1
        for x in c.get("unidades") or []:
            if x.get("sku"):
                r = d.setdefault(x["sku"], {"sku": x["sku"], "desc": x.get("desc"), "unidades": 0})
                r["unidades"] += 1
    prods = produtos()
    for sku, p in prods.items():   # cadastrados que não saíram no período também aparecem
        d.setdefault(sku, {"sku": sku, "desc": p.get("desc"), "unidades": 0})
    for r in d.values():
        p = prods.get(r["sku"]) or {}
        r.update(eans=p.get("eans") or [], qr=bool(p.get("qr")))
    for r in pl.values():
        r["motorista"] = (placas().get(r["placa"]) or {}).get("motorista") or ""
    return {"skus": sorted(d.values(), key=lambda r: -r["unidades"]), "placas": sorted(pl.values(), key=lambda r: -r["cargas"])}


@router.get("/codigo/{codigo}")
def rota_codigo(codigo: str, request: Request):
    """Cadastros: a câmera leu um código — de qual SKU ele é?"""
    sku = indice_ean().get(re.sub(r"\s+", "", codigo))
    return {"codigo": codigo, "sku": sku, "produto": produtos().get(sku) if sku else None}


@router.get("/cadastros")
def rota_cadastros(request: Request):
    return {"produtos": produtos(), "placas": placas(), "listas": listas()}


@router.put("/cadastros/produto")
def rota_produto(payload: Produto, request: Request):
    exigir(request, "cadastros", 3, "Cadastrar produto")
    q = quem(request)
    sku = payload.sku.strip()
    if not sku:
        raise HTTPException(status_code=400, detail="SKU vazio.")
    eans = sorted({re.sub(r"\s+", "", str(e)) for e in payload.eans if str(e).strip()})
    idx = indice_ean()
    em_outro = [e for e in eans if idx.get(e) not in (None, sku)]
    if em_outro:
        raise HTTPException(status_code=409, detail=f"Código já usado em outro SKU: {', '.join(em_outro)}")
    with TRAVA:
        p = produtos().setdefault(sku, {})
        p.update(desc=payload.desc or p.get("desc") or "", eans=eans, qr=bool(payload.qr), por=q["por"], em=agora())
        _cad_salvar("produtos")
    return p


@router.post("/cadastros/ean")
def rota_add_ean(payload: AddEan, request: Request):
    """Cadastros com a câmera: acrescenta um código a um SKU."""
    exigir(request, "cadastros", 3, "Vincular código")
    q = quem(request)
    codigo = re.sub(r"\s+", "", payload.codigo)
    if not re.fullmatch(r"[0-9A-Za-z\-]{6,40}", codigo):
        raise HTTPException(status_code=400, detail="Código inválido.")
    dono = indice_ean().get(codigo)
    if dono and dono != payload.sku:
        raise HTTPException(status_code=409, detail=f"Este código já está no SKU {dono}.")
    with TRAVA:
        p = produtos().setdefault(payload.sku, {"desc": "", "eans": [], "qr": False})
        if not p.get("desc"):
            p["desc"] = next((x.get("desc") for c in CARGAS.values() for x in c.get("unidades") or [] if x.get("sku") == payload.sku), "") or ""
        p["eans"] = sorted(set(p.get("eans") or []) | {codigo})
        p.update(por=q["por"], em=agora())
        _cad_salvar("produtos")
    return {"sku": payload.sku, **p}


@router.put("/cadastros/placa")
def rota_placa(payload: Placa, request: Request):
    exigir(request, "cadastros", 3, "Cadastrar motorista")
    q = quem(request)
    pl = re.sub(r"\s+", "", payload.placa.upper())
    with TRAVA:
        placas()[pl] = {"motorista": payload.motorista.strip()[:120], "por": q["por"], "em": agora()}
        _cad_salvar("placas")
        for c in CARGAS.values():   # cargas abertas desta placa sem motorista recebem o nome
            if c.get("placa") == pl and c.get("aberta") and not c.get("motorista"):
                c["motorista"] = payload.motorista.strip()[:120]
                gravar(c)
    return placas()[pl]


@router.put("/cadastros/listas")
def rota_listas(payload: dict, request: Request):
    exigir(request, "cadastros", 3, "Mudar as listas")
    with TRAVA:
        l = listas()
        for k in LISTAS_PADRAO:
            if k in payload:
                v = payload[k]
                if k == "exigir_conferencia":
                    l[k] = bool(v)
                elif k == "clientes_transferencia":
                    l[k] = str(v)[:500]
                else:
                    l[k] = [str(x).strip()[:120] for x in v if str(x).strip()][:60]
        _cad_salvar("listas")
    return l


@router.get("/painel")
def rota_painel(request: Request, de: str = "", ate: str = ""):
    ate = iso_ok(ate, hoje())
    de = iso_ok(de, ate)
    if de > ate:
        de, ate = ate, de
    return painel(de, ate)


@router.post("/recarregar")
def rota_recarregar(request: Request):
    exigir(request, "pcl", 3, "Recarregar")
    with TRAVA:
        CARGAS.clear()
        _CARREGADO.update(ok=False, desde="")
        for k in CAD:
            CAD[k] = None
        CART.update(dias=None, lidos_em=0.0, pv={}, lidas={})
    return {"ok": True}
