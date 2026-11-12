"""
conhecimento.py — TUDO O QUE ESTÁ ESCRITO VIRA BASE DO AGENTE (v7.3)

"A IA não tem limites: o que está escrito, em qualquer lugar, em qualquer campo, vira base de dados."

Funções puras (sem banco): recebem o que está gravado e devolvem ARTIGOS GERADOS para o agente.
Nada aqui é digitado à mão — muda a fonte, muda o artigo (o agente refaz a cada gravação).

  • org-<setor>             o setor no Organograma: gestor (topo), subsetores, todas as pessoas com todos os campos,
                            quem responde a quem, vagas
  • pes-<setor>-<pessoa>    uma pessoa: cargo, setor, gestor, quem responde a ela, atividades que executa,
                            processos em que é responsável (RELAÇÃO CRUZADA)
  • ativ-<setor>-<n>        uma atividade (Fluxo interno · Atividades): setor, subsetor, executores, modo,
                            categoria, descrição e os processos ligados a ela
  • diag-<setor>-<secao>    qualquer outra seção do Diagnóstico, campo a campo
  • proc-<processo>         a FICHA do processo: todos os campos (identificação, como é feito hoje, confronto,
                            oportunidades, TO BE, validação, implantação, anotações…). O passo a passo continua no mapa-<id>.
  • mlh-<card> / ind-<id>   Melhoria Contínua: cada melhoria e cada indicador, campo a campo

A versão de um artigo gerado é um resumo (hash) do conteúdo: mudou o texto, mudou a versão
(é assim que a resposta guardada de uma pergunta sabe que precisa ser refeita).
"""
import hashlib
import json
import re
import unicodedata
from typing import Callable, Dict, List, Optional

LIMITE_ARTIGO = 6000        # caracteres por artigo gerado (o que passa disso vai cortado com "…")
SINONIMOS_GESTOR = "gestor gestora gerente responsavel lider chefe coordenador coordenadora supervisor diretor topo encarregado"

# campos que não são conteúdo (posição na tela, cor, foto, controle interno)
_IGNORAR = {"id", "x", "y", "ox", "oy", "w", "h", "foto", "cor", "versao", "ordem", "largura", "altura", "zoom",
            "nos", "ligacoes", "raias", "fluxo", "_tipo", "dup", "rep"}
_ROTULO = {"descricao": "descrição", "observacao": "observação", "obs": "observação", "regra": "regra", "dono": "responsável",
           "area": "setor", "validacao_tobe": "validação do TO BE", "tobe": "TO BE (como vai ficar)", "it": "IT oficial",
           "passos_real": "como é feito hoje", "passos_it": "passos da IT", "implantacao": "implantação",
           "confronto": "confronto e gaps", "oportunidades": "oportunidades", "ajustes_agente": "correções do agente",
           "gestor_id": "responde a", "extras": "também responde a", "abaixo_de": "abaixo de", "escala": "escala",
           "horario": "horário", "ramal": "ramal", "nivel": "nível", "subsetor": "subsetor", "vaga": "vaga em aberto"}


def tx(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def sem_acento(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sem_acento(s)).strip("-")[:40] or "x"


def versao_de(art: dict) -> str:
    base = json.dumps([art.get("titulo"), art.get("passos"), art.get("palavras")], ensure_ascii=False, sort_keys=True)
    return "g" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]


def rotulo(k: str) -> str:
    return _ROTULO.get(k, str(k).replace("_", " ").strip())


def _valor_simples(v) -> Optional[str]:
    if v is None or v == "" or v == [] or v == {}:
        return None
    if isinstance(v, bool):
        return "sim" if v else None
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        t = tx(v)
        if not t or re.fullmatch(r"[0-9a-f]{16,}|[A-Za-z0-9_-]{20,}", t):   # cara de id interno: não é conteúdo
            return None
        return t
    return None


def achatar(obj, prefixo: str = "", limite: int = LIMITE_ARTIGO) -> List[str]:
    """Qualquer JSON → linhas "campo: valor". Listas de objetos viram uma linha por item. Nada é perdido, só o que é
    controle de tela (posição, cor, ids)."""
    linhas: List[str] = []
    tam = [0]

    def por(t: str) -> bool:
        if tam[0] + len(t) > limite:
            if not linhas or linhas[-1] != "…":
                linhas.append("…")
            return False
        linhas.append(t); tam[0] += len(t)
        return True

    def item_txt(d: dict) -> str:
        partes = []
        for k, v in d.items():
            if k in _IGNORAR or str(k).startswith("_"):
                continue
            s = _valor_simples(v)
            if s is None and isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
                s = ", ".join(t for t in (_valor_simples(x) for x in v) if t) or None
            if s is None and isinstance(v, dict):
                s = "; ".join(f"{rotulo(k2)}: {t}" for k2, v2 in v.items() for t in [_valor_simples(v2)] if t and k2 not in _IGNORAR) or None
            if s:
                partes.append(f"{rotulo(k)}: {s}")
        return " · ".join(partes)

    def andar(o, pref: str) -> bool:
        if isinstance(o, dict):
            for k, v in o.items():
                if k in _IGNORAR or str(k).startswith("_"):
                    continue
                if not andar(v, f"{pref} › {rotulo(k)}" if pref else rotulo(k)):
                    return False
            return True
        if isinstance(o, list):
            simples = [t for t in (_valor_simples(x) for x in o if not isinstance(x, (dict, list))) if t]
            if simples and not por(f"{pref}: {', '.join(simples)}" if pref else ", ".join(simples)):
                return False
            n = 0
            for x in o:
                if isinstance(x, dict):
                    t = item_txt(x)
                    if t:
                        n += 1
                        if not por(f"{pref} {n}: {t}" if pref else t):
                            return False
                elif isinstance(x, list) and not andar(x, pref):
                    return False
            return True
        s = _valor_simples(o)
        return por(f"{pref}: {s}" if pref else s) if s else True

    andar(obj, prefixo)
    return linhas


def _art(aid: str, titulo: str, passos: List[str], chaves: List[str], setor: str = "", origem: Optional[dict] = None) -> dict:
    tot, out = 0, []
    for p in passos:
        if tot + len(p) > LIMITE_ARTIGO:
            out.append("…"); break
        out.append(p); tot += len(p)
    a = {"id": aid, "titulo": tx(titulo)[:160], "setor": tx(setor), "passos": out,
         "palavras": " ".join(tx(c) for c in chaves if tx(c)), "ativo": True, "gerado": True, "origem": origem or {}}
    a["versao"] = versao_de(a)
    return a


# ══════════════════════════════════════════════════════════════════════════
# Organograma: setores e pessoas
# ══════════════════════════════════════════════════════════════════════════
def _nome_p(p: dict) -> str:
    return tx(p.get("nome")) or ("(vaga) " + tx(p.get("cargo"))).strip()


def _linha_pessoa(p: dict, nomes: dict) -> str:
    partes = [_nome_p(p)]
    for campo, rot in (("cargo", "cargo"), ("subsetor", "subsetor"), ("nivel", "nível"), ("email", "e-mail"), ("ramal", "ramal"),
                       ("escala", "escala"), ("horario", "horário"), ("obs", "observação")):
        if tx(p.get(campo)):
            partes.append(f"{rot}: {tx(p.get(campo))}")
    if p.get("vaga"):
        partes.append("vaga em aberto")
    if p.get("gestor_id") in nomes:
        partes.append("responde a: " + nomes[p["gestor_id"]])
    tamb = [nomes[x] for x in (p.get("extras") or []) if x in nomes]
    if tamb:
        partes.append("também responde a: " + ", ".join(tamb))
    outros = item_extra(p)
    if outros:
        partes.append(outros)
    return " · ".join(partes)


_CAMPOS_PESSOA = {"nome", "cargo", "subsetor", "nivel", "email", "ramal", "escala", "horario", "obs", "vaga", "gestor_id", "extras",
                  "abaixo_de", "ponto_pid"}


def item_extra(p: dict) -> str:
    """Campos da pessoa que não estão na lista conhecida (qualquer campo novo também vira base)."""
    resto = {k: v for k, v in p.items() if k not in _CAMPOS_PESSOA}
    return " · ".join(achatar(resto, limite=600))


def _mesma_pessoa(k: str, nome: str, cargo: str) -> bool:
    """"João", "joão silva" ou o cargo ("conferente") escritos em Executores/Responsável apontam para esta pessoa."""
    if not k:
        return False
    return k == nome or nome.startswith(k + " ") or k.startswith(nome + " ") or (bool(cargo) and k == cargo)


def _topo(pessoas: List[dict]) -> List[dict]:
    """Quem está no topo: não responde a ninguém do próprio setor (se ninguém, o de menor nível)."""
    ids = {p.get("id") for p in pessoas}
    topo = [p for p in pessoas if p.get("gestor_id") not in ids]
    if topo and len(topo) < len(pessoas):
        return topo
    def nv(p):
        m = re.search(r"\d+", tx(p.get("nivel")))
        return int(m.group()) if m else 99
    menor = min((nv(p) for p in pessoas), default=99)
    return [p for p in pessoas if nv(p) == menor] or topo


def artigos_organograma(setores: List[dict], atividades_por_pessoa: Dict[str, list], processos_por_dono: Dict[str, list]) -> List[dict]:
    out = []
    for s in setores:
        d = s.get("dados") or {}
        if not isinstance(d, dict) or d.get("_tipo") or not tx(d.get("nome")):
            continue
        setor = tx(d.get("nome"))
        pessoas = [p for p in d.get("pessoas") or [] if isinstance(p, dict)]
        nomes = {p.get("id"): _nome_p(p) for p in pessoas}
        reais = [p for p in pessoas if tx(p.get("nome"))]
        topo = _topo(reais)
        linhas = [f"Setor {setor} no Organograma (Fluxo interno · Organograma). Atualizado sozinho a cada alteração."]
        if tx(d.get("descricao")):
            linhas.append("Descrição do setor: " + tx(d.get("descricao")))
        if topo:
            linhas.append(f"GESTOR / responsável pelo setor {setor} (topo do organograma): "
                          + "; ".join(f"{_nome_p(p)}" + (f" ({tx(p.get('cargo'))})" if tx(p.get("cargo")) else "") for p in topo) + ".")
        subs: Dict[str, list] = {}
        for p in pessoas:
            subs.setdefault(tx(p.get("subsetor")) or "(sem subsetor)", []).append(p)
        sub_nomes = [k for k in subs if k != "(sem subsetor)"]
        if sub_nomes:
            linhas.append(f"Subsetores de {setor}: " + ", ".join(sub_nomes) + ".")
            for sn in sub_nomes:
                t = _topo([p for p in subs[sn] if tx(p.get("nome"))])
                if t:
                    linhas.append(f"Responsável pelo subsetor {sn}: " + "; ".join(_nome_p(p) + (f" ({tx(p.get('cargo'))})" if tx(p.get("cargo")) else "") for p in t) + ".")
        linhas.append(f"Pessoas do setor {setor} ({len(reais)} pessoas" + (f", {len(pessoas) - len(reais)} vagas" if len(pessoas) > len(reais) else "") + "):")
        linhas += [_linha_pessoa(p, nomes) for p in pessoas]
        resto = {k: v for k, v in d.items() if k not in ("nome", "descricao", "pessoas", "historico_estrutura")}
        linhas += achatar(resto, limite=1500)
        hist = d.get("historico_estrutura") or []
        if hist:
            linhas += ["Mudanças de estrutura: " + " | ".join(t for t in (" · ".join(achatar(h, limite=300)) for h in hist[-8:]) if t)]
        chaves = [setor, "setor organograma", SINONIMOS_GESTOR] + sub_nomes + [_nome_p(p) for p in reais] + [tx(p.get("cargo")) for p in pessoas]
        out.append(_art(f"org-{s.get('id')}", f"Setor {setor} — organograma, gestor e pessoas", linhas, chaves, setor,
                        {"tipo": "organograma", "setor_id": s.get("id")}))
        # uma por pessoa (relação cruzada: atividades e processos dela)
        filhos: Dict[str, list] = {}
        for p in pessoas:
            for g in [p.get("gestor_id")] + list(p.get("extras") or []):
                if g:
                    filhos.setdefault(g, []).append(p)
        for p in reais:
            nm = _nome_p(p)
            lin = [f"{nm} trabalha no setor {setor}" + (f", subsetor {tx(p.get('subsetor'))}" if tx(p.get("subsetor")) else "")
                   + (f", cargo {tx(p.get('cargo'))}" if tx(p.get("cargo")) else "") + ".",
                   "Ficha no Organograma: " + _linha_pessoa(p, nomes)]
            if p in topo:
                lin.append(f"{nm} é gestor(a) / responsável pelo setor {setor} (topo do organograma).")
            if filhos.get(p.get("id")):
                lin.append(f"Respondem a {nm}: " + ", ".join(_nome_p(f) + (f" ({tx(f.get('cargo'))})" if tx(f.get("cargo")) else "") for f in filhos[p["id"]]) + ".")
            chave, cargo = sem_acento(nm), sem_acento(tx(p.get("cargo")))
            atv = [a for k, lst in atividades_por_pessoa.items() if _mesma_pessoa(k, chave, cargo) for a in lst]
            if atv:
                lin.append(f"Atividades que {nm} executa: " + "; ".join(sorted(set(atv))) + ".")
            procs = [x for k, lst in processos_por_dono.items() if _mesma_pessoa(k, chave, "") for x in lst]
            if procs:
                lin.append(f"Processos em que {nm} é responsável: " + "; ".join(sorted(set(procs))) + ".")
            out.append(_art(f"pes-{s.get('id')}-{p.get('id') or slug(nm)}", f"{nm} — {tx(p.get('cargo')) or 'pessoa'} em {setor}", lin,
                            [nm, tx(p.get("cargo")), setor, tx(p.get("subsetor")), tx(p.get("email"))], setor,
                            {"tipo": "organograma", "setor_id": s.get("id"), "pessoa_id": p.get("id")}))
    return out


# ══════════════════════════════════════════════════════════════════════════
# Atividades e demais seções do Diagnóstico
# ══════════════════════════════════════════════════════════════════════════
def _nomes_executores(t: str) -> List[str]:
    return [sem_acento(x).strip() for x in re.split(r"[,;/\n]| e ", tx(t)) if len(sem_acento(x).strip()) >= 3]


def artigos_atividades(setores: List[dict], diag: Dict[str, dict], processos: List[dict]) -> List[dict]:
    nome_setor = {s.get("id"): tx((s.get("dados") or {}).get("nome")) for s in setores}
    # processo ligado a cada atividade (pela origem do processo, ou pelo nome)
    proc_de: Dict[str, list] = {}
    for r in processos:
        d = r.get("dados") or {}
        if not isinstance(d, dict) or not tx(d.get("nome")):
            continue
        o = d.get("origem") or {}
        rotulo_p = f"{tx(d.get('codigo'))} {tx(d.get('nome'))}".strip()
        for k in {sem_acento(o.get("atividade")), sem_acento(d.get("nome"))}:
            if k:
                proc_de.setdefault(k, []).append(rotulo_p)
    out = []
    for dep, secoes in (diag or {}).items():
        setor = nome_setor.get(dep) or dep
        for secao, dados in (secoes or {}).items():
            if secao == "inventario" and isinstance(dados, list):
                for i, a in enumerate(dados):
                    if not isinstance(a, dict) or not tx(a.get("atividade")):
                        continue
                    nm = tx(a.get("atividade"))
                    lin = [f"Atividade “{nm}” do setor {setor}" + (f", subsetor {tx(a.get('subsetor'))}" if tx(a.get("subsetor")) else "")
                           + " (Fluxo interno · Atividades)."]
                    if tx(a.get("descricao")):
                        lin.append(f"O que é {nm}: {tx(a.get('descricao'))}")
                    if tx(a.get("executores")):
                        lin.append(f"Quem executa {nm}: {tx(a.get('executores'))}.")
                    resto = {k: v for k, v in a.items() if k not in ("atividade", "descricao", "executores", "subsetor")}
                    lin += achatar(resto, limite=1500)
                    procs = proc_de.get(sem_acento(nm)) or []
                    lin.append(("Processo mapeado desta atividade: " + "; ".join(procs) + ".") if procs
                               else "Esta atividade ainda não virou processo mapeado.")
                    out.append(_art(f"ativ-{dep}-{slug(nm)}", f"Atividade {nm} — {setor}", lin,
                                    [nm, "atividade", setor, tx(a.get("subsetor")), tx(a.get("executores")), tx(a.get("categoria"))], setor,
                                    {"tipo": "atividade", "setor_id": dep, "atividade": nm}))
                continue
            lin = achatar(dados)
            if not lin:
                continue
            nm_sec = rotulo(secao)
            out.append(_art(f"diag-{dep}-{secao}", f"{nm_sec.capitalize()} — {setor} (diagnóstico)",
                            [f"Seção “{nm_sec}” do diagnóstico do setor {setor}."] + lin, [nm_sec, setor, "diagnostico"], setor,
                            {"tipo": "diagnostico", "setor_id": dep, "secao": secao}))
    return out


def atividades_por_pessoa(diag: Dict[str, dict]) -> Dict[str, list]:
    r: Dict[str, list] = {}
    for dep, secoes in (diag or {}).items():
        for a in ((secoes or {}).get("inventario") or []):
            if isinstance(a, dict) and tx(a.get("atividade")):
                for n in _nomes_executores(a.get("executores")):
                    r.setdefault(n, []).append(tx(a.get("atividade")))
    return r


# ══════════════════════════════════════════════════════════════════════════
# Processos (ficha completa) e Melhoria Contínua
# ══════════════════════════════════════════════════════════════════════════
_SO_NO_PASSO_A_PASSO = {"passos_real", "passos_it", "fluxo", "ajustes_agente"}


def artigos_processos(processos: List[dict]) -> List[dict]:
    out = []
    for r in processos:
        d = r.get("dados") or {}
        if not isinstance(d, dict) or d.get("_tipo") or not tx(d.get("nome")):
            continue
        cod = tx(d.get("codigo"))
        resto = {k: v for k, v in d.items() if k not in _SO_NO_PASSO_A_PASSO}
        if isinstance(resto.get("tobe"), dict):
            resto["tobe"] = {k: v for k, v in resto["tobe"].items() if k != "passos"}
        lin = [f"Ficha completa do processo {cod} {tx(d.get('nome'))} (Fluxo interno · Processos e Melhoria Contínua). "
               "O passo a passo oficial está no artigo do processo."] + achatar(resto)
        out.append(_art(f"proc-{r.get('id')}", f"{tx(d.get('nome'))} ({cod}) — ficha completa do processo", lin,
                        [tx(d.get("nome")), cod, tx(d.get("area")), tx(d.get("dono")), tx(d.get("macroprocesso")), "processo ficha"],
                        tx(d.get("area")), {"tipo": "processo_ficha", "processo_id": r.get("id"), "codigo": cod}))
    return out


def processos_por_dono(processos: List[dict]) -> Dict[str, list]:
    r: Dict[str, list] = {}
    for x in processos:
        d = x.get("dados") or {}
        if isinstance(d, dict) and tx(d.get("nome")) and tx(d.get("dono")):
            for n in _nomes_executores(d.get("dono")):
                r.setdefault(n, []).append(f"{tx(d.get('codigo'))} {tx(d.get('nome'))}".strip())
    return r


def artigos_melhoria(cards: List[dict], indicadores: List[dict]) -> List[dict]:
    out = []
    nome_ind = {i.get("id"): tx((i.get("dados") or {}).get("nome")) for i in indicadores}
    for c in cards:
        d = c.get("dados") or {}
        if not isinstance(d, dict):
            continue
        tit = tx(d.get("titulo") or d.get("nome")) or "Melhoria"
        cod = tx(d.get("codigo"))
        lin = [f"Melhoria Contínua {cod} “{tit}”."]
        if d.get("indicador_id") in nome_ind:
            lin.append("Indicador desta melhoria: " + nome_ind[d["indicador_id"]] + ".")
        lin += achatar(d)
        out.append(_art(f"mlh-{c.get('id')}", f"Melhoria {cod} — {tit}".replace("  ", " "), lin,
                        [tit, cod, "melhoria continua", tx(d.get("setor") or d.get("area")), tx(d.get("responsavel"))],
                        tx(d.get("setor") or d.get("area")), {"tipo": "melhoria", "card_id": c.get("id")}))
    for i in indicadores:
        d = i.get("dados") or {}
        if not isinstance(d, dict) or not tx(d.get("nome")):
            continue
        lin = [f"Indicador “{tx(d.get('nome'))}” (Melhoria Contínua)."] + achatar(d)
        out.append(_art(f"ind-{i.get('id')}", f"Indicador {tx(d.get('nome'))}", lin,
                        [tx(d.get("nome")), "indicador", tx(d.get("setor") or d.get("area")), tx(d.get("responsavel") or d.get("gestor"))],
                        tx(d.get("setor") or d.get("area")), {"tipo": "indicador", "indicador_id": i.get("id")}))
    return out


# ══════════════════════════════════════════════════════════════════════════
def gerar(fontes: dict) -> List[dict]:
    """fontes = {setores, diag:{dep:{secao:dados}}, processos, melhoria_cards, melhoria_indicadores}."""
    setores = [s for s in fontes.get("setores") or [] if isinstance(s, dict)]
    diag = fontes.get("diag") or {}
    procs = [p for p in fontes.get("processos") or [] if isinstance(p, dict)]
    arts = artigos_organograma(setores, atividades_por_pessoa(diag), processos_por_dono(procs))
    arts += artigos_atividades(setores, diag, procs)
    arts += artigos_processos(procs)
    arts += artigos_melhoria(fontes.get("melhoria_cards") or [], fontes.get("melhoria_indicadores") or [])
    vistos, unicos = set(), []
    for a in arts:
        if a["id"] not in vistos:
            vistos.add(a["id"]); unicos.append(a)
    return unicos


def aplicar_correcoes(art: dict, correcoes: List[dict]) -> dict:
    """Correções (DE → PARA) de quem cuida do agente: trocam o texto quando acham, e sempre ficam no topo do artigo."""
    corr = [c for c in correcoes if tx(c.get("para"))]
    if not corr:
        return art
    a = dict(art, passos=list(art.get("passos") or []))
    for c in corr:
        de = tx(c.get("de"))
        if de:
            padrao = r"\s+".join(re.escape(w) for w in de.split())
            a["passos"] = [re.sub(padrao, lambda _m: tx(c["para"]), p, flags=re.IGNORECASE) for p in a["passos"]]
    a["passos"].insert(0, "CORREÇÕES (valem mais que o resto do texto): " + " ".join(
        f"Onde se dizia “{tx(c.get('de'))}”, o correto é: {tx(c.get('para'))}." for c in corr))
    a["palavras"] = (a.get("palavras") or "") + " " + " ".join(tx(c.get("para")) for c in corr)
    a["versao"] = versao_de(a)
    return a
