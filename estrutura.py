"""
estrutura.py — MUDANÇAS DE ESTRUTURA NO ORGANOGRAMA (v6.5)

Movimentos, sem recadastrar ninguém:
  • "setor"          → um SUBSETOR vira SETOR novo (ex.: a perna Qualidade da Frota vira o setor Qualidade)
  • "mover"          → um SUBSETOR vai para OUTRO SETOR (ex.: Frota sai de Logística e vai para CX);
                       com subsetor_destino, junta-se a um subsetor que já existe lá
  • "setor_vira_sub" → um SETOR INTEIRO vira subsetor de outro setor (o setor antigo é excluído — fica guardado)
  • "pessoa"         → UMA PESSOA vai para outro setor

O que vai junto e o que é acertado (tudo sai no RESUMO antes de confirmar):
  • as pessoas, com cargo, nível, escala, horário e e-mail (mesmo id);
  • quem responde a quem: vínculos entre quem vai junto ficam; vínculo com quem fica é cortado
    (nos dois sentidos) e aparece no resumo para acertar;
  • PONTO: cada pessoa guarda "ponto_pid" = o código que o Ponto já usava ("<setor antigo>.<pessoa>"),
    então digitais, cadastro e batidas continuam dela;
  • (setor/mover) as ATIVIDADES do Diagnóstico daquele subsetor e os PROCESSOS ligados a elas,
    se "levar_atividades" estiver marcado;
  • nos dois setores fica uma linha no "historico_estrutura".

Funções puras (sem banco): o main.py lê os documentos, chama planejar() e grava o que voltar.
"""
import copy
import re
import secrets
import unicodedata
from datetime import datetime, timezone
from typing import Optional


def tx(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def nrm(v) -> str:
    s = unicodedata.normalize("NFD", tx(v).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


ORDEM_NIVEL = {"A": 0, "B": 1, "C": 2, "D": 3}


class Recusado(Exception):
    pass


def _quem_sai(origem: dict, modo: str, subsetor: str, pessoa_id: str) -> list:
    pessoas = origem.get("pessoas") or []
    if modo == "setor_vira_sub":
        if not pessoas:
            raise Recusado("Este setor não tem ninguém para levar.")
        return list(pessoas)
    if modo == "pessoa":
        p = next((x for x in pessoas if x.get("id") == pessoa_id), None)
        if not p:
            raise Recusado("Pessoa não encontrada neste setor (pode ter sido alterada agora há pouco).")
        return [p]
    sai = [x for x in pessoas if nrm(x.get("subsetor")) == nrm(subsetor)]
    if not tx(subsetor):
        raise Recusado("Escolha o subsetor.")
    if not sai:
        raise Recusado(f"O subsetor “{tx(subsetor)}” não tem ninguém neste setor.")
    return sai


def _nome(pessoas: list, pid: str) -> str:
    p = next((x for x in pessoas if x.get("id") == pid), None)
    return (tx(p.get("nome")) or "(vaga)") if p else ""


def _lider(sai: list) -> Optional[dict]:
    """Quem chefia o grupo: não responde a ninguém do grupo; desempate pelo nível mais alto."""
    ids = {p.get("id") for p in sai}
    topo = [p for p in sai if p.get("gestor_id") not in ids and not p.get("vaga") and tx(p.get("nome"))]
    topo = topo or [p for p in sai if not p.get("vaga") and tx(p.get("nome"))]
    topo.sort(key=lambda p: ORDEM_NIVEL.get(p.get("nivel"), 9))
    return topo[0] if topo else None


def planejar(origem_id: str, origem: dict, modo: str, subsetor: str = "", pessoa_id: str = "",
             destino_id: str = "", destino: Optional[dict] = None, novo: Optional[dict] = None,
             inventario_origem: Optional[list] = None, inventario_destino: Optional[list] = None,
             processos: Optional[list] = None, levar_atividades: bool = True, quem: str = "",
             subsetor_destino: Optional[str] = None) -> dict:
    """Devolve {"origem", "destino", "inventario_origem", "inventario_destino", "processos" (só os que mudam),
    "resumo"}. "destino" None + novo={nome,cor,descricao} = setor novo."""
    if modo not in ("setor", "mover", "pessoa", "setor_vira_sub"):
        raise Recusado("Movimento inválido.")
    origem = copy.deepcopy(origem or {})
    sai = _quem_sai(origem, modo, subsetor, pessoa_id)
    ids = {p.get("id") for p in sai}
    nome_origem = tx(origem.get("nome"))
    sub = tx(subsetor) if modo != "pessoa" else tx(sai[0].get("subsetor") if subsetor_destino is None else subsetor_destino)
    inteiro = modo == "setor_vira_sub"
    if inteiro:
        sub = nome_origem
    # subsetor com que as pessoas, atividades e processos chegam no destino ("" = sem subsetor, no setor novo)
    sub_dest = "" if modo == "setor" else tx(subsetor_destino) if (subsetor_destino is not None and tx(subsetor_destino)) else sub

    if modo == "setor":
        novo = novo or {}
        if not tx(novo.get("nome")):
            raise Recusado("Dê um nome ao setor novo.")
        dest = {"_tipo": "setor", "nome": tx(novo.get("nome")), "descricao": tx(novo.get("descricao")) or f"Veio de {nome_origem} ({sub}).",
                "cor": tx(novo.get("cor")) or origem.get("cor") or "", "responsavel": "", "subsetores": [], "pessoas": [], "pos_caixas": {}}
        nome_destino = dest["nome"]
    else:
        if destino is None:
            raise Recusado("Escolha o setor de destino.")
        if destino_id == origem_id:
            raise Recusado("O destino é o próprio setor.")
        dest = copy.deepcopy(destino)
        dest.setdefault("pessoas", []); dest.setdefault("subsetores", [])
        nome_destino = tx(dest.get("nome"))

    # ── vínculos de chefia ──
    cortados = []
    ids_dest = {p.get("id") for p in dest.get("pessoas") or []}
    novos_ids = {}
    movidos = []
    for p in sai:
        q = copy.deepcopy(p)
        q["ponto_pid"] = tx(p.get("ponto_pid")) or f"{origem_id}.{p.get('id')}"
        if q.get("gestor_id") and q["gestor_id"] not in ids:
            cortados.append(f"{tx(q.get('nome')) or '(vaga)'} respondia a {_nome(origem['pessoas'], q['gestor_id'])} — ficou sem gestor")
            q["gestor_id"] = ""
        q["extras"] = [x for x in (q.get("extras") or []) if x in ids]
        if q.get("abaixo_de") and q["abaixo_de"] not in ids:
            q["abaixo_de"] = ""
        q.pop("ox", None); q.pop("oy", None)   # posição ajustada à mão era do desenho antigo
        if modo == "setor":
            q["subsetor"] = ""
        elif modo == "pessoa" and subsetor_destino is not None:
            q["subsetor"] = tx(subsetor_destino)
        elif modo in ("mover", "setor_vira_sub"):
            if inteiro and tx(q.get("subsetor")):
                q["subsetor_anterior"] = tx(q.get("subsetor"))   # o organograma tem 2 níveis: o subsetor antigo fica anotado
            q["subsetor"] = sub_dest
        if q.get("id") in ids_dest:   # id repetido no destino (raríssimo): ganha id novo, o Ponto segue pelo ponto_pid
            novos_ids[q["id"]] = secrets.token_hex(5)
        movidos.append(q)
    for q in movidos:
        for campo in ("id", "gestor_id", "abaixo_de"):
            if q.get(campo) in novos_ids:
                q[campo] = novos_ids[q[campo]]
        q["extras"] = [novos_ids.get(x, x) for x in q["extras"]]
    ficam = []
    for p in origem.get("pessoas") or []:
        if p.get("id") in ids:
            continue
        if p.get("gestor_id") in ids:
            cortados.append(f"{tx(p.get('nome')) or '(vaga)'} respondia a {_nome(sai, p['gestor_id'])}, que saiu — ficou sem gestor")
            p["gestor_id"] = ""
        p["extras"] = [x for x in (p.get("extras") or []) if x not in ids]
        if p.get("abaixo_de") in ids:
            p["abaixo_de"] = ""
        ficam.append(p)
    origem["pessoas"] = ficam
    if modo != "pessoa":
        origem["subsetores"] = [s for s in (origem.get("subsetores") or []) if nrm(s if isinstance(s, str) else (s or {}).get("nome")) != nrm(sub)]
        origem["pos_caixas"] = {k: v for k, v in (origem.get("pos_caixas") or {}).items() if k.split("|")[0] not in ids}
    dest["pessoas"] = list(dest.get("pessoas") or []) + movidos
    novo_sub = sub_dest if modo != "pessoa" else sub
    if modo != "setor" and novo_sub and not any(nrm(s if isinstance(s, str) else (s or {}).get("nome")) == nrm(novo_sub) for s in dest["subsetores"]):
        dest["subsetores"] = list(dest["subsetores"]) + [novo_sub]
    if modo == "setor":
        lid = _lider(movidos)
        dest["responsavel"] = tx(lid.get("nome")) if lid else ""
    if modo != "pessoa" and nrm(origem.get("responsavel")) in {nrm(p.get("nome")) for p in sai}:
        origem["responsavel"] = ""

    # ── atividades do Diagnóstico e processos ──
    inv_o = copy.deepcopy(inventario_origem or [])
    inv_d = copy.deepcopy(inventario_destino or [])
    proc_mudam, ativs = [], []
    if modo != "pessoa" and levar_atividades:
        levar = [i for i in inv_o if isinstance(i, dict) and tx(i.get("atividade")) and (inteiro or nrm(i.get("subsetor")) == nrm(sub))]
        nomes = {nrm(i.get("atividade")) for i in levar}
        ja = {nrm(i.get("atividade")) for i in inv_d if isinstance(i, dict)}
        inv_o = [i for i in inv_o if not (isinstance(i, dict) and nrm(i.get("atividade")) in nomes)]
        for i in levar:
            ativs.append(tx(i.get("atividade")))
            if nrm(i.get("atividade")) not in ja:
                inv_d.append({**i, "subsetor": sub_dest})
        for p in processos or []:
            d = p.get("dados") or {}
            if not isinstance(d, dict) or d.get("_tipo"):
                continue
            o = d.get("origem") or {}
            do_setor = o.get("dep") == origem_id or ((inteiro or not o) and nrm(d.get("area")) == nrm(nome_origem))
            if not do_setor:
                continue
            liga = inteiro or nrm(o.get("atividade") or d.get("nome")) in nomes or (o and nrm(o.get("subsetor")) == nrm(sub))
            if not liga:
                continue
            nd = copy.deepcopy(d)
            nd["area"] = nome_destino
            antigo_sub = o.get("subsetor", "") if o else ""
            if nrm(nd.get("macroprocesso")) and nrm(nd.get("macroprocesso")) in (nrm(antigo_sub), nrm(sub)):
                nd["macroprocesso"] = sub_dest
            nd["origem"] = {**(o or {"atividade": tx(d.get("nome"))}), "dep": destino_id or "__novo__", "dep_nome": nome_destino, "subsetor": sub_dest}
            proc_mudam.append({"id": p["id"], "versao": p.get("versao"), "dados": nd})

    # ── histórico ──
    if modo == "setor":
        txt_o, txt_d = f"O subsetor {sub} virou o setor {nome_destino}", f"Setor criado a partir do subsetor {sub} de {nome_origem}"
    elif modo == "mover":
        para = f" (no subsetor {sub_dest})" if nrm(sub_dest) != nrm(sub) else ""
        txt_o, txt_d = f"O subsetor {sub} foi para {nome_destino}{para}", f"O subsetor {sub} veio de {nome_origem}{para}"
    elif inteiro:
        txt_o, txt_d = f"O setor {nome_origem} virou o subsetor {sub_dest} de {nome_destino}", f"O setor {nome_origem} entrou como subsetor {sub_dest}"
    else:
        n = tx(sai[0].get("nome")) or "(vaga)"
        txt_o, txt_d = f"{n} foi para {nome_destino}", f"{n} veio de {nome_origem}"
    marca = {"em": agora(), "por": quem}
    origem["historico_estrutura"] = list(origem.get("historico_estrutura") or []) + [{**marca, "texto": txt_o}]
    dest["historico_estrutura"] = list(dest.get("historico_estrutura") or []) + [{**marca, "texto": txt_d}]

    resumo = {"modo": modo, "subsetor": sub, "subsetor_destino": sub_dest if modo != "pessoa" else sub, "origem": nome_origem,
              "origem_excluida": inteiro, "destino": nome_destino, "novo_setor": modo == "setor",
              "pessoas": [tx(p.get("nome")) or "(vaga)" for p in sai], "responsavel_novo": dest.get("responsavel", "") if modo == "setor" else "",
              "vinculos_cortados": cortados, "atividades": ativs,
              "processos": [f"{tx(p['dados'].get('codigo'))} {tx(p['dados'].get('nome'))}".strip() for p in proc_mudam],
              "texto": txt_o}
    return {"origem": origem, "destino": dest, "inventario_origem": inv_o, "inventario_destino": inv_d,
            "processos": proc_mudam, "resumo": resumo}
