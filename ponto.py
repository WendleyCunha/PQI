"""
ponto.py — módulo PONTO (registro de ponto por digital) dentro do PQI.

Quem usa: só quem o administrador liberar em Segurança (pensado para o perfil
GERENTE; o administrador sempre vê tudo). A permissão é POR ABA, como na
Qualidade — então dá para dar a uma pessoa de alçada menor só a aba Cadastros,
com edição completa, sem abrir o resto.

Abas
  hoje        quem entrou, quem está no intervalo, quem saiu, quem faltou (tempo real)
  marcar      a tela de BATER PONTO (fica aberta no computador da loja com o leitor)
  espelho     batidas e saldo do dia a dia de cada pessoa
  tratamento  inconsistências e ajustes (motivo obrigatório)
  cadastros   colaboradores (vindos do Organograma), digitais, unidades, terminais e regras

REGRAS QUE NÃO SE QUEBRAM
  1. A marcação é IMUTÁVEL: não existe rota para alterar nem apagar batida.
     Cada uma recebe um NSR (número sequencial, sem buraco) e um HASH encadeado
     com a anterior — qualquer mexida direta no banco quebra a corrente.
     Ajuste é SEMPRE um registro novo em pto_ajustes, com motivo e autor.
  2. Nunca bloqueia batida por horário. O sistema só sinaliza.
  3. Hora oficial = a do servidor (Brasília). A do aparelho fica só como evidência.
  4. O Organograma é o dono das pessoas: o Ponto só LÊ (nome, cargo, setor,
     gestor, escala, horário). Os dados que só o ponto usa (CPF, matrícula,
     unidade, jornada estruturada, digitais) ficam em pto_colab.
  5. Digital: nunca a imagem — só o "template" do leitor, cifrado (AES-256-GCM)
     com a chave PONTO_CHAVE (variável de ambiente no Render, fora do banco).

O TERMINAL É O LOGIN  [v5.2]
  Quem entra no PQI com a aba "Bater ponto" liberada transforma aquele aparelho
  no terminal — sem escolher nada e sem segredo para digitar. O terminal nasce
  sozinho no primeiro uso (um por login) e a UNIDADE também é automática:
    1º o Organograma: a pessoa do login (mesmo e-mail) tem unidade no ponto;
    2º senão, a unidade de quem bater o primeiro ponto ali.
  Dá para corrigir em Cadastros → Terminais, e fica gravado para aquele login.

COMO A DIGITAL CHEGA AQUI
  O leitor USB fica ligado no aparelho (PC com o AGENTE, pasta kingponto-agente/,
  ou celular Android com o app). Ao abrir "Bater ponto", a tela entrega ao agente
  a chave do terminal daquele login (gerada pelo servidor, ninguém digita nada).
  Na batida: tela pede um DESAFIO → agente lê o dedo, identifica a pessoa e
  ASSINA (desafio + pessoa + hora) → servidor confere. O desafio vale uma vez só
  e por 2 minutos; a batida fica gravada com o login e o terminal que a fizeram.

Coleções do Firestore (prefixo pto_)
  pto_marc     1 documento por batida (id = NSR com 10 dígitos)   ← nunca alterado
  pto_ajustes  ajustes do RH/gestor (incluir, desconsiderar, abonar, justificar)
  pto_colab    cadastro de ponto de cada pessoa do Organograma (id = setor.pessoa)
  pto_config   "geral" (unidades, terminais, regras, feriados) e "contador" (último NSR + hash)

Economia do plano grátis: as batidas dos últimos dias ficam em memória (lidas uma
vez); toda gravação passa por aqui e atualiza a memória.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import db_diario as db

try:   # cifra da digital — se a biblioteca faltar, o resto do Ponto funciona (só a digital fica desligada)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:  # pragma: no cover
    AESGCM = None

MODULO = "ponto"
COL_MARC = "pto_marc"
COL_AJ = "pto_ajustes"
COL_COLAB = "pto_colab"
COL_CFG = "pto_config"
COL_ORG = "organograma_setores"
DIAS_EM_MEMORIA = 70

BR = timezone(timedelta(hours=-3), "BRT")   # Brasília (sem horário de verão desde 2019)
TIPOS = ["Entrada", "Saída p/ intervalo", "Volta do intervalo", "Saída"]

router = APIRouter(prefix="/api/ponto")
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


def _fs_todos(col: str) -> list:
    return [{**(d.to_dict() or {}), "_id": d.id} for d in db.get_db().collection(col).stream(timeout=db.TIMEOUT_FS)]


def _fs_periodo(col: str, campo: str, de: str, ate: str) -> list:
    q = db.get_db().collection(col).where(campo, ">=", de).where(campo, "<=", ate)
    return [d.to_dict() for d in q.stream(timeout=db.TIMEOUT_FS)]


def _fs_gravar_marcacao(montar: Callable[[dict], dict]) -> dict:
    """Transação: lê o contador, monta a batida com o próximo NSR e grava as duas
    coisas juntas. Dois terminais batendo no mesmo segundo nunca pegam o mesmo NSR."""
    from google.cloud import firestore as _fs
    cli = db.get_db()
    ref_cont = cli.collection(COL_CFG).document("contador")
    tx = cli.transaction()

    @_fs.transactional
    def _t(t):
        snap = ref_cont.get(transaction=t)
        cont = snap.to_dict() if snap.exists else {"nsr": 0, "hash": ""}
        m = montar(cont)
        t.set(cli.collection(COL_MARC).document(f"{m['nsr']:010d}"), m)
        t.set(ref_cont, {"nsr": m["nsr"], "hash": m["hash"], "em": m["momento"]})
        return m
    return _t(tx)


def _fs_organograma() -> list:
    return db.docs_listar(COL_ORG)


# ══════════════════════════════════════════════════════════════════════════
# utilidades
# ══════════════════════════════════════════════════════════════════════════
def agora() -> datetime:
    return datetime.now(BR)


def hoje() -> str:
    return agora().strftime("%Y-%m-%d")


def iso_ok(s: Optional[str], padrao: str) -> str:
    return s if s and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else padrao


def hm_ok(s) -> Optional[str]:
    s = str(s or "").strip()
    m = re.fullmatch(r"(\d{1,2})[:hH](\d{2})", s)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def minutos(hm: Optional[str]) -> Optional[int]:
    hm = hm_ok(hm)
    return int(hm[:2]) * 60 + int(hm[3:]) if hm else None


def fmt_min(m: int) -> str:
    s = "-" if m < 0 else ""
    m = abs(int(m))
    return f"{s}{m // 60:02d}:{m % 60:02d}"


def dias(de: str, ate: str) -> List[str]:
    d0, d1 = date.fromisoformat(de), date.fromisoformat(ate)
    if d1 < d0 or (d1 - d0).days > 400:
        raise HTTPException(400, "Período inválido (até 400 dias).")
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def so_digitos(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def cpf_valido(cpf: str) -> bool:
    c = so_digitos(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(c[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11 % 10
        if dv != int(c[n]):
            return False
    return True


def quem(request: Request) -> dict:
    u = H["usuario_atual"](request)
    return {"por": u.get("nome") or u.get("email"), "email": u.get("email"), "admin": u.get("papel") == "admin"}


def exigir(request: Request, abas, minimo: int, acao: str) -> None:
    H["exigir_aba"](request, MODULO, abas, minimo, acao)


def nivel(request: Request, aba: str) -> int:
    try:
        return int(H["nivel_aba"](request, MODULO, aba))
    except Exception:
        return 0


TRAVA = threading.RLock()


# ══════════════════════════════════════════════════════════════════════════
# CRIPTOGRAFIA (digital e terminais)
# ══════════════════════════════════════════════════════════════════════════
_SEGREDO_PROCESSO = secrets.token_bytes(32)   # só para os desafios (valem 2 minutos)


def _chave_mestra() -> Optional[bytes]:
    bruto = os.environ.get("PONTO_CHAVE", "").strip()
    if not bruto:
        return None
    try:
        k = base64.urlsafe_b64decode(bruto + "=" * (-len(bruto) % 4))
    except Exception:
        k = b""
    return k if len(k) == 32 else hashlib.sha256(bruto.encode("utf-8")).digest()


def cripto_ok() -> bool:
    return AESGCM is not None and _chave_mestra() is not None


def _exigir_cripto() -> bytes:
    if AESGCM is None:
        raise HTTPException(503, "A biblioteca de criptografia (cryptography) não está instalada no servidor.")
    k = _chave_mestra()
    if not k:
        raise HTTPException(503, "Falta a variável PONTO_CHAVE no Render — sem ela a digital não é guardada. "
                                 "Peça ao administrador para configurar (ver PONTO-LEIA.md).")
    return k


def cifrar(chave: bytes, dados: bytes, contexto: bytes = b"") -> str:
    n = secrets.token_bytes(12)
    return base64.b64encode(n + AESGCM(chave).encrypt(n, dados, contexto or None)).decode("ascii")


def decifrar(chave: bytes, texto: str, contexto: bytes = b"") -> bytes:
    bruto = base64.b64decode(texto)
    return AESGCM(chave).decrypt(bruto[:12], bruto[12:], contexto or None)


def _k_terminal(segredo: str, uso: str) -> bytes:
    """Mesma conta que o agente faz: a chave de cada uso sai do segredo do terminal."""
    return hashlib.sha256(f"kingponto|{uso}|{segredo}".encode("utf-8")).digest()


def novo_desafio() -> str:
    carga = f"{int(time.time())}.{secrets.token_hex(8)}"
    return carga + "." + hmac.new(_SEGREDO_PROCESSO, carga.encode(), hashlib.sha256).hexdigest()[:32]


_DESAFIOS_USADOS: dict = {}


def desafio_valido(d: str) -> bool:
    try:
        ts, rnd, sig = d.split(".")
    except ValueError:
        return False
    esperado = hmac.new(_SEGREDO_PROCESSO, f"{ts}.{rnd}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(esperado, sig) or abs(time.time() - int(ts)) > 120:
        return False
    with TRAVA:
        for k in [k for k, v in _DESAFIOS_USADOS.items() if v < time.time() - 300]:
            _DESAFIOS_USADOS.pop(k, None)
        if d in _DESAFIOS_USADOS:
            return False
        _DESAFIOS_USADOS[d] = time.time()
    return True


def assinatura_esperada(segredo: str, desafio: str, pessoa: str, score: int, momento_aparelho: str) -> str:
    msg = f"{desafio}|{pessoa}|{int(score)}|{momento_aparelho}".encode("utf-8")
    return hmac.new(_k_terminal(segredo, "assina"), msg, hashlib.sha256).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO (unidades, terminais, regras, feriados)
# ══════════════════════════════════════════════════════════════════════════
REGRAS_PADRAO = {
    "tol_marcacao": 5,       # minutos de tolerância por batida (CLT art. 58 §1º)
    "tol_dia": 10,           # limite diário da tolerância
    "intervalo_min_6h": 60,  # jornada acima de 6 h exige 1 h de intervalo
    "intervalo_min_4h": 15,  # jornada de 4 a 6 h exige 15 min
    "interjornada_h": 11,    # descanso mínimo entre um dia e outro
    "extra_max_dia": 120,    # alerta quando passar de 2 h extras no dia
    "duplicada_min": 2,      # batida repetida em menos de 2 min não grava (avisa a hora da anterior)
    "periodo_inicio": 21,    # fechamento do dia 21 ao dia 20
}
_cfg: Optional[dict] = None


def cfg() -> dict:
    global _cfg
    with TRAVA:
        if _cfg is None:
            d = _fs_get(COL_CFG, "geral") or {}
            _cfg = {"unidades": d.get("unidades") or [], "terminais": d.get("terminais") or [],
                    "regras": {**REGRAS_PADRAO, **(d.get("regras") or {})}, "feriados": d.get("feriados") or []}
        return _cfg


def _salvar_cfg() -> None:
    with TRAVA:
        _fs_set(COL_CFG, "geral", cfg())


def terminal(tid: str) -> Optional[dict]:
    return next((t for t in cfg()["terminais"] if t.get("id") == tid), None)


def terminal_publico(t: dict) -> dict:
    return {**{k: t.get(k) for k in ("id", "login", "nome", "unidade_id", "unidade_auto", "ativo", "criado_em", "criado_por", "ultimo_sinal")},
            "unidade_nome": _unidade_nome(t.get("unidade_id") or "")}


def _gerar_chave(t: dict) -> None:
    t["chave_cif"] = cifrar(_exigir_cripto(), secrets.token_urlsafe(24).encode(), t["id"].encode())


def segredo_do_terminal(t: dict) -> str:
    """Chave do terminal — gerada pelo servidor, ninguém digita. Se a PONTO_CHAVE mudou, gera outra."""
    k = _exigir_cripto()
    with TRAVA:
        if t.get("chave_cif"):
            try:
                return decifrar(k, t["chave_cif"], t["id"].encode()).decode("utf-8")
            except Exception:
                pass
        _gerar_chave(t)
        _salvar_cfg()
        return decifrar(k, t["chave_cif"], t["id"].encode()).decode("utf-8")


def _tid_do_login(email: str) -> str:
    return "u" + hashlib.sha256((email or "").strip().lower().encode("utf-8")).hexdigest()[:10]


def unidade_do_login(email: str) -> str:
    """A pessoa do login no Organograma (mesmo e-mail) com unidade no cadastro do ponto."""
    e = (email or "").strip().lower()
    if not e:
        return ""
    for pid, o in org_pessoas().items():
        if str(o.get("email") or "").strip().lower() == e:
            u = (colabs().get(pid) or {}).get("unidade_id")
            if u:
                return u
    return ""


def terminal_do_login(request: Request) -> dict:
    """O aparelho logado É o terminal: um por login, criado no primeiro uso."""
    q = quem(request)
    email = (q["email"] or "").strip().lower()
    if not email:
        raise HTTPException(401, "Faça login de novo.")
    tid = _tid_do_login(email)
    with TRAVA:
        t = terminal(tid)
        mudou = False
        if not t:
            t = {"id": tid, "login": email, "nome": f"Terminal · {q['por']}", "unidade_id": "", "unidade_auto": True, "ativo": True,
                 "criado_em": agora().isoformat(timespec="seconds"), "criado_por": q["por"]}
            cfg()["terminais"].append(t)
            mudou = True
        if not t.get("unidade_id"):
            u = unidade_do_login(email)
            if u:
                t["unidade_id"], t["unidade_auto"], mudou = u, True, True
        if mudou:
            _salvar_cfg()
    return t


# ══════════════════════════════════════════════════════════════════════════
# PESSOAS — Organograma (só leitura) + cadastro do ponto
# ══════════════════════════════════════════════════════════════════════════
_org = {"em": 0.0, "pessoas": {}}
_colab: Optional[dict] = None


def org_pessoas(forcar: bool = False) -> dict:
    """{pid: pessoa} — pid = "<setor>.<pessoa>". Lido do Organograma a cada 60 s no máximo."""
    with TRAVA:
        if forcar or time.time() - _org["em"] > 60 or not _org["pessoas"]:
            saida = {}
            for s in _fs_organograma():
                d = s.get("dados") or {}
                if d.get("_tipo") == "niveis":
                    continue
                nomes = {p.get("id"): p.get("nome") for p in d.get("pessoas") or []}
                for p in d.get("pessoas") or []:
                    if p.get("vaga") or not str(p.get("nome") or "").strip() or not p.get("id"):
                        continue
                    # [v6.5] quem mudou de setor no Organograma guarda o código antigo (digitais e batidas continuam dele)
                    pid = str(p.get("ponto_pid") or "").strip() or f"{s['id']}.{p['id']}"
                    saida[pid] = {"pid": pid, "nome": str(p.get("nome")).strip(), "cargo": p.get("cargo") or "",
                                  "setor_id": s["id"], "setor": d.get("nome") or "", "cor": d.get("cor") or "",
                                  "subsetor": p.get("subsetor") or "", "gestor": nomes.get(p.get("gestor_id")) or "",
                                  "escala": p.get("escala") or "", "horario": p.get("horario") or "",
                                  "email": p.get("email") or ""}
            _org.update(em=time.time(), pessoas=saida)
        return _org["pessoas"]


def colabs() -> dict:
    global _colab
    with TRAVA:
        if _colab is None:
            _colab = {d["_id"]: {k: v for k, v in d.items() if k != "_id"} for d in _fs_todos(COL_COLAB)}
        return _colab


def jornada_do_horario(texto: str) -> dict:
    """Tenta ler o horário do Organograma ("08:00 às 17:48 · intervalo 12:00-13:00") só para sugerir."""
    hs = [hm_ok(x) for x in re.findall(r"\d{1,2}[:hH]\d{2}", texto or "")]
    hs = [h for h in hs if h]
    j = {"dias": [0, 1, 2, 3, 4], "entrada": "", "saida_int": "", "retorno": "", "saida": ""}
    if len(hs) >= 4:
        j.update(entrada=hs[0], saida_int=hs[2] if minutos(hs[2]) < minutos(hs[1]) else hs[1],
                 retorno=hs[3] if minutos(hs[2]) < minutos(hs[1]) else hs[2],
                 saida=hs[1] if minutos(hs[2]) < minutos(hs[1]) else hs[3])
    elif len(hs) == 2:
        j.update(entrada=hs[0], saida=hs[1])
    return j


def pessoa(pid: str) -> dict:
    """Organograma + cadastro do ponto. Quem saiu do Organograma continua aparecendo
    (as batidas antigas precisam de nome), marcado como fora do organograma."""
    o = org_pessoas().get(pid)
    c = colabs().get(pid) or {}
    base = o or {"pid": pid, "nome": c.get("nome") or "(fora do Organograma)", "cargo": c.get("cargo") or "",
                 "setor_id": pid.split(".")[0], "setor": c.get("setor") or "", "cor": "", "subsetor": "",
                 "gestor": "", "escala": "", "horario": "", "email": ""}
    return {**base, "no_organograma": bool(o), "cpf": c.get("cpf") or "", "matricula": c.get("matricula") or "",
            "unidade_id": c.get("unidade_id") or "", "ativo": bool(c.get("ativo")),
            "jornada": c.get("jornada") or jornada_do_horario(base.get("horario")),
            "jornada_sugerida": not c.get("jornada"),
            "digitais": [{"dedo": x.get("dedo"), "qualidade": x.get("qualidade"), "em": x.get("em"), "por": x.get("por")}
                         for x in c.get("digitais") or []],
            "cadastrado": bool(c)}


def pessoas_todas() -> List[dict]:
    pids = set(org_pessoas()) | set(colabs())
    return sorted((pessoa(p) for p in pids), key=lambda x: (x["setor"].lower(), x["nome"].lower()))


# ══════════════════════════════════════════════════════════════════════════
# MARCAÇÕES (imutáveis) — memória dos últimos dias
# ══════════════════════════════════════════════════════════════════════════
_marc = {"carregado": False, "desde": "", "lista": []}


def _carregar_marc() -> None:
    with TRAVA:
        if _marc["carregado"]:
            return
        desde = (agora().date() - timedelta(days=DIAS_EM_MEMORIA)).isoformat()
        lista = _fs_periodo(COL_MARC, "data", desde, "9999-12-31")
        lista.sort(key=lambda m: m.get("nsr", 0))
        _marc.update(carregado=True, desde=desde, lista=lista)


def marcacoes(de: str, ate: str) -> List[dict]:
    _carregar_marc()
    if de >= _marc["desde"]:
        return [m for m in _marc["lista"] if de <= m["data"] <= ate]
    antigas = _fs_periodo(COL_MARC, "data", de, ate)
    return sorted(antigas, key=lambda m: m.get("nsr", 0))


def _hash(m: dict, anterior: str) -> str:
    base = "|".join(str(m.get(k, "")) for k in ("nsr", "pid", "cpf", "momento", "terminal_id", "metodo")) + "|" + (anterior or "")
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def registrar_marcacao(p: dict, terminal_id: str, unidade_id: str, metodo: str, score: Optional[int],
                       momento_aparelho: str, operador: dict) -> dict:
    regras = cfg()["regras"]
    with TRAVA:
        _carregar_marc()
        ultima = next((m for m in reversed(_marc["lista"]) if m["pid"] == p["pid"]), None)
        agora_dt = agora()
        if ultima:
            dif = (agora_dt - datetime.fromisoformat(ultima["momento"])).total_seconds()
            if dif < 60 * int(regras.get("duplicada_min", 2)):
                raise HTTPException(409, f"{p['nome'].split()[0]}, sua batida das {ultima['momento'][11:16]} já foi registrada.")

        def montar(cont: dict) -> dict:
            m = {"nsr": int(cont.get("nsr") or 0) + 1, "id": uuid.uuid4().hex, "pid": p["pid"], "nome": p["nome"],
                 "cpf": so_digitos(p.get("cpf")), "matricula": p.get("matricula") or "", "setor": p.get("setor") or "",
                 "momento": agora_dt.isoformat(timespec="seconds"), "data": agora_dt.strftime("%Y-%m-%d"),
                 "momento_aparelho": str(momento_aparelho or "")[:40], "terminal_id": terminal_id,
                 "unidade_id": unidade_id, "metodo": metodo, "score": score,
                 "operador": operador.get("email"), "hash_anterior": cont.get("hash") or ""}
            m["hash"] = _hash(m, m["hash_anterior"])
            return m

        m = _fs_gravar_marcacao(montar)
        _marc["lista"].append(m)
        return m


def tipo_da_batida(m: dict) -> str:
    do_dia = [x for x in marcacoes(m["data"], m["data"]) if x["pid"] == m["pid"]]
    i = next((k for k, x in enumerate(do_dia) if x["nsr"] == m["nsr"]), len(do_dia) - 1)
    return TIPOS[i % 4] if i < 4 else ("Entrada (extra)" if i % 2 == 0 else "Saída (extra)")


def conferir_corrente(marcs: List[dict]) -> dict:
    """Refaz os hashes: qualquer batida alterada direto no banco aparece aqui."""
    quebras = []
    for i, m in enumerate(marcs):
        if _hash(m, m.get("hash_anterior") or "") != m.get("hash"):
            quebras.append(m["nsr"])
        if i and marcs[i - 1]["nsr"] + 1 == m["nsr"] and marcs[i - 1]["hash"] != m.get("hash_anterior"):
            quebras.append(m["nsr"])
    nsrs = [m["nsr"] for m in marcs]
    buracos = [n for a, b in zip(nsrs, nsrs[1:]) for n in range(a + 1, b)][:50]
    return {"ok": not quebras and not buracos, "quebras": sorted(set(quebras))[:50], "buracos": buracos}


# ══════════════════════════════════════════════════════════════════════════
# AJUSTES (o "tratamento") — sempre registro novo
# ══════════════════════════════════════════════════════════════════════════
_aj: Optional[list] = None
TIPOS_AJUSTE = {"incluir": "Incluir batida esquecida", "desconsiderar": "Desconsiderar batida",
                "abonar": "Abonar o dia", "justificar": "Justificar ocorrência"}


def ajustes() -> list:
    global _aj
    with TRAVA:
        if _aj is None:
            _aj = sorted(_fs_todos(COL_AJ), key=lambda a: a.get("criado_em") or "")
        return _aj


def ajustes_do(pid: str, d: str) -> list:
    return [a for a in ajustes() if a.get("pid") == pid and a.get("data") == d and not a.get("cancelado")]


# ══════════════════════════════════════════════════════════════════════════
# APURAÇÃO — funções puras (testadas em testes_ponto.py)
# ══════════════════════════════════════════════════════════════════════════
def previsto_do_dia(j: dict, d: str, feriados: List[str]) -> dict:
    """Minutos previstos e horários do dia, pela jornada (dias: 0=segunda … 6=domingo)."""
    wd = date.fromisoformat(d).weekday()
    if d in feriados or wd not in (j.get("dias") or []):
        return {"min": 0, "entrada": None, "saida": None, "intervalo": 0}
    e, s = minutos(j.get("entrada")), minutos(j.get("saida"))
    if e is None or s is None:
        return {"min": 0, "entrada": None, "saida": None, "intervalo": 0}
    si, r = minutos(j.get("saida_int")), minutos(j.get("retorno"))
    inter = (r - si) if (si is not None and r is not None and r > si) else 0
    total = (s - e if s > e else s + 1440 - e) - inter
    return {"min": max(0, total), "entrada": e, "saida": s, "intervalo": inter}


def apurar_dia(batidas_hm: List[str], prev: dict, regras: dict, abonado: bool = False) -> dict:
    """Uma linha do espelho. batidas_hm: "HH:MM" já com os ajustes aplicados."""
    bs = sorted(minutos(b) for b in batidas_hm if minutos(b) is not None)
    pares = [(bs[i], bs[i + 1]) for i in range(0, len(bs) - 1, 2)]
    trabalhado = sum(b - a for a, b in pares)
    inc = []
    if len(bs) % 2:
        inc.append("marcacao_impar")
    p = prev["min"]
    atraso = 0
    if p and bs and prev["entrada"] is not None and bs[0] - prev["entrada"] > regras["tol_marcacao"]:
        atraso = bs[0] - prev["entrada"]
        inc.append("atraso")
    falta = bool(p and not bs and not abonado)
    if falta:
        inc.append("falta")
    if trabalhado > 360:
        intervalos = [pares[i + 1][0] - pares[i][1] for i in range(len(pares) - 1)]
        if not intervalos or max(intervalos) < regras["intervalo_min_6h"] - regras["tol_marcacao"]:
            inc.append("intervalo_curto")
    elif trabalhado > 240 and len(pares) >= 2:
        if max(pares[i + 1][0] - pares[i][1] for i in range(len(pares) - 1)) < regras["intervalo_min_4h"] - regras["tol_marcacao"]:
            inc.append("intervalo_curto")
    diferenca = trabalhado - p
    if abonado:
        diferenca = max(0, diferenca)
    elif len(bs) % 2:
        diferenca = 0          # dia incompleto: não calcula saldo até tratar
    # tolerância: até X min por batida e Y min no dia são desconsiderados
    if abs(diferenca) <= regras["tol_dia"] and not falta:
        diferenca = 0
    if diferenca > regras["extra_max_dia"] and p:   # em folga/feriado o alerta é "batida em dia de folga"
        inc.append("extra_excedida")
    if p == 0 and bs:
        inc.append("trabalho_em_folga")
    return {"batidas": [fmt_min(b) for b in bs], "previsto": p, "trabalhado": trabalhado,
            "saldo": diferenca, "atraso": atraso, "falta": falta, "abonado": abonado, "inconsistencias": inc}


NOMES_INC = {"marcacao_impar": "Batida faltando (número ímpar)", "atraso": "Atraso", "falta": "Falta",
             "intervalo_curto": "Intervalo menor que o mínimo", "interjornada": "Menos de 11 h de descanso",
             "extra_excedida": "Mais de 2 h extras no dia", "trabalho_em_folga": "Batida em dia de folga/feriado",
             "contingencia": "Batida sem digital (teste/contingência)"}
SEVERIDADE = {"marcacao_impar": 3, "falta": 3, "interjornada": 3, "intervalo_curto": 2, "extra_excedida": 2,
              "atraso": 1, "trabalho_em_folga": 1, "contingencia": 1}


def espelho(pid: str, de: str, ate: str, com_teste: bool = False) -> dict:
    p = pessoa(pid)
    regras, feriados = cfg()["regras"], [f.get("data") for f in cfg()["feriados"]]
    todas = [m for m in marcacoes(de, ate) if m["pid"] == pid and (com_teste or m.get("metodo") != "teste")]
    linhas, ult_saida, h = [], None, hoje()
    # a saída do dia anterior ao período entra na conta do descanso (interjornada)
    for d in dias(de, ate):
        ms = [m for m in todas if m["data"] == d]
        ajs = ajustes_do(pid, d)
        fora = {a.get("nsr") for a in ajs if a["tipo"] == "desconsiderar"}
        hms = [m["momento"][11:16] for m in ms if m["nsr"] not in fora]
        hms += [a["horario"] for a in ajs if a["tipo"] == "incluir" and hm_ok(a.get("horario"))]
        abon = any(a["tipo"] == "abonar" for a in ajs)
        prev = previsto_do_dia(p["jornada"], d, feriados)
        r = apurar_dia(hms, prev, regras, abon)
        if d >= h:   # o dia ainda está acontecendo: sem falta, sem "batida faltando" e sem saldo
            r["inconsistencias"] = [i for i in r["inconsistencias"] if i not in ("falta", "marcacao_impar", "intervalo_curto")]
            r.update(falta=False, saldo=0, em_andamento=True)
        just = {a.get("inconsistencia") for a in ajs if a["tipo"] == "justificar"}
        if r["batidas"] and ult_saida is not None:
            ini = datetime.fromisoformat(f"{d}T{r['batidas'][0]}:00")
            if (ini - ult_saida).total_seconds() < regras["interjornada_h"] * 3600:
                r["inconsistencias"].append("interjornada")
        if r["batidas"] and len(r["batidas"]) % 2 == 0:
            ult_saida = datetime.fromisoformat(f"{d}T{r['batidas'][-1]}:00")
        if any(m.get("metodo") == "teste" for m in ms if m["nsr"] not in fora):
            r["inconsistencias"].append("contingencia")
        r["tratadas"] = sorted(i for i in r["inconsistencias"] if i in just or (abon and i in ("falta", "atraso")))
        r["pendentes"] = [i for i in r["inconsistencias"] if i not in r["tratadas"]]
        r.update(data=d, dia_semana=["seg", "ter", "qua", "qui", "sex", "sáb", "dom"][date.fromisoformat(d).weekday()],
                 feriado=next((f.get("nome") for f in cfg()["feriados"] if f.get("data") == d), None),
                 marcacoes=[{"nsr": m["nsr"], "hora": m["momento"][11:16], "terminal_id": m.get("terminal_id"),
                             "metodo": m.get("metodo"), "desconsiderada": m["nsr"] in fora} for m in ms],
                 ajustes=[{k: a.get(k) for k in ("id", "tipo", "horario", "nsr", "inconsistencia", "motivo", "por", "criado_em")} for a in ajs])
        linhas.append(r)
    tot = {k: sum(l[k] for l in linhas) for k in ("previsto", "trabalhado", "atraso")}
    tot["saldo"] = sum(l["saldo"] for l in linhas)
    tot["faltas"] = sum(1 for l in linhas if l["falta"])
    tot["pendentes"] = sum(len(l["pendentes"]) for l in linhas)
    return {"pessoa": p, "de": de, "ate": ate, "dias": linhas, "totais": tot}


# ══════════════════════════════════════════════════════════════════════════
# ROTAS
# ══════════════════════════════════════════════════════════════════════════
def _unidade_nome(uid: str) -> str:
    return next((u.get("nome") for u in cfg()["unidades"] if u.get("id") == uid), "")


@router.get("/inicio")
def r_inicio(request: Request):
    exigir(request, ["hoje", "marcar", "espelho", "tratamento", "cadastros"], 1, "Abrir o Ponto")
    q = quem(request)
    return {"unidades": cfg()["unidades"], "terminais": [terminal_publico(t) for t in cfg()["terminais"]],
            "regras": cfg()["regras"], "feriados": cfg()["feriados"], "cripto_ok": cripto_ok(),
            "cripto_lib": AESGCM is not None, "chave_ok": _chave_mestra() is not None,
            "hoje": hoje(), "agora": agora().isoformat(timespec="seconds"), "admin": q["admin"],
            "nomes_inc": NOMES_INC, "tipos_ajuste": TIPOS_AJUSTE}


@router.get("/hoje")
def r_hoje(request: Request, unidade: str = "", setor: str = ""):
    exigir(request, "hoje", 1, "Ver o Hoje")
    d = hoje()
    ms = [m for m in marcacoes(d, d)]
    feriados = [f.get("data") for f in cfg()["feriados"]]
    agora_min = agora().hour * 60 + agora().minute
    pessoas, cont = [], {"presentes": 0, "intervalo": 0, "sairam": 0, "ausentes": 0, "atrasados": 0, "folga": 0, "sem_cadastro": 0}
    for p in pessoas_todas():
        if not p["ativo"]:
            if p["no_organograma"]:
                cont["sem_cadastro"] += 1
            continue
        if unidade and p["unidade_id"] != unidade:
            continue
        if setor and p["setor_id"] != setor:
            continue
        bs = [m for m in ms if m["pid"] == p["pid"] and m.get("metodo") != "teste"]
        prev = previsto_do_dia(p["jornada"], d, feriados)
        n = len(bs)
        if n == 0:
            st = "folga" if not prev["min"] else ("ausente" if prev["entrada"] is not None and agora_min > prev["entrada"] + cfg()["regras"]["tol_marcacao"] else "aguardando")
        else:
            st = ["trabalhando", "intervalo", "trabalhando", "saiu"][min(n, 4) - 1] if n <= 4 else ("trabalhando" if n % 2 else "saiu")
        atraso = bool(bs and prev["entrada"] is not None and minutos(bs[0]["momento"][11:16]) - prev["entrada"] > cfg()["regras"]["tol_marcacao"])
        if st in ("trabalhando",):
            cont["presentes"] += 1
        elif st == "intervalo":
            cont["intervalo"] += 1
        elif st == "saiu":
            cont["sairam"] += 1
        elif st == "ausente":
            cont["ausentes"] += 1
        elif st == "folga":
            cont["folga"] += 1
        if atraso:
            cont["atrasados"] += 1
        pessoas.append({"pid": p["pid"], "nome": p["nome"], "cargo": p["cargo"], "setor": p["setor"], "cor": p["cor"],
                        "unidade": _unidade_nome(p["unidade_id"]), "status": st, "atraso": atraso,
                        "previsto": [fmt_min(prev["entrada"]) if prev["entrada"] is not None else None,
                                     fmt_min(prev["saida"]) if prev["saida"] is not None else None],
                        "batidas": [m["momento"][11:16] for m in bs]})
    nomes_t = {t["id"]: t.get("nome") for t in cfg()["terminais"]}
    feed = [{"nsr": m["nsr"], "hora": m["momento"][11:19], "nome": m["nome"], "setor": m.get("setor"),
             "tipo": tipo_da_batida(m), "terminal": nomes_t.get(m.get("terminal_id"), m.get("terminal_id")),
             "unidade": _unidade_nome(m.get("unidade_id")), "metodo": m.get("metodo")}
            for m in reversed(ms) if (not unidade or m.get("unidade_id") == unidade)][:80]
    return {"data": d, "agora": agora().isoformat(timespec="seconds"), "kpis": cont, "pessoas": pessoas, "feed": feed,
            "terminais": [{**terminal_publico(t), "batidas_hoje": sum(1 for m in ms if m.get("terminal_id") == t["id"]),
                           "online": bool(t.get("ultimo_sinal") and (agora() - datetime.fromisoformat(t["ultimo_sinal"])).total_seconds() < 180)}
                          for t in cfg()["terminais"]]}


# ── BATER PONTO ──────────────────────────────────────────────────────────
@router.get("/terminal")
def r_terminal_eu(request: Request):
    """O terminal deste login (criado sozinho) + a chave que a tela entrega ao agente do leitor."""
    exigir(request, ["marcar", "cadastros"], 2, "Usar o leitor")
    t = terminal_do_login(request)
    return {**terminal_publico(t), "chave": segredo_do_terminal(t) if cripto_ok() and t.get("ativo") else None,
            "hora_servidor": agora().isoformat(timespec="seconds")}


@router.get("/desafio")
def r_desafio(request: Request):
    exigir(request, ["marcar", "cadastros"], 2, "Usar o leitor")
    t = terminal_do_login(request)
    with TRAVA:
        t["ultimo_sinal"] = agora().isoformat(timespec="seconds")   # só em memória (não gasta gravação)
    return {"desafio": novo_desafio(), "hora_servidor": agora().isoformat(timespec="seconds")}


@router.get("/terminal/pacote")
def r_pacote(request: Request):
    """Templates de todos os ativos, cifrados com a chave do terminal deste login."""
    exigir(request, "marcar", 2, "Carregar as digitais no leitor")
    t = terminal_do_login(request)
    if not t.get("ativo"):
        raise HTTPException(403, f"Este terminal ({t.get('nome')}) está bloqueado. Procure quem cuida dos Cadastros do Ponto.")
    tid = t["id"]
    k = _exigir_cripto()
    seg = segredo_do_terminal(t)
    itens = []
    for pid, c in colabs().items():
        if not c.get("ativo"):
            continue
        for dg in c.get("digitais") or []:
            try:
                itens.append({"p": pid, "d": dg.get("dedo"), "t": base64.b64encode(decifrar(k, dg["t"], pid.encode())).decode()})
            except Exception:
                continue
    blob = cifrar(_k_terminal(seg, "pacote"), json.dumps({"em": agora().isoformat(timespec="seconds"), "itens": itens}).encode(), tid.encode())
    return {"terminal_id": tid, "qtd": len(itens), "pessoas": len({i["p"] for i in itens}), "pacote": blob}


class Batida(BaseModel):
    metodo: str = "digital"            # digital | teste
    pessoa: str
    score: Optional[int] = None
    momento_aparelho: str = ""
    desafio: str = ""
    assinatura: str = ""


@router.post("/marcar")
def r_marcar(b: Batida, request: Request):
    exigir(request, "marcar", 2, "Registrar batida")
    q = quem(request)
    t = terminal_do_login(request)
    if not t.get("ativo"):
        raise HTTPException(403, f"Este terminal ({t.get('nome')}) está bloqueado. Procure quem cuida dos Cadastros do Ponto.")
    if b.metodo == "teste":
        if not q["admin"]:
            raise HTTPException(403, "O modo de teste (sem leitor) é só para o administrador.")
    elif b.metodo == "digital":
        if not desafio_valido(b.desafio):
            raise HTTPException(400, "A leitura expirou. Coloque o dedo de novo.")
        esperado = assinatura_esperada(segredo_do_terminal(t), b.desafio, b.pessoa, b.score or 0, b.momento_aparelho)
        if not hmac.compare_digest(esperado, (b.assinatura or "").lower()):
            raise HTTPException(403, "A leitura não veio do leitor deste terminal (assinatura não confere). Batida não registrada.")
    else:
        raise HTTPException(400, "Método inválido.")
    p = pessoa(b.pessoa)
    if not p["cadastrado"] or not p["ativo"]:
        raise HTTPException(404, "Digital reconhecida, mas a pessoa não está ativa no Ponto. Procure o gestor.")
    m = registrar_marcacao(p, t["id"], t.get("unidade_id") or p.get("unidade_id") or "", b.metodo, b.score, b.momento_aparelho, q)
    with TRAVA:
        t["ultimo_sinal"] = m["momento"]
        if not t.get("unidade_id") and p.get("unidade_id"):   # unidade automática: a de quem bateu o primeiro ponto aqui
            t["unidade_id"], t["unidade_auto"] = p["unidade_id"], True
            _salvar_cfg()
    return {"nsr": m["nsr"], "nome": p["nome"], "primeiro_nome": p["nome"].split()[0], "cargo": p["cargo"], "setor": p["setor"],
            "hora": m["momento"][11:19], "data": m["data"], "tipo": tipo_da_batida(m), "metodo": m["metodo"],
            "hash": m["hash"][:16]}


@router.get("/comprovante/{nsr}")
def r_comprovante(nsr: int, request: Request):
    exigir(request, ["hoje", "marcar", "espelho", "tratamento"], 1, "Ver o comprovante")
    _carregar_marc()
    m = next((x for x in _marc["lista"] if x["nsr"] == nsr), None) or _fs_get(COL_MARC, f"{nsr:010d}")
    if not m:
        raise HTTPException(404, "Batida não encontrada.")
    cpf = so_digitos(m.get("cpf"))
    return {"nsr": m["nsr"], "nome": m["nome"], "cpf": f"***.{cpf[3:6]}.{cpf[6:9]}-**" if len(cpf) == 11 else "",
            "data": m["data"], "hora": m["momento"][11:19], "terminal": (terminal(m.get("terminal_id")) or {}).get("nome", m.get("terminal_id")),
            "unidade": _unidade_nome(m.get("unidade_id")), "metodo": m.get("metodo"), "hash": m.get("hash"),
            "empregador": os.environ.get("PONTO_EMPREGADOR", "King Star Colchões")}


# ── ESPELHO ──────────────────────────────────────────────────────────────
def periodo_padrao() -> tuple:
    ini = int(cfg()["regras"].get("periodo_inicio") or 1)
    h = agora().date()
    if ini <= 1:
        de = h.replace(day=1)
    else:
        de = h.replace(day=ini) if h.day >= ini else (h.replace(day=1) - timedelta(days=1)).replace(day=ini)
    return de.isoformat(), h.isoformat()


@router.get("/pessoas")
def r_pessoas(request: Request):
    exigir(request, ["espelho", "tratamento", "hoje"], 1, "Ver as pessoas")
    return [{k: p[k] for k in ("pid", "nome", "cargo", "setor", "setor_id", "ativo", "unidade_id")} for p in pessoas_todas() if p["cadastrado"]]


@router.get("/espelho")
def r_espelho(request: Request, pessoa: str, de: str = "", ate: str = "", teste: bool = False):
    exigir(request, "espelho", 1, "Ver o espelho")
    pd, pa = periodo_padrao()
    e = espelho(pessoa, iso_ok(de, pd), iso_ok(ate, pa), teste)
    e["pessoa"].pop("digitais", None)
    return e


# ── TRATAMENTO ───────────────────────────────────────────────────────────
@router.get("/tratamento")
def r_tratamento(request: Request, de: str = "", ate: str = "", unidade: str = "", todas: bool = False):
    exigir(request, "tratamento", 1, "Ver o tratamento")
    pd, pa = periodo_padrao()
    de, ate = iso_ok(de, pd), iso_ok(ate, pa)
    if ate >= hoje():   # o dia de hoje ainda está acontecendo: não vira pendência
        ate = (agora().date() - timedelta(days=1)).isoformat()
    saida = []
    for p in pessoas_todas():
        if not p["ativo"] or (unidade and p["unidade_id"] != unidade):
            continue
        if ate < de:
            break
        for l in espelho(p["pid"], de, ate)["dias"]:
            lista = l["inconsistencias"] if todas else l["pendentes"]
            for i in lista:
                saida.append({"pid": p["pid"], "nome": p["nome"], "setor": p["setor"], "data": l["data"],
                              "dia_semana": l["dia_semana"], "tipo": i, "nome_tipo": NOMES_INC.get(i, i),
                              "severidade": SEVERIDADE.get(i, 1), "tratada": i in l["tratadas"],
                              "batidas": l["batidas"], "previsto": l["previsto"], "trabalhado": l["trabalhado"],
                              "marcacoes": l["marcacoes"]})
    saida.sort(key=lambda x: (-x["severidade"], x["data"], x["nome"]))
    return {"de": de, "ate": ate, "itens": saida}


class Ajuste(BaseModel):
    pessoa: str
    data: str
    tipo: str
    horario: Optional[str] = None
    nsr: Optional[int] = None
    inconsistencia: Optional[str] = None
    motivo: str


@router.post("/ajustes")
def r_ajuste(a: Ajuste, request: Request):
    exigir(request, "tratamento", 3, "Ajustar o ponto")
    q = quem(request)
    if a.tipo not in TIPOS_AJUSTE:
        raise HTTPException(400, "Tipo de ajuste inválido.")
    if len((a.motivo or "").strip()) < 5:
        raise HTTPException(400, "Escreva o motivo (é obrigatório e fica no histórico).")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.data or "") or a.data > hoje():
        raise HTTPException(400, "Data inválida.")
    if not pessoa(a.pessoa)["cadastrado"]:
        raise HTTPException(404, "Pessoa sem cadastro no Ponto.")
    reg = {"id": uuid.uuid4().hex, "pid": a.pessoa, "data": a.data, "tipo": a.tipo, "motivo": a.motivo.strip()[:500],
           "por": q["por"], "email": q["email"], "criado_em": agora().isoformat(timespec="seconds")}
    if a.tipo == "incluir":
        if not hm_ok(a.horario):
            raise HTTPException(400, "Informe o horário (HH:MM).")
        reg["horario"] = hm_ok(a.horario)
    if a.tipo == "desconsiderar":
        if not any(m["nsr"] == a.nsr and m["pid"] == a.pessoa for m in marcacoes(a.data, a.data)):
            raise HTTPException(400, "Batida não encontrada neste dia.")
        reg["nsr"] = a.nsr
    if a.tipo == "justificar":
        if a.inconsistencia not in NOMES_INC:
            raise HTTPException(400, "Escolha a ocorrência que está sendo justificada.")
        reg["inconsistencia"] = a.inconsistencia
    with TRAVA:
        lista = ajustes()          # carrega ANTES de gravar (senão o novo entra duas vezes na memória)
        _fs_set(COL_AJ, reg["id"], reg)
        lista.append(reg)
    return reg


@router.get("/ajustes")
def r_ajustes(request: Request, de: str = "", ate: str = ""):
    exigir(request, "tratamento", 1, "Ver os ajustes")
    pd, pa = periodo_padrao()
    de, ate = iso_ok(de, pd), iso_ok(ate, pa)
    nomes = {p["pid"]: p["nome"] for p in pessoas_todas()}
    return [{**a, "nome": nomes.get(a["pid"], a["pid"])} for a in reversed(ajustes()) if de <= a.get("data", "") <= ate][:500]


@router.get("/integridade")
def r_integridade(request: Request):
    exigir(request, "tratamento", 1, "Conferir a integridade")
    _carregar_marc()
    return {**conferir_corrente(_marc["lista"]), "conferidas": len(_marc["lista"]), "desde": _marc["desde"]}


# ── CADASTROS ────────────────────────────────────────────────────────────
@router.get("/colaboradores")
def r_colaboradores(request: Request, recarregar: bool = False):
    exigir(request, "cadastros", 1, "Ver os cadastros")
    org_pessoas(forcar=recarregar)
    return pessoas_todas()


class Cadastro(BaseModel):
    cpf: str = ""
    matricula: str = ""
    unidade_id: str = ""
    ativo: bool = True
    jornada: dict = {}


def _jornada_limpa(j: dict) -> dict:
    saida = {"dias": sorted({int(x) for x in (j.get("dias") or []) if str(x).isdigit() and 0 <= int(x) <= 6})}
    for k in ("entrada", "saida_int", "retorno", "saida"):
        v = j.get(k)
        if v and not hm_ok(v):
            raise HTTPException(400, f"Horário inválido em “{k}”: use HH:MM.")
        saida[k] = hm_ok(v) or ""
    if saida["dias"] and not (saida["entrada"] and saida["saida"]):
        raise HTTPException(400, "Informe pelo menos a entrada e a saída da jornada.")
    if bool(saida["saida_int"]) != bool(saida["retorno"]):
        raise HTTPException(400, "Intervalo: preencha a saída E a volta (ou deixe os dois vazios).")
    return saida


@router.put("/colaboradores/{pid}")
def r_salvar_colab(pid: str, c: Cadastro, request: Request):
    exigir(request, "cadastros", 3, "Alterar o cadastro do ponto")
    q = quem(request)
    o = org_pessoas().get(pid)
    atual = dict(colabs().get(pid) or {})
    if not o and not atual:
        raise HTTPException(404, "Pessoa não encontrada no Organograma.")
    cpf = so_digitos(c.cpf)
    if cpf and not cpf_valido(cpf):
        raise HTTPException(400, "CPF inválido — confira os números.")
    if c.ativo and not cpf:
        raise HTTPException(400, "Para ativar no ponto, o CPF é obrigatório (vai no comprovante e no arquivo fiscal).")
    if cpf and any(so_digitos(x.get("cpf")) == cpf and k != pid for k, x in colabs().items()):
        raise HTTPException(409, "Este CPF já está em outro cadastro.")
    if c.unidade_id and not any(u.get("id") == c.unidade_id for u in cfg()["unidades"]):
        raise HTTPException(400, "Unidade inexistente.")
    novo = {**atual, "cpf": cpf, "matricula": str(c.matricula or "").strip()[:30], "unidade_id": c.unidade_id,
            "ativo": bool(c.ativo), "jornada": _jornada_limpa(c.jornada or {}),
            "nome": (o or {}).get("nome") or atual.get("nome"), "setor": (o or {}).get("setor") or atual.get("setor"),
            "cargo": (o or {}).get("cargo") or atual.get("cargo"),
            "atualizado_por": q["por"], "atualizado_em": agora().isoformat(timespec="seconds")}
    novo.setdefault("digitais", [])
    novo.setdefault("criado_em", novo["atualizado_em"])
    hist = list(atual.get("historico") or [])[-40:]
    mud = [k for k in ("cpf", "matricula", "unidade_id", "ativo", "jornada") if atual.get(k) != novo.get(k)]
    if mud:
        hist.append({"em": novo["atualizado_em"], "por": q["por"], "campos": mud})
    novo["historico"] = hist
    with TRAVA:
        _fs_set(COL_COLAB, pid, novo)
        colabs()[pid] = novo
    return pessoa(pid)


class Digital(BaseModel):
    dedo: int
    pacote: str = ""           # template cifrado pelo agente com a chave do terminal
    qualidade: int = 0
    termo_lgpd: bool = False
    teste: bool = False        # modo de teste do administrador (sem leitor)


@router.post("/colaboradores/{pid}/digital")
def r_digital(pid: str, d: Digital, request: Request):
    exigir(request, "cadastros", 3, "Cadastrar digital")
    q = quem(request)
    if not d.termo_lgpd:
        raise HTTPException(400, "A pessoa precisa aceitar o termo de uso da biometria (LGPD) antes da leitura.")
    if not 1 <= d.dedo <= 10:
        raise HTTPException(400, "Dedo inválido.")
    c = colabs().get(pid)
    if not c:
        raise HTTPException(400, "Salve o cadastro do ponto (CPF, unidade, jornada) antes de ler a digital.")
    k = _exigir_cripto()
    if d.teste:
        if not q["admin"]:
            raise HTTPException(403, "O modo de teste é só para o administrador.")
        template = hashlib.sha256(f"teste|{pid}|{d.dedo}".encode()).digest()
    else:
        t = terminal_do_login(request)
        if not t.get("ativo"):
            raise HTTPException(403, "O terminal deste login está bloqueado.")
        try:
            template = decifrar(_k_terminal(segredo_do_terminal(t), "captura"), d.pacote, b"captura")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "A leitura não veio do agente deste terminal (não consegui abrir). Tente de novo.")
        if len(template) < 16:
            raise HTTPException(400, "Leitura vazia. Tente de novo.")
    em = agora().isoformat(timespec="seconds")
    dg = {"dedo": d.dedo, "t": cifrar(k, template, pid.encode()), "qualidade": int(d.qualidade or 0), "em": em,
          "por": q["por"], "termo_lgpd_em": em, "terminal_id": _tid_do_login(q["email"] or ""), "teste": bool(d.teste)}
    with TRAVA:
        novo = dict(c)
        novo["digitais"] = [x for x in (c.get("digitais") or []) if x.get("dedo") != d.dedo] + [dg]
        novo.setdefault("historico", []).append({"em": em, "por": q["por"], "campos": [f"digital dedo {d.dedo}"]})
        _fs_set(COL_COLAB, pid, novo)
        colabs()[pid] = novo
    return pessoa(pid)


@router.delete("/colaboradores/{pid}/digital/{dedo}")
def r_digital_excluir(pid: str, dedo: int, request: Request):
    exigir(request, "cadastros", 4, "Excluir digital")
    q = quem(request)
    c = colabs().get(pid)
    if not c:
        raise HTTPException(404, "Cadastro não encontrado.")
    with TRAVA:
        novo = dict(c)
        novo["digitais"] = [x for x in (c.get("digitais") or []) if x.get("dedo") != dedo]
        novo.setdefault("historico", []).append({"em": agora().isoformat(timespec="seconds"), "por": q["por"], "campos": [f"digital dedo {dedo} excluída"]})
        _fs_set(COL_COLAB, pid, novo)
        colabs()[pid] = novo
    return pessoa(pid)


class Unidades(BaseModel):
    unidades: list


@router.put("/unidades")
def r_unidades(u: Unidades, request: Request):
    exigir(request, "cadastros", 3, "Alterar unidades")
    vistas, saida = set(), []
    for x in u.unidades:
        nome = str((x or {}).get("nome") or "").strip()
        if not nome:
            continue
        uid = str(x.get("id") or "").strip() or re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")[:30] or uuid.uuid4().hex[:8]
        if uid in vistas:
            raise HTTPException(400, f"Unidade repetida: {nome}")
        vistas.add(uid)
        saida.append({"id": uid, "nome": nome[:80], "cidade": str(x.get("cidade") or "")[:60], "uf": str(x.get("uf") or "").upper()[:2]})
    removidas = {x["id"] for x in cfg()["unidades"]} - vistas
    em_uso = [r for r in removidas if any(c.get("unidade_id") == r for c in colabs().values()) or any(t.get("unidade_id") == r for t in cfg()["terminais"])]
    if em_uso and nivel(request, "cadastros") < 4:
        raise HTTPException(403, "Remover unidade em uso exige “Excluir” na aba Cadastros.")
    with TRAVA:
        cfg()["unidades"] = saida
        _salvar_cfg()
    return saida


class TerminalEdicao(BaseModel):
    nome: Optional[str] = None
    unidade_id: Optional[str] = None
    ativo: Optional[bool] = None


@router.put("/terminais/{tid}")
def r_terminal_editar(tid: str, e: TerminalEdicao, request: Request):
    exigir(request, "cadastros", 3, "Alterar terminal")
    t = terminal(tid)
    if not t:
        raise HTTPException(404, "Terminal não encontrado.")
    with TRAVA:
        if e.nome is not None and e.nome.strip():
            t["nome"] = e.nome.strip()[:60]
        if e.unidade_id is not None:
            if e.unidade_id and not any(u.get("id") == e.unidade_id for u in cfg()["unidades"]):
                raise HTTPException(400, "Unidade inexistente.")
            t["unidade_id"], t["unidade_auto"] = e.unidade_id, False   # escolhida à mão: não muda mais sozinha
        if e.ativo is not None:
            t["ativo"] = bool(e.ativo)
        _salvar_cfg()
    return terminal_publico(t)


@router.delete("/terminais/{tid}")
def r_terminal_excluir(tid: str, request: Request):
    exigir(request, "cadastros", 4, "Excluir terminal")
    _carregar_marc()
    if any(m.get("terminal_id") == tid for m in _marc["lista"]):
        raise HTTPException(409, "Este terminal já registrou batidas: bloqueie em vez de excluir (o histórico precisa dele).")
    with TRAVA:
        cfg()["terminais"] = [t for t in cfg()["terminais"] if t.get("id") != tid]
        _salvar_cfg()
    return {"ok": True}


class Regras(BaseModel):
    regras: dict = {}
    feriados: list = []


@router.put("/regras")
def r_regras(r: Regras, request: Request):
    exigir(request, "cadastros", 3, "Alterar regras")
    novas = dict(cfg()["regras"])
    for k, padrao in REGRAS_PADRAO.items():
        if k in r.regras:
            try:
                novas[k] = max(0, min(1440, int(r.regras[k])))
            except (TypeError, ValueError):
                raise HTTPException(400, f"Valor inválido em {k}.")
    fer = []
    for f in r.feriados:
        d = str((f or {}).get("data") or "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            fer.append({"data": d, "nome": str(f.get("nome") or "Feriado")[:60]})
    with TRAVA:
        cfg()["regras"] = novas
        cfg()["feriados"] = sorted(fer, key=lambda x: x["data"])
        _salvar_cfg()
    return {"regras": novas, "feriados": cfg()["feriados"]}
