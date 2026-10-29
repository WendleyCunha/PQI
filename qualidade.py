"""
qualidade.py — módulo QUALIDADE (conferência digital do Mapa de Entregas)

Fluxo
  1. O ANALISTA importa o PDF do Mapa de Entregas (fonte da verdade). A página
     lê o PDF no navegador e manda as cargas já organizadas para cá.
  2. Cada PV é procurado na BASE da Carteira (todas as fotos importadas, da mais
     recente para trás). Os itens da Carteira trazem o SKU. PV fora da Carteira,
     ou itens que não batem, viram ALERTA.
  3. O CONFERENTE confere a carga no celular (carga = palete): lê o código de
     barras (EAN/QR) ou, enquanto o SKU não tem EAN cadastrado, confirma tocando.
     Falta/sobra exigem motivo. "Palete OK" fecha a conferência.
  4. O ANALISTA valida — ou pede AJUSTE (item sai / item entra). O ajuste vira
     tarefa: gerar novo PDF no Protheus e importar. A nova versão confronta o
     que foi pedido; a carga inteira é conferida de novo.
  5. RETORNO: o que voltou (não entregue / retirado na troca), com o estado.
  6. GAP das trocas: o conferente aponta, o analista analisa e registra a
     classificação real (as automáticas também entram na fila).

Coleções do Firestore (prefixo qld_):
  qld_cargas      1 documento por carga (número do Protheus)
  qld_cadastros   "produtos" (SKU → EAN/QR) · "placas" (placa → motorista) · "listas"

Economia do plano grátis: as cargas recentes ficam em memória (lidas uma vez);
toda gravação passa por aqui e atualiza a memória.
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
from typing import Any, Callable, Optional

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


def _fs_recentes(col: str, campo: str, minimo: str) -> list:
    q = db.get_db().collection(col).where(campo, ">=", minimo)
    return [d.to_dict() for d in q.stream(timeout=db.TIMEOUT_FS)]


def _fs_status_abertos(col: str) -> list:
    q = db.get_db().collection(col).where("aberta", "==", True)
    return [d.to_dict() for d in q.stream(timeout=db.TIMEOUT_FS)]


# ══════════════════════════════════════════════════════════════════════════
# utilidades
# ══════════════════════════════════════════════════════════════════════════
def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sem(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip().upper()


def num_chave(pv) -> str:
    d = re.sub(r"\D", "", str(pv or "")).lstrip("0")
    return d or str(pv or "").strip()


def data_iso(br: str) -> str:
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", str(br or "").strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else datetime.now().strftime("%Y-%m-%d")


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
                       "Adaptação / conforto", "Troca não efetuada", "Outro"],
    "avarias": ["Rasgado", "Amassado", "Sujo / manchado", "Molhado", "Embalagem violada", "Quebrado", "Outro"],
    "motivos_divergencia": ["Item não encontrado no estoque", "Item avariado no carregamento", "Erro de separação",
                            "Item a mais (não é desta carga)", "Código ilegível", "Outro"],
    "motivos_ajuste": ["Item avariado", "Falta de estoque", "Erro de separação", "Pedido alterado", "Cliente pediu", "Outro"],
    "clientes_transferencia": "0NG5EE",
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
            # dia reimportado: o que veio dele é relido
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
_CARREGADO = {"ok": False}


def _carregar() -> None:
    if _CARREGADO["ok"]:
        return
    with TRAVA:
        if _CARREGADO["ok"]:
            return
        minimo = (datetime.now() - timedelta(days=DIAS_EM_MEMORIA)).strftime("%Y-%m-%d")
        for c in _fs_recentes(COL_CARGAS, "data_iso", minimo) + _fs_status_abertos(COL_CARGAS):
            if c and c.get("numero"):
                CARGAS[c["numero"]] = c
        _CARREGADO["ok"] = True


def carga(numero: str) -> dict:
    _carregar()
    c = CARGAS.get(numero)
    if c is None:
        c = _fs_get(COL_CARGAS, numero)
        if not c:
            raise HTTPException(status_code=404, detail=f"Carga {numero} não encontrada.")
        CARGAS[numero] = c
    return c


FINAIS = {"retorno_concluido"}


def gravar(c: dict) -> None:
    c["aberta"] = c.get("status") not in FINAIS
    c["atualizado_em"] = agora()
    c["resumo"] = resumo(c)
    _fs_set(COL_CARGAS, c["numero"], c)
    CARGAS[c["numero"]] = c


def hist(c: dict, q: dict, acao: str, det: str = "") -> None:
    c.setdefault("historico", []).append({"em": agora(), "por": q["por"], "email": q["email"], "acao": acao, "det": det[:400]})
    c["historico"] = c["historico"][-300:]


def resumo(c: dict) -> dict:
    un = c.get("unidades") or []
    conf = c.get("conferencia") or {}
    lidas = conf.get("leituras") or {}
    peds = c.get("pedidos") or []
    trocas = [p for p in peds if p.get("troca")]
    gap_pend = sum(1 for p in trocas if (p.get("gap") or {}).get("flags") and not (p.get("gap") or {}).get("analise"))
    return {
        "pedidos": len(peds), "unidades": len(un), "lidas": len(lidas),
        "por_codigo": sum(1 for x in lidas.values() if x.get("modo") in ("ean", "qr")),
        "sobras": len(conf.get("sobras") or []),
        "alertas": sum(len(p.get("alertas") or []) for p in peds),
        "pv_fora": sum(1 for p in peds if not (p.get("carteira") or {}).get("achado")),
        "trocas": len(trocas), "gap_pendentes": gap_pend,
        "ajustes_abertos": sum(1 for a in c.get("ajustes") or [] if a.get("status") == "aberto"),
        "retornadas": len((c.get("retorno") or {}).get("itens") or []),
        "avariadas": sum(1 for x in (c.get("retorno") or {}).get("itens") or [] if x.get("estado") == "avariada"),
    }


def publico(c: dict) -> dict:
    return {k: c.get(k) for k in ("numero", "data_mapa", "data_iso", "placa", "transportadora", "motorista", "cubagem",
                                   "status", "versao", "transferencia", "atualizado_em")} | {"resumo": c.get("resumo") or resumo(c)}


# ══════════════════════════════════════════════════════════════════════════
# IMPORTAÇÃO do Mapa (com versões e confronto dos ajustes)
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
    """Confere cada ajuste aberto contra a nova versão; devolve as mudanças não solicitadas."""
    va, vn = _qtd_por_item(antigos), _qtd_por_item(novos)
    def acha(d, pv, desc):
        k, ds = num_chave(pv), sem(desc)
        return sum(v for (p, s), v in d.items() if p == k and (s == ds or s.startswith(ds) or ds.startswith(s)))
    explicadas = set()
    for a in c.get("ajustes") or []:
        if a.get("status") != "aberto":
            continue
        antes, depois = acha(va, a["pv"], a["desc"]), acha(vn, a["pv"], a["desc"])
        ok = depois <= antes - int(a["qtd"]) if a["tipo"] == "sair" else depois >= antes + int(a["qtd"])
        a.update(status="confirmado" if ok else "nao_executado", verificado_em=agora(), versao_verificada=c.get("versao"),
                 nota=f"Antes {antes}, depois {depois} no novo PDF.")
        explicadas.add((num_chave(a["pv"]), sem(a["desc"])))
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
    # observações repetidas no arquivo (texto padrão do cadastro) → GAP
    cont: dict = {}
    for c in cargas_in:
        for p in c.get("pedidos") or []:
            for o in set(sem(x) for x in p.get("obs") or [] if not sem(x).startswith("OBSERVACAO")):
                cont[o] = cont.get(o, 0) + 1
    repetidas = {o for o, n in cont.items() if n >= 3}
    transf = clientes_transf()
    rel = {"novas": [], "versoes": [], "iguais": [], "alertas": 0, "pv_fora": 0}
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
            pl = str(ci.get("placa") or "").upper()
            mot = (placas().get(pl) or {}).get("motorista") or ""
            base_carga = {"numero": num, "data_mapa": payload.get("data_mapa") or "", "data_iso": data_iso(payload.get("data_mapa")),
                          "placa": pl, "transportadora": str(ci.get("transportadora") or ""), "motorista": mot,
                          "cubagem": float(ci.get("cubagem") or 0), "transferencia": bool(peds) and all(p["transferencia"] for p in peds)}
            if existente:
                c = existente
                antigos = c.get("pedidos") or []
                if c.get("conferencia"):
                    c.setdefault("conferencias_anteriores", []).append({**c["conferencia"], "versao": c.get("versao")})
                c.update(base_carga)
                c["versao"] = int(c.get("versao") or 1) + 1
                # o GAP já analisado continua valendo para o mesmo PV
                gap_ant = {num_chave(p.get("pv")): p.get("gap") for p in antigos}
                for p in peds:
                    g = gap_ant.get(num_chave(p.get("pv")))
                    if g and (g.get("analise") or g.get("conferente")):
                        p["gap"] = {**p["gap"], "conferente": g.get("conferente"), "analise": g.get("analise")}
                c["mudancas_nao_solicitadas"] = _confrontar({**c, "pedidos": peds}, antigos, peds)
                c["pedidos"], c["unidades"] = peds, _unidades(peds)
                c["conferencia"], c["validacao"], c["status"] = None, None, "importada"
                c["versoes"].append({"v": c["versao"], "em": agora(), "por": q["por"], "arquivo": payload.get("arquivo") or "", "assinatura": assin})
                hist(c, q, "nova versão do Mapa", f"v{c['versao']} · {len(c['mudancas_nao_solicitadas'])} mudança(s) não solicitada(s)")
                rel["versoes"].append(num)
            else:
                c = {**base_carga, "versao": 1, "status": "importada", "pedidos": peds, "unidades": _unidades(peds),
                     "conferencia": None, "validacao": None, "ajustes": [], "retorno": None, "mudancas_nao_solicitadas": [],
                     "versoes": [{"v": 1, "em": agora(), "por": q["por"], "arquivo": payload.get("arquivo") or "", "assinatura": assin}],
                     "historico": [], "criado_em": agora()}
                hist(c, q, "Mapa importado", payload.get("arquivo") or "")
                rel["novas"].append(num)
            rel["alertas"] += sum(len(p["alertas"]) for p in peds)
            rel["pv_fora"] += sum(1 for p in peds if not p["carteira"]["achado"])
            gravar(c)
    return rel


# ══════════════════════════════════════════════════════════════════════════
# CONFERÊNCIA
# ══════════════════════════════════════════════════════════════════════════
def _ped(c: dict, seq_ou_pv: str) -> Optional[dict]:
    return next((p for p in c.get("pedidos") or [] if p.get("seq") == seq_ou_pv or num_chave(p.get("pv")) == num_chave(seq_ou_pv)), None)


def _exigir_status(c: dict, *ok) -> None:
    if c.get("status") not in ok:
        nomes = {"importada": "importada", "em_conferencia": "em conferência", "conferida": "conferida", "validada": "validada",
                 "aguardando_pdf": "aguardando novo PDF", "em_retorno": "em retorno", "retorno_concluido": "retorno concluído"}
        raise HTTPException(status_code=409, detail=f"A carga {c['numero']} está “{nomes.get(c.get('status'), c.get('status'))}” — esta ação não vale agora.")


def _conf(c: dict, q: dict) -> dict:
    if not c.get("conferencia"):
        c["conferencia"] = {"inicio": agora(), "por": q["por"], "email": q["email"], "leituras": {}, "sobras": [], "fim": None}
        c["status"] = "em_conferencia"
        hist(c, q, "conferência iniciada", f"versão {c.get('versao')}")
    return c["conferencia"]


def ler_codigo(c: dict, q: dict, codigo: str, unidade: Optional[str], pv: Optional[str], qr: Optional[str]) -> dict:
    """Uma leitura na conferência. Devolve o que a tela deve fazer."""
    _exigir_status(c, "importada", "em_conferencia")
    conf = _conf(c, q)
    lidas = conf["leituras"]
    un = c.get("unidades") or []
    # confirmação manual de uma unidade (só para SKU sem EAN cadastrado)
    if unidade and not codigo:
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
        # EAN desconhecido: a tela oferece vincular a um item da carga que ainda não tem EAN
        opcoes = sorted({(x["sku"], x["desc"]) for x in un if x.get("sku") and not tem_ean(x["sku"])})
        return {"resultado": "desconhecido", "codigo": codigo, "opcoes": [{"sku": s, "desc": d} for s, d in opcoes]}
    livres = [x for x in un if x.get("sku") == sku and x["id"] not in lidas]
    if pv:
        livres = [x for x in livres if num_chave(x["pv"]) == num_chave(pv)]
    if not livres:
        desc = (produtos().get(sku) or {}).get("desc") or sku
        conf["sobras"].append({"id": uuid.uuid4().hex[:8], "codigo": codigo, "sku": sku, "desc": desc, "por": q["por"], "email": q["email"], "em": agora()})
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


def finalizar_conferencia(c: dict, q: dict, divergencias: list, obs: str) -> dict:
    _exigir_status(c, "em_conferencia", "importada")
    conf = _conf(c, q)
    lidas = conf["leituras"]
    faltas: dict = {}
    for x in c.get("unidades") or []:
        if x["id"] not in lidas:
            k = f"falta|{x['pv']}|{x['desc']}"
            faltas.setdefault(k, {"chave": k, "tipo": "falta", "pv": x["pv"], "desc": x["desc"], "qtd": 0})["qtd"] += 1
    sobras: dict = {}
    for s in conf.get("sobras") or []:
        k = f"sobra|{s['codigo']}"
        sobras.setdefault(k, {"chave": k, "tipo": "sobra", "pv": "", "desc": s["desc"], "codigo": s["codigo"], "qtd": 0})["qtd"] += 1
    todas = list(faltas.values()) + list(sobras.values())
    resp = {d.get("chave"): d for d in divergencias or []}
    sem_motivo = [d for d in todas if not (resp.get(d["chave"]) or {}).get("motivo")]
    if sem_motivo:
        return {"resultado": "precisa_motivo", "divergencias": todas}
    for d in todas:
        d["motivo"] = resp[d["chave"]]["motivo"]
        d["obs"] = str(resp[d["chave"]].get("obs") or "")[:300]
    conf["fim"] = {"em": agora(), "por": q["por"], "email": q["email"], "ok": not todas, "divergencias": todas, "obs": str(obs or "")[:500]}
    c["status"] = "conferida"
    hist(c, q, "palete OK" if not todas else "conferência fechada com divergência", f"{len(todas)} divergência(s)")
    gravar(c)
    return {"resultado": "ok", "divergencias": todas}


# ══════════════════════════════════════════════════════════════════════════
# RETORNO
# ══════════════════════════════════════════════════════════════════════════
def ler_retorno(c: dict, q: dict, dados: dict) -> dict:
    _exigir_status(c, "validada", "em_retorno")
    ret = c.get("retorno") or {"inicio": agora(), "por": q["por"], "email": q["email"], "itens": [], "fim": None}
    c["retorno"] = ret
    origem = dados.get("origem") if dados.get("origem") in ("nao_entregue", "retirada") else "nao_entregue"
    estado = "avariada" if dados.get("estado") == "avariada" else "ok"
    codigo = re.sub(r"\s+", "", str(dados.get("codigo") or ""))
    # candidatos: itens que saíram (não entregue) ou itens a retirar das trocas
    cand = []
    for p in c.get("pedidos") or []:
        lista = p.get("entregar") if origem == "nao_entregue" else (p.get("col2") if p.get("troca") else [])
        for i, it in enumerate(lista or []):
            ja = sum(1 for r in ret["itens"] if r["origem"] == origem and r["pv"] == p.get("pv") and r["i"] == i)
            if ja < int(it.get("qtd") or 0):
                cand.append({"pv": p.get("pv"), "seq": p.get("seq"), "i": i, "sku": it.get("sku"), "desc": it.get("desc"), "nome": p.get("nome"), "bairro": p.get("bairro")})
    if codigo:
        sku = indice_ean().get(codigo)
        if not sku:
            return {"resultado": "desconhecido", "codigo": codigo, "mensagem": "EAN não cadastrado. Escolha o item na lista."}
        cand = [x for x in cand if x.get("sku") == sku]
    elif dados.get("item"):
        pv_, i_ = str(dados["item"]).split("|", 1)
        cand = [x for x in cand if num_chave(x["pv"]) == num_chave(pv_) and str(x["i"]) == i_]
        if cand and tem_ean(cand[0].get("sku")):
            return {"resultado": "exige_codigo", "mensagem": "Este produto tem EAN cadastrado: leia o código de barras."}
    if dados.get("pv"):
        cand = [x for x in cand if num_chave(x["pv"]) == num_chave(dados["pv"])]
    if not cand:
        return {"resultado": "nao_pertence", "mensagem": "Não há unidade pendente deste produto nesta carga para esse tipo de retorno."}
    pvs = sorted({num_chave(x["pv"]) for x in cand})
    if len(pvs) > 1:
        return {"resultado": "escolher_pedido", "opcoes": [{"pv": x["pv"], "nome": x["nome"], "bairro": x["bairro"]} for x in {num_chave(y["pv"]): y for y in cand}.values()]}
    if estado == "avariada" and not dados.get("avaria"):
        return {"resultado": "precisa_avaria", "mensagem": "Escolha o tipo de avaria."}
    x = cand[0]
    item = {"id": uuid.uuid4().hex[:8], "pv": x["pv"], "seq": x["seq"], "i": x["i"], "sku": x.get("sku"), "desc": x["desc"], "origem": origem,
            "estado": estado, "avaria": str(dados.get("avaria") or "") if estado == "avariada" else "", "obs": str(dados.get("obs") or "")[:300],
            "modo": "ean" if codigo else "manual", "codigo": codigo, "por": q["por"], "email": q["email"], "em": agora()}
    ret["itens"].append(item)
    c["status"] = "em_retorno"
    gravar(c)
    return {"resultado": "ok", "item": item}


# ══════════════════════════════════════════════════════════════════════════
# PAINEL
# ══════════════════════════════════════════════════════════════════════════
def painel(dias: int) -> dict:
    _carregar()
    lim = (datetime.now() - timedelta(days=max(1, dias))).strftime("%Y-%m-%d")
    cs = [c for c in CARGAS.values() if (c.get("data_iso") or "") >= lim]
    st: dict = {}
    skus, skus_ean = set(), set()
    un_conf = un_cod = 0
    div = {"faltas": 0, "sobras": 0, "por_motivo": {}}
    trocas = gap_flag = gap_analis = gap_errado = 0
    flags: dict = {}
    aj = {"aberto": 0, "confirmado": 0, "nao_executado": 0, "cancelado": 0}
    ret = {"unidades": 0, "avariadas": 0, "por_placa": {}, "por_transportadora": {}, "por_motorista": {}, "por_produto": {}, "por_avaria": {}}
    alertas = pv_fora = 0
    for c in cs:
        st[c.get("status")] = st.get(c.get("status"), 0) + 1
        for x in c.get("unidades") or []:
            if x.get("sku"):
                skus.add(x["sku"])
                if tem_ean(x["sku"]):
                    skus_ean.add(x["sku"])
        # divergências contam em todas as conferências fechadas (inclusive as de versões anteriores do Mapa)
        for conf in (c.get("conferencias_anteriores") or []) + [c.get("conferencia") or {}]:
            if not conf.get("fim"):
                continue
            for d in conf["fim"].get("divergencias") or []:
                div["faltas" if d["tipo"] == "falta" else "sobras"] += int(d.get("qtd") or 0)
                div["por_motivo"][d.get("motivo") or "—"] = div["por_motivo"].get(d.get("motivo") or "—", 0) + int(d.get("qtd") or 0)
        conf = c.get("conferencia") or {}
        if conf.get("fim"):
            lid = conf.get("leituras") or {}
            un_conf += len(lid)
            un_cod += sum(1 for v in lid.values() if v.get("modo") in ("ean", "qr"))
        for p in c.get("pedidos") or []:
            alertas += len(p.get("alertas") or [])
            pv_fora += 0 if (p.get("carteira") or {}).get("achado") else 1
            if p.get("troca"):
                trocas += 1
                g = p.get("gap") or {}
                fl = list(g.get("flags") or []) + (["conferente"] if g.get("conferente") else [])
                if fl:
                    gap_flag += 1
                    for f in fl:
                        flags[f] = flags.get(f, 0) + 1
                if g.get("analise"):
                    gap_analis += 1
                    if g["analise"].get("mapa_correto") is False:
                        gap_errado += 1
        for a in c.get("ajustes") or []:
            aj[a.get("status", "aberto")] = aj.get(a.get("status", "aberto"), 0) + 1
        for r in (c.get("retorno") or {}).get("itens") or []:
            ret["unidades"] += 1
            if r.get("estado") == "avariada":
                ret["avariadas"] += 1
                for chave, valor in (("por_placa", c.get("placa")), ("por_transportadora", c.get("transportadora")),
                                     ("por_motorista", c.get("motorista") or "(sem motorista)"), ("por_produto", r.get("desc")), ("por_avaria", r.get("avaria"))):
                    ret[chave][valor or "—"] = ret[chave].get(valor or "—", 0) + 1
    todos_skus = produtos()
    return {
        "dias": dias, "cargas": len(cs), "status": st, "alertas": alertas, "pv_fora": pv_fora,
        "ean": {"skus_nas_cargas": len(skus), "skus_com_ean": len(skus_ean), "faltam": len(skus - skus_ean),
                "pct_skus": round(100 * len(skus_ean) / len(skus), 1) if skus else 0,
                "unidades_conferidas": un_conf, "unidades_por_codigo": un_cod,
                "pct_unidades_codigo": round(100 * un_cod / un_conf, 1) if un_conf else 0,
                "skus_com_qr": sum(1 for p in todos_skus.values() if p.get("qr")), "cadastrados_total": sum(1 for p in todos_skus.values() if p.get("eans"))},
        "divergencias": div, "ajustes": aj,
        "gap": {"trocas": trocas, "apontadas": gap_flag, "analisadas": gap_analis, "mapa_errado": gap_errado,
                "pct_gap": round(100 * gap_errado / gap_analis, 1) if gap_analis else 0, "por_tipo": flags},
        "retorno": ret,
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
    tipo: str
    pv: str
    desc: str
    qtd: int = 1
    motivo: str
    obs: Optional[str] = ""


class AjusteStatus(BaseModel):
    status: str
    nota: Optional[str] = ""


class Texto(BaseModel):
    texto: Optional[str] = ""


class GapAnalise(BaseModel):
    classificacao: str
    mapa_correto: bool
    fonte: Optional[str] = ""
    nota: Optional[str] = ""


class Produto(BaseModel):
    sku: str
    desc: Optional[str] = ""
    eans: list = []
    qr: bool = False


class Placa(BaseModel):
    placa: str
    motorista: str


@router.get("/cargas")
def rota_cargas(request: Request, dias: int = 15):
    _carregar()
    lim = (datetime.now() - timedelta(days=max(1, min(dias, DIAS_EM_MEMORIA)))).strftime("%Y-%m-%d")
    lst = [publico(c) for c in CARGAS.values() if (c.get("data_iso") or "") >= lim or c.get("aberta")]
    return sorted(lst, key=lambda x: (x.get("data_iso") or "", x.get("numero") or ""), reverse=True)


@router.get("/cargas/{numero}")
def rota_carga(numero: str, request: Request):
    c = carga(numero)
    return {**c, "resumo": resumo(c)}


@router.post("/importar")
def rota_importar(payload: Importacao, request: Request):
    exigir(request, "validacao", 3, "Importar o Mapa de Entregas")
    if len(json.dumps(payload.cargas, ensure_ascii=False)) > 3_000_000:
        raise HTTPException(status_code=413, detail="Arquivo grande demais.")
    return importar_mapa(payload.model_dump(), quem(request))


@router.post("/cargas/{numero}/leitura")
def rota_leitura(numero: str, payload: Leitura, request: Request):
    exigir(request, "conferencia", 2, "Conferir a carga")
    with TRAVA:
        return ler_codigo(carga(numero), quem(request), payload.codigo, payload.unidade, payload.pv, payload.qr)


@router.post("/cargas/{numero}/desfazer")
def rota_desfazer(numero: str, payload: Leitura, request: Request):
    exigir(request, "conferencia", 2, "Desfazer leitura")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "em_conferencia")
        conf = c["conferencia"]
        if payload.unidade and payload.unidade in conf["leituras"]:
            lt = conf["leituras"][payload.unidade]
            if lt.get("email") != q["email"] and nivel(request, "validacao") < 3:
                raise HTTPException(status_code=403, detail="Só quem leu (ou o analista) pode desfazer esta leitura.")
            conf["leituras"].pop(payload.unidade)
        elif payload.codigo:   # tirar uma sobra
            conf["sobras"] = [s for s in conf.get("sobras") or [] if s.get("id") != payload.codigo]
        gravar(c)
        return {"ok": True}


@router.post("/cargas/{numero}/vincular-ean")
def rota_vincular(numero: str, payload: VincularEan, request: Request):
    """Conferente lê um EAN desconhecido e diz de qual item da carga ele é — o EAN fica cadastrado (auditado)."""
    exigir(request, ["conferencia", "cadastros"], 2, "Vincular EAN")
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
        p.update(desc=p.get("desc") or desc, por=q["por"], em=agora(), origem=f"conferência da carga {numero}")
        _cad_salvar("produtos")
        hist(c, q, "EAN vinculado", f"{codigo} → {payload.sku} {desc}")
        return ler_codigo(c, q, codigo, None, None, None)


@router.post("/cargas/{numero}/finalizar")
def rota_finalizar(numero: str, payload: Finalizar, request: Request):
    exigir(request, "conferencia", 2, "Fechar a conferência (Palete OK)")
    with TRAVA:
        return finalizar_conferencia(carga(numero), quem(request), payload.divergencias, payload.obs)


@router.post("/cargas/{numero}/validar")
def rota_validar(numero: str, payload: Texto, request: Request):
    exigir(request, "validacao", 3, "Validar a carga")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "conferida")
        if any(a.get("status") == "aberto" for a in c.get("ajustes") or []):
            raise HTTPException(status_code=409, detail="Há ajuste aberto: importe o novo PDF (ou cancele o ajuste) antes de validar.")
        c["validacao"] = {"em": agora(), "por": q["por"], "email": q["email"], "obs": str(payload.texto or "")[:500]}
        c["status"] = "validada"
        hist(c, q, "carga validada", payload.texto or "")
        gravar(c)
        return publico(c)


@router.post("/cargas/{numero}/ajustes")
def rota_ajuste(numero: str, payload: Ajuste, request: Request):
    exigir(request, ["conferencia", "validacao"], 2, "Solicitar ajuste")
    q = quem(request)
    if payload.tipo not in ("sair", "entrar") or payload.qtd < 1 or not payload.motivo.strip() or not payload.desc.strip():
        raise HTTPException(status_code=400, detail="Ajuste incompleto: tipo, item, quantidade e motivo.")
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "importada", "em_conferencia", "conferida", "aguardando_pdf")
        a = {"id": uuid.uuid4().hex[:8], "tipo": payload.tipo, "pv": payload.pv, "desc": payload.desc[:200], "qtd": int(payload.qtd),
             "motivo": payload.motivo[:120], "obs": str(payload.obs or "")[:300], "por": q["por"], "email": q["email"], "em": agora(),
             "status": "aberto", "versao_pedida": c.get("versao")}
        c.setdefault("ajustes", []).append(a)
        c["status"] = "aguardando_pdf"
        hist(c, q, "ajuste solicitado", f"{'SAIR' if a['tipo'] == 'sair' else 'ENTRAR'} {a['qtd']}× {a['desc']} (PV {a['pv']}) — {a['motivo']}")
        gravar(c)
        return a


@router.put("/cargas/{numero}/ajustes/{aid}")
def rota_ajuste_status(numero: str, aid: str, payload: AjusteStatus, request: Request):
    exigir(request, "validacao", 3, "Mudar ajuste")
    q = quem(request)
    if payload.status not in ("cancelado",):
        raise HTTPException(status_code=400, detail="Só é possível cancelar um ajuste aqui; os outros status vêm do novo PDF.")
    with TRAVA:
        c = carga(numero)
        a = next((x for x in c.get("ajustes") or [] if x["id"] == aid), None)
        if not a or a.get("status") != "aberto":
            raise HTTPException(status_code=404, detail="Ajuste aberto não encontrado.")
        a.update(status="cancelado", nota=str(payload.nota or "")[:300], cancelado_por=q["por"], cancelado_em=agora())
        if c.get("status") == "aguardando_pdf" and not any(x.get("status") == "aberto" for x in c["ajustes"]):
            c["status"] = "conferida" if (c.get("conferencia") or {}).get("fim") else ("em_conferencia" if c.get("conferencia") else "importada")
        hist(c, q, "ajuste cancelado", f"{a['desc']} — {payload.nota or ''}")
        gravar(c)
        return a


@router.post("/cargas/{numero}/gap/{seq}")
def rota_gap_conferente(numero: str, seq: str, payload: Texto, request: Request):
    exigir(request, ["conferencia", "gap"], 2, "Apontar GAP")
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
def rota_gap_lista(request: Request, dias: int = 30):
    _carregar()
    lim = (datetime.now() - timedelta(days=max(1, min(dias, DIAS_EM_MEMORIA)))).strftime("%Y-%m-%d")
    out = []
    for c in CARGAS.values():
        if (c.get("data_iso") or "") < lim:
            continue
        for p in c.get("pedidos") or []:
            if p.get("troca"):
                out.append({"carga": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "seq": p.get("seq"), "pv": p.get("pv"),
                            "pv_retira": p.get("pv_retira"), "nome": p.get("nome"), "protocolo": p.get("protocolo"), "obs": p.get("obs"),
                            "entregar": p.get("entregar"), "col2": p.get("col2"), "gap": p.get("gap")})
    return sorted(out, key=lambda x: (x["data_iso"] or "", x["carga"]), reverse=True)


@router.get("/ajustes")
def rota_ajustes(request: Request, dias: int = 30):
    _carregar()
    lim = (datetime.now() - timedelta(days=max(1, min(dias, DIAS_EM_MEMORIA)))).strftime("%Y-%m-%d")
    out = []
    for c in CARGAS.values():
        if (c.get("data_iso") or "") >= lim or c.get("aberta"):
            for a in c.get("ajustes") or []:
                out.append({**a, "carga": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "versao_atual": c.get("versao"),
                            "status_carga": c.get("status")})
    return sorted(out, key=lambda x: x.get("em") or "", reverse=True)


@router.get("/skus")
def rota_skus(request: Request, dias: int = 30):
    """SKUs que apareceram nas cargas do período — base do cadastro de EAN/QR e do progresso."""
    _carregar()
    lim = (datetime.now() - timedelta(days=max(1, min(dias, DIAS_EM_MEMORIA)))).strftime("%Y-%m-%d")
    d: dict = {}
    pl: dict = {}
    for c in CARGAS.values():
        if (c.get("data_iso") or "") < lim:
            continue
        if c.get("placa"):
            pl.setdefault(c["placa"], {"placa": c["placa"], "transportadora": c.get("transportadora"), "cargas": 0})["cargas"] += 1
        for x in c.get("unidades") or []:
            if x.get("sku"):
                r = d.setdefault(x["sku"], {"sku": x["sku"], "desc": x.get("desc"), "unidades": 0})
                r["unidades"] += 1
    prods = produtos()
    for r in d.values():
        p = prods.get(r["sku"]) or {}
        r.update(eans=p.get("eans") or [], qr=bool(p.get("qr")))
    for r in pl.values():
        r["motorista"] = (placas().get(r["placa"]) or {}).get("motorista") or ""
    return {"skus": sorted(d.values(), key=lambda r: -r["unidades"]), "placas": sorted(pl.values(), key=lambda r: -r["cargas"])}


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
        if it.get("email") != q["email"] and nivel(request, "validacao") < 3:
            raise HTTPException(status_code=403, detail="Só quem registrou (ou o analista) pode remover.")
        ret["itens"].remove(it)
        gravar(c)
        return {"ok": True}


@router.post("/cargas/{numero}/retorno/concluir")
def rota_retorno_concluir(numero: str, payload: Texto, request: Request):
    exigir(request, "retorno", 2, "Concluir o retorno")
    q = quem(request)
    with TRAVA:
        c = carga(numero)
        _exigir_status(c, "validada", "em_retorno")
        ret = c.get("retorno") or {"inicio": agora(), "por": q["por"], "email": q["email"], "itens": []}
        ret["fim"] = {"em": agora(), "por": q["por"], "email": q["email"], "obs": str(payload.texto or "")[:500]}
        c["retorno"], c["status"] = ret, "retorno_concluido"
        hist(c, q, "retorno concluído", f"{len(ret['itens'])} unidade(s) retornada(s)")
        gravar(c)
        return publico(c)


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


@router.put("/cadastros/placa")
def rota_placa(payload: Placa, request: Request):
    exigir(request, "cadastros", 3, "Cadastrar motorista")
    q = quem(request)
    pl = re.sub(r"\s+", "", payload.placa.upper())
    with TRAVA:
        placas()[pl] = {"motorista": payload.motorista.strip()[:120], "por": q["por"], "em": agora()}
        _cad_salvar("placas")
        # cargas ainda abertas desta placa sem motorista recebem o nome
        for c in CARGAS.values():
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
                l[k] = str(v)[:500] if k == "clientes_transferencia" else [str(x).strip()[:120] for x in v if str(x).strip()][:60]
        _cad_salvar("listas")
    return l


@router.get("/painel")
def rota_painel(request: Request, dias: int = 30):
    return painel(min(max(dias, 1), DIAS_EM_MEMORIA))


@router.post("/recarregar")
def rota_recarregar(request: Request):
    exigir(request, "validacao", 3, "Recarregar")
    with TRAVA:
        CARGAS.clear()
        _CARREGADO["ok"] = False
        for k in CAD:
            CAD[k] = None
        CART.update(dias=None, lidos_em=0.0, pv={}, lidas={})
    return {"ok": True}
