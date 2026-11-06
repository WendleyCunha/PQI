"""
motorista.py — APP DO MOTORISTA e PAINEL AO VIVO (parte do módulo Qualidade)

O que entra aqui
  · App do motorista (/motorista/ — instalável no Android): login pela PLACA,
    o motorista escolhe o próprio nome, vê as cargas VALIDADAS da placa e dá baixa
    em cada pedido na porta do cliente: ENTREGUE, RETORNO ou PARCIAL (com motivo e
    foto). Ao terminar, avisa "Cheguei no CD". O GPS vai junto enquanto o app está
    aberto.
  · Palavra da entrega: cada pedido ganha uma palavra. Ligada POR MOTORISTA, ela é
    exigida para registrar ENTREGUE. Errou N vezes → trava e o motorista pede
    liberação ao PCL. A palavra NUNCA sai nas rotas do app (só o servidor confere).
  · Painel ao vivo (aba "Ao vivo" da Qualidade): cargas na rua, desfecho de cada
    pedido na hora, liberações, baixa da entrega no ERP (o PCL marca), retornos
    informados × recebidos no CD e a posição do caminhão.
  · Motoristas e app (aba "Motoristas e app"): quem dirige cada placa, palavra
    liga/desliga por motorista, senha da placa (reset volta para a inicial).

O que NÃO muda
  · O status da carga (validada → em_retorno → retorno_concluido → baixada) continua
    sendo do CD. O que o motorista informa fica em c["rua"] e é a EXPECTATIVA: a peça
    só entra no estoque quando o Retorno recebe no CD (fluxo que já existe).

Login do app
  · Placa + senha. A senha inicial vem de MOTORISTA_SENHA_INICIAL (padrão King@2026)
    e precisa ser trocada no 1º acesso. Resetar (aba Motoristas e app) volta para a
    inicial e derruba quem estiver logado naquela placa.
  · Cookie próprio (pqi_motorista) — não dá acesso a nada do PQI.

Firestore (gasta pouco do plano grátis)
  qld_cadastros/app_motorista   placas (senha) · motoristas (nome, placas, palavra) · cfg
  qld_cargas/<n>.rua            palavras · saída · pedidos (desfecho) · chegada · trilha GPS
  qld_fotos/<id>                fotos do retorno (comprimidas no celular)
  A posição AO VIVO fica só na memória; a trilha grava 1 ponto a cada 3 min.
"""
import base64
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

import qualidade as qld
import seguranca as seg

SENHA_INICIAL = os.environ.get("MOTORISTA_SENHA_INICIAL") or "King@2026"
COOKIE = "pqi_motorista"
DURACAO_SESSAO = 14 * 86400          # o celular fica logado 14 dias (reset da senha derruba antes)
DOC_APP = "app_motorista"
MOTIVOS_CHAT = ("Ausente", "Avaria", "Escadas")   # [v5.5] padrão; o PQI muda em Motoristas e app
CHAT_MAX_MSGS = 300
CHAT_MINUTOS = 15   # [v5.6] contagem regressiva do atendimento (ajustável em Motoristas e app)
COL_FOTOS = "qld_fotos"
STATUS_RUA = ("validada", "em_retorno")   # cargas que o motorista vê e pode baixar
DIAS_APP = 4                               # o app mostra cargas com Mapa de até 4 dias atrás
DESFAZER_MIN = 15                          # o motorista corrige o próprio registro em até 15 min
TRILHA_SEG = 180                           # 1 ponto gravado da trilha a cada 3 min
FOTO_MAX = 450_000                         # bytes (base64) de cada foto

api = APIRouter(prefix="/api/motorista")          # app (rota pública: o login é a placa)
painel_api = APIRouter(prefix="/api/routerking")   # [v5.9] PQI — módulo RouterKing (permissão por aba)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


qld.H["cotacao_cfg"] = lambda: app_cfg()["cfg"].get("cotacao")   # [v5.11] regra de COTAÇÃO editável no RouterKing


def ligar(app) -> None:
    app.include_router(api)
    app.include_router(painel_api)
    for rota, arq, tipo in (("/motorista/", "motorista.html", "text/html"),
                            ("/motorista/sw.js", "motorista-sw.js", "application/javascript"),
                            ("/motorista/manifest.webmanifest", "motorista.webmanifest", "application/manifest+json"),
                            ("/motorista/icone-192.png", "motorista-192.png", "image/png"),
                            ("/motorista/icone-512.png", "motorista-512.png", "image/png")):
        app.add_api_route(rota, _servir(arq, tipo), methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route("/motorista", lambda: RedirectResponse("/motorista/", status_code=307), methods=["GET", "HEAD"], include_in_schema=False)
    # [v5.9] RouterKing: a mesma tela-base da Qualidade, com o menu e a permissão do sistema RouterKing
    app.add_api_route("/routerking", _pagina_rk, methods=["GET", "HEAD"], include_in_schema=False)
    # a foto do motorista também abre na Qualidade (Retorno/GAP), com a permissão de lá
    app.add_api_route("/api/qualidade/foto/{fid}", _foto_qualidade, methods=["GET"], include_in_schema=False)


def _pagina_rk():
    html = open(os.path.join(BASE_DIR, "qualidade.html"), encoding="utf-8").read()
    html = html.replace('data-modulo="qualidade"', 'data-modulo="routerking"', 1)
    html = re.sub(r"<title>.*?</title>", "<title>RouterKing — Entregas · King Star</title>", html, count=1)
    html = html.replace('href="/qualidade-app/manifest.webmanifest"', 'href="/pqi-app/manifest.webmanifest"', 1)
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


def _foto_qualidade(fid: str, request: Request):
    qld.exigir(request, ["retorno", "gap", "pcl"], 1, "Ver foto")
    return _foto(fid)


def _foto(fid: str):
    if not re.fullmatch(r"[0-9a-f]{16}", fid):
        raise HTTPException(404, "Foto não encontrada.")
    d = _fs_get(COL_FOTOS, fid)
    if not d:
        raise HTTPException(404, "Foto não encontrada.")
    return Response(base64.b64decode(d["b64"]), media_type=f"image/{d.get('tipo') or 'jpeg'}", headers={"Cache-Control": "private, max-age=86400"})


def _servir(arq: str, tipo: str):
    def servir():
        cab = {"Cache-Control": "no-cache"}
        if arq.endswith("sw.js"):
            cab["Service-Worker-Allowed"] = "/motorista/"
        return FileResponse(os.path.join(BASE_DIR, arq), media_type=tipo, headers=cab)
    return servir


# rotas que o porteiro do main.py deixa passar sem o login do PQI
ROTAS_PUBLICAS = {"/motorista", "/motorista/", "/motorista/sw.js", "/motorista/manifest.webmanifest",
                  "/motorista/icone-192.png", "/motorista/icone-512.png"}
PREFIXO_PUBLICO = "/api/motorista/"


# ══════════════════════════════════════════════════════════════════════════
# Persistência (trocável nos testes)
# ══════════════════════════════════════════════════════════════════════════
def _fs_get(col: str, doc_id: str) -> Optional[dict]:
    return qld._fs_get(col, doc_id)


def _fs_set(col: str, doc_id: str, dados: dict) -> None:
    qld._fs_set(col, doc_id, dados)


TRAVA = qld.TRAVA
MODULO_RK = "routerking"   # [v5.9] Ao vivo, Histórico de entregas e Motoristas e app ficam no sistema RouterKing


def rk_exigir(request: Request, abas, minimo: int, acao: str) -> None:
    qld.exigir_mod(request, MODULO_RK, abas, minimo, acao)


def rk_nivel(request: Request, aba: str) -> int:
    return qld.nivel(request, aba, MODULO_RK)
APP: dict = {"dados": None}


def app_cfg() -> dict:
    if APP["dados"] is None:
        d = _fs_get(qld.COL_CAD, DOC_APP) or {}
        APP["dados"] = {"placas": d.get("placas") or {}, "motoristas": d.get("motoristas") or {}, "excluidos": d.get("excluidos") or {},
                        "cfg": {"tentativas": 5, "motivos_chat": list(MOTIVOS_CHAT), "chat_minutos": CHAT_MINUTOS,
                                "cotacao": json.loads(json.dumps(qld.COTACAO_PADRAO)), **(d.get("cfg") or {})}}
        if _padronizar(APP["dados"]):
            app_salvar()
    return APP["dados"]


def _padronizar(A: dict) -> bool:
    """Placas gravadas com traço (FIB-3J44) → sem traço (FIB3J44), nas senhas e nos motoristas."""
    mudou = False
    novas: dict = {}
    for k, v in A["placas"].items():
        pl = norm_placa(k)
        if pl != k:
            mudou = True
        if pl and (pl not in novas or str((v or {}).get("em") or "") > str(novas[pl].get("em") or "")):
            novas[pl] = v
    A["placas"] = novas
    for m in A["motoristas"].values():
        lp = sorted({norm_placa(x) for x in m.get("placas") or [] if norm_placa(x)})
        if lp != (m.get("placas") or []):
            m["placas"], mudou = lp, True
    return mudou


def app_salvar() -> None:
    _fs_set(qld.COL_CAD, DOC_APP, app_cfg())


def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in s if unicodedata.category(c) != "Mn").upper())


def norm_placa(p) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(p or "").upper())[:8]


def _min_desde(iso: str) -> float:
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return 1e9


# ══════════════════════════════════════════════════════════════════════════
# PALAVRA DA ENTREGA
# ══════════════════════════════════════════════════════════════════════════
# Curtas, fáceis de falar ao telefone, sem acento obrigatório e sem duplo sentido.
PALAVRAS = """ABACATE ABACAXI ACEROLA AMORA ANIS ARARA AREIA AVELA AZALEIA BAMBU BANANA BAUNILHA BEIJAFLOR BEMTEVI BRISA
CACAU CAJU CAMELO CANARIO CANELA CANOA CAQUI CARAMBOLA CASTANHA CEREJA CIPO COCO COMETA CORAL CORUJA CRAVO CRISTAL
DAMASCO DIAMANTE ESMERALDA ESTRELA FAROL FIGO FLAUTA GAIVOTA GALAXIA GIRASSOL GOIABA GRAMA GRAVIOLA HORTELA IPE JABUTI
JACA JASMIM JUJUBA KIWI LAGO LARANJA LAVANDA LIMAO LIRIO LOBO LONTRA LUA MACA MAGNOLIA MANACA MANGA MARACUJA MARE
MELANCIA MELAO MEXERICA MOCHILA NEBLINA NUVEM OLIVEIRA ORVALHO OSTRA PAINEIRA PANDA PAPAGAIO PERA PEROLA PIANO
PINHAO PIPOCA PITANGA PITAYA PLANETA POMBA QUINDIM RIACHO ROMA ROSA RUBI SABIA SAFIRA SALVIA SEREIA SERRA SORVETE
TAMARINDO TANGERINA TATU TOPAZIO TROVAO TUCANO TULIPA UVA VAGALUME VENTO VIOLA VIOLETA ZEBRA""".split()


def chave_pv(pv) -> str:
    return qld.num_chave(pv)


def rua(c: dict) -> dict:
    r = c.get("rua")
    if not isinstance(r, dict):
        r = c["rua"] = {}
    r.setdefault("palavras", {})
    r.setdefault("pedidos", {})
    r.setdefault("trilha", [])
    return r


def garantir_palavras(c: dict) -> bool:
    """Dá uma palavra a cada pedido que ainda não tem (sem repetir na carga). True = mudou."""
    r = rua(c)
    usadas = set(r["palavras"].values())
    mudou = False
    for p in c.get("pedidos") or []:
        k = chave_pv(p.get("pv"))
        if k and k not in r["palavras"]:
            livres = [w for w in PALAVRAS if w not in usadas] or PALAVRAS
            w = secrets.choice(livres)
            r["palavras"][k] = w
            usadas.add(w)
            mudou = True
    return mudou


# ══════════════════════════════════════════════════════════════════════════
# MOTORISTAS (quem dirige cada placa)
# ══════════════════════════════════════════════════════════════════════════
def placas_conhecidas() -> dict:
    """{placa: transportadora/motorista do cadastro} — cadastro da Qualidade + placas com acesso ao app."""
    out = {pl: {"motorista": (v or {}).get("motorista") or ""} for pl, v in qld.placas().items()}
    for pl in app_cfg()["placas"]:
        out.setdefault(pl, {"motorista": ""})
    for m in app_cfg()["motoristas"].values():   # placa só no cadastro de motoristas do app também entra
        if m.get("ativo", True):
            for pl in m.get("placas") or []:
                out.setdefault(norm_placa(pl), {"motorista": m.get("nome") or ""})
    return out


def _placa_no_pdf_depois(pl: str, desde: str) -> bool:
    qld._carregar()
    return any(norm_placa(c.get("placa")) == pl and not c.get("avulsa") and
               max([str(v.get("em") or "") for v in c.get("versoes") or []] or [""]) > desde for c in qld.CARGAS.values())


def semear_motoristas() -> bool:
    """O nome cadastrado em Cadastros → placa vira um motorista do app (palavra desligada)."""
    A = app_cfg()
    nomes = {(norm(m.get("nome")), pl) for m in A["motoristas"].values() for pl in m.get("placas") or []}
    mudou = False
    exc = A.setdefault("excluidos", {})
    for pl, v in qld.placas().items():
        nome = str((v or {}).get("motorista") or "").strip()
        e = exc.get(f"{norm(nome)}|{pl}")
        if e:   # [v5.6] excluído no PQI: só volta quando a placa aparecer de novo num PDF importado depois da exclusão
            if not _placa_no_pdf_depois(pl, e.get("em") or ""):
                continue
            exc.pop(f"{norm(nome)}|{pl}", None)
            mudou = True
        if nome and (norm(nome), pl) not in nomes:
            existente = next((m for m in A["motoristas"].values() if norm(m.get("nome")) == norm(nome)), None)
            if existente:
                existente["placas"] = sorted(set(existente.get("placas") or []) | {pl})
            else:
                A["motoristas"]["m" + uuid.uuid4().hex[:8]] = {"nome": nome[:120], "placas": [pl], "palavra": False, "ativo": True,
                                                              "em": agora(), "por": "cadastro da placa"}
            nomes.add((norm(nome), pl))
            mudou = True
    return mudou


def mid_da_placa(pl: str) -> str:
    """O motorista ligado à placa em Motoristas e app (ativo; se houver mais de um, o cadastrado/alterado por último)."""
    ms = [(str(m.get("em") or ""), k) for k, m in app_cfg()["motoristas"].items() if m.get("ativo", True) and pl in (m.get("placas") or [])]
    return max(ms)[1] if ms else ""


def motoristas_da_placa(pl: str) -> list:
    return sorted([{"id": k, "nome": m["nome"]} for k, m in app_cfg()["motoristas"].items()
                   if m.get("ativo", True) and pl in (m.get("placas") or [])], key=lambda x: x["nome"])


def palavra_ligada(mid: Optional[str]) -> bool:
    return bool(mid and (app_cfg()["motoristas"].get(mid) or {}).get("palavra"))


# ══════════════════════════════════════════════════════════════════════════
# SESSÃO DO APP (cookie próprio — não abre nada do PQI)
# ══════════════════════════════════════════════════════════════════════════
def _token(pl: str, mid: str, versao: int) -> str:
    return seg.criar_token(f"mot:{pl}:{mid}", versao, DURACAO_SESSAO)


def _gravar_cookie(resp: Response, pl: str, mid: str, versao: int) -> None:
    resp.set_cookie(COOKIE, _token(pl, mid, versao), max_age=DURACAO_SESSAO, httponly=True, samesite="lax",
                    secure=os.environ.get("RENDER") is not None, path="/")


def sessao(request: Request, precisa_motorista: bool = True) -> dict:
    d = seg.ler_token(request.cookies.get(COOKIE)) or {}
    m = re.fullmatch(r"mot:([A-Z0-9]{5,8}):(m[0-9a-f]{8})?", str(d.get("e") or ""))
    if not m:
        raise HTTPException(401, "Entre de novo com a placa e a senha.")
    pl, mid = m.group(1), m.group(2) or ""
    reg = app_cfg()["placas"].get(pl)
    if not reg or int(reg.get("versao", 1)) != int(d.get("v", -1)):
        raise HTTPException(401, "A senha desta placa mudou. Entre de novo.")
    mot = app_cfg()["motoristas"].get(mid)
    if not mid or not mot or not mot.get("ativo", True) or pl not in (mot.get("placas") or []):
        mid = mid_da_placa(pl)   # [v5.6] logou com a placa = é o motorista ligado a ela no PQI (sem tela de escolha)
    if precisa_motorista:
        if reg.get("trocar"):
            raise HTTPException(403, "Troque a senha inicial antes de continuar.")
        if not mid:
            raise HTTPException(403, "Esta placa não tem motorista cadastrado no PQI. Peça ao PCL (Motoristas e app).")
    return {"placa": pl, "mid": mid, "reg": reg, "nome": (app_cfg()["motoristas"].get(mid) or {}).get("nome", "")}


def _ultimo_acesso(pl: str) -> None:
    reg = app_cfg()["placas"].get(pl)
    if reg and _min_desde(reg.get("ultimo_acesso") or "") > 30:
        reg["ultimo_acesso"] = agora()
        app_salvar()


class Login(BaseModel):
    placa: str
    senha: str


class NovaSenha(BaseModel):
    nova: str
    repetir: str


class Quem(BaseModel):
    id: Optional[str] = ""
    nome_novo: Optional[str] = ""


def _placa_existe(pl: str) -> bool:
    if pl in placas_conhecidas():
        return True
    qld._carregar()
    limite = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
    return any(norm_placa(c.get("placa")) == pl and (c.get("data_iso") or "") >= limite for c in qld.CARGAS.values())


@api.post("/login")
def rota_login(payload: Login, request: Request):
    pl = norm_placa(payload.placa)
    chave = f"placa:{pl}"
    if seg.bloqueado_ate(chave):
        raise HTTPException(429, "Muitas tentativas erradas. Espere alguns minutos ou peça ao PCL para liberar.")
    with TRAVA:
        A = app_cfg()
        reg = A["placas"].get(pl)
        if reg:
            ok = seg.conferir_senha(payload.senha, reg.get("hash"))
        else:
            ok = len(pl) >= 5 and _placa_existe(pl) and secrets.compare_digest(payload.senha.encode(), SENHA_INICIAL.encode())
        if not ok:
            seg.registrar_falha(chave, "email")
            raise HTTPException(401, "Placa ou senha não conferem.")
        seg.limpar_falhas(chave)
        if not reg:
            reg = A["placas"][pl] = {"hash": seg.gerar_hash(SENHA_INICIAL), "trocar": True, "versao": 1, "em": agora(), "por": "1º acesso"}
        semear_motoristas()
        reg["ultimo_acesso"] = agora()
        app_salvar()
        r = JSONResponse({"placa": pl, "trocar_senha": bool(reg.get("trocar")), "motoristas": motoristas_da_placa(pl)})
        _gravar_cookie(r, pl, "", int(reg.get("versao", 1)))
        return r


@api.post("/senha")
def rota_senha(payload: NovaSenha, request: Request):
    s = sessao(request, precisa_motorista=False)
    n = payload.nova or ""
    if n != payload.repetir:
        raise HTTPException(400, "As duas senhas não são iguais.")
    if len(n) < 6 or n.strip() != n:
        raise HTTPException(400, "A senha precisa ter pelo menos 6 caracteres, sem espaço no começo ou no fim.")
    if n == SENHA_INICIAL:
        raise HTTPException(400, "Use uma senha diferente da inicial.")
    with TRAVA:
        reg = app_cfg()["placas"][s["placa"]]
        reg.update(hash=seg.gerar_hash(n), trocar=False, versao=int(reg.get("versao", 1)) + 1, senha_em=agora())
        app_salvar()
        r = JSONResponse({"ok": True, "motoristas": motoristas_da_placa(s["placa"])})
        _gravar_cookie(r, s["placa"], "", reg["versao"])
        return r


@api.post("/quem")
def rota_quem(payload: Quem, request: Request):
    s = sessao(request, precisa_motorista=False)
    if s["reg"].get("trocar"):
        raise HTTPException(403, "Troque a senha inicial antes de continuar.")
    with TRAVA:
        A = app_cfg()
        mid = str(payload.id or "")
        if mid:
            m = A["motoristas"].get(mid)
            if not m or not m.get("ativo", True) or s["placa"] not in (m.get("placas") or []):
                raise HTTPException(404, "Motorista não encontrado nesta placa.")
        else:   # [v5.6] o app não cadastra nome: a placa já vem ligada ao motorista no PQI
            raise HTTPException(403, "O motorista desta placa é cadastrado pelo PQI (Motoristas e app). Peça ao PCL.")
        r = JSONResponse({"ok": True, "motorista": {"id": mid, "nome": A["motoristas"][mid]["nome"]}})
        _gravar_cookie(r, s["placa"], mid, int(s["reg"].get("versao", 1)))
        return r


@api.get("/me")
def rota_me(request: Request):
    try:
        s = sessao(request, precisa_motorista=False)
    except HTTPException:
        return {"logado": False}
    return {"logado": True, "placa": s["placa"], "trocar_senha": bool(s["reg"].get("trocar")),
            "motorista": {"id": s["mid"], "nome": s["nome"]} if s["nome"] else None,
            "motoristas": motoristas_da_placa(s["placa"])}


@api.post("/sair")
def rota_sair():
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE, path="/")
    return r


# ══════════════════════════════════════════════════════════════════════════
# CARGAS DO MOTORISTA
# ══════════════════════════════════════════════════════════════════════════
def cargas_da_placa(pl: str) -> list:
    qld._carregar()
    limite = (datetime.now() - timedelta(days=DIAS_APP)).strftime("%Y-%m-%d")
    out = []
    for c in qld.CARGAS.values():
        if norm_placa(c.get("placa")) != pl or (c.get("data_iso") or "") < limite:
            continue
        r = c.get("rua") or {}
        chegou_hoje = (r.get("chegada") or {}).get("em", "")[:10] == datetime.now().strftime("%Y-%m-%d")
        if (c.get("status") in STATUS_RUA and not r.get("chegada")) or chegou_hoje:
            out.append(c)
    return sorted(out, key=lambda c: (c.get("data_iso") or "", c.get("numero") or ""))


def _pedido_app(c: dict, p: dict, mid: str) -> dict:
    """O pedido como o app vê — SEM a palavra."""
    k = chave_pv(p.get("pv"))
    ev = (rua(c)["pedidos"].get(k) or {})
    tent = int(ev.get("tentativas") or 0)
    lib = ev.get("liberacao") or {}
    return {"seq": p.get("seq"), "pv": p.get("pv"), "nome": p.get("nome"), "endereco": p.get("endereco"), "compl": p.get("compl"),
            "bairro": p.get("bairro"), "cidade": p.get("cidade"), "cep": p.get("cep"), "troca": bool(p.get("troca")),
            "itens": [{"i": i, "desc": it.get("desc"), "qtd": int(it.get("qtd") or 0)} for i, it in enumerate(p.get("entregar") or [])],
            "retirar": [{"desc": it.get("desc"), "qtd": int(it.get("qtd") or 0)} for it in (p.get("col2") or [])] if p.get("troca") else [],
            "desfecho": ev.get("desfecho") or "", "motivo": ev.get("motivo") or "", "em": ev.get("em") or "", "por": ev.get("por") or "",
            "palavra_conferida": bool(ev.get("palavra_ok")), "liberacao": lib.get("status") or "",
            "tentativas": tent, "pode_desfazer": bool(ev.get("desfecho")) and ev.get("mid") == mid and _min_desde(ev.get("em")) <= DESFAZER_MIN
                                                and not ev.get("baixa_erp"),
            "chat": _chat_resumo(c, p, "motorista"), "motivo_chat": motivo_do_chat(c, p),
            "nf": p.get("nf"), "cliente": p.get("cliente"), "obs": p.get("obs") or [], "cotacao": qld.cotacao(p),
            "validar_palavra": (ev.get("validar_palavra") or {}).get("status") or ""}


def carga_app(c: dict, mid: str) -> dict:
    r = rua(c)
    peds = [_pedido_app(c, p, mid) for p in c.get("pedidos") or []]
    return {"numero": c["numero"], "data_iso": c.get("data_iso"), "transportadora": c.get("transportadora"), "status": c.get("status"),
            "saida": r.get("saida"), "chegada": r.get("chegada"), "pedidos": peds,
            "feitos": sum(1 for p in peds if p["desfecho"]), "total": len(peds),
            "palavra_ligada": palavra_ligada(mid), "tentativas_max": int(app_cfg()["cfg"].get("tentativas") or 5),
            "motivos": motivos_chat(), "motivos_chat": motivos_chat()}   # [v5.7] retorno/parcial usam os MESMOS motivos do chat


@api.get("/cargas")
def rota_cargas(request: Request):
    s = sessao(request)
    _ultimo_acesso(s["placa"])
    with TRAVA:
        cs = cargas_da_placa(s["placa"])
        for c in cs:
            if garantir_palavras(c):
                qld.gravar(c)
        return {"placa": s["placa"], "motorista": {"id": s["mid"], "nome": s["nome"]}, "cargas": [carga_app(c, s["mid"]) for c in cs],
                "servidor": agora()}


def _carga_do_motorista(s: dict, numero: str) -> dict:
    c = qld.carga(numero)
    if norm_placa(c.get("placa")) != s["placa"]:
        raise HTTPException(404, "Esta carga não é da sua placa.")
    if c.get("status") not in STATUS_RUA:
        raise HTTPException(409, "Esta carga não está liberada para entrega (ou já foi fechada no CD).")
    if (c.get("rua") or {}).get("chegada"):
        raise HTTPException(409, "Você já avisou a chegada no CD. Correções agora só pelo PCL.")
    return c


def _ped_da_carga(c: dict, pv: str) -> dict:
    p = next((x for x in c.get("pedidos") or [] if chave_pv(x.get("pv")) == chave_pv(pv)), None)
    if not p:
        raise HTTPException(404, "Pedido não encontrado nesta carga.")
    return p


def _hist(c: dict, s: dict, acao: str, det: str = "") -> None:
    qld.hist(c, {"por": f"{s['nome']} (motorista · {s['placa']})", "email": f"motorista:{s['placa']}"}, acao, det)


class Posicao(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    prec: Optional[float] = None


class Desfecho(Posicao):
    cid: Optional[str] = ""          # id do registro gerado no celular (reenvio offline não duplica)
    desfecho: str                    # entregue | retorno | parcial
    palavra: Optional[str] = ""
    motivo: Optional[str] = ""
    linhas: list = []                # parcial: [{i, qtd}] que VOLTARAM
    obs: Optional[str] = ""
    foto: Optional[str] = ""         # data:image/jpeg;base64,...
    offline_em: Optional[str] = ""   # [v5.10] registrado no celular SEM SINAL (hora do celular); enviado quando a conexão voltou


class PedidoLib(BaseModel):
    motivo: Optional[str] = ""


FOTO_OBRIGATORIA = re.compile(r"AVARIA|RECUS|DIVERG|ERRAD", re.I)


@api.post("/cargas/{numero}/saida")
def rota_saida(numero: str, payload: Posicao, request: Request):
    s = sessao(request)
    with TRAVA:
        c = _carga_do_motorista(s, numero)
        r = rua(c)
        if not r.get("saida"):
            garantir_palavras(c)
            r["saida"] = {"em": agora(), "mid": s["mid"], "nome": s["nome"], "lat": payload.lat, "lng": payload.lng}
            _hist(c, s, "saiu para entrega (app)", f"{len(c.get('pedidos') or [])} pedido(s)")
            qld.gravar(c)
        return carga_app(c, s["mid"])


@api.post("/cargas/{numero}/pedido/{pv}")
def rota_desfecho(numero: str, pv: str, payload: Desfecho, request: Request):
    s = sessao(request)
    if payload.desfecho not in ("entregue", "retorno", "parcial"):
        raise HTTPException(400, "Escolha Entregue, Retorno ou Parcial.")
    with TRAVA:
        c = _carga_do_motorista(s, numero)
        p = _ped_da_carga(c, pv)
        r = rua(c)
        garantir_palavras(c)
        k = chave_pv(p.get("pv"))
        ev = r["pedidos"].setdefault(k, {})
        if ev.get("desfecho"):
            if payload.cid and ev.get("cid") == payload.cid:
                return {"resultado": "ok", "carga": carga_app(c, s["mid"])}   # reenvio do mesmo registro (estava sem sinal)
            raise HTTPException(409, "Este pedido já tem registro. Use Corrigir (até 15 min) ou fale com o PCL.")
        if not r.get("saida"):
            r["saida"] = {"em": agora(), "mid": s["mid"], "nome": s["nome"], "lat": payload.lat, "lng": payload.lng, "auto": True}
        maxt = int(app_cfg()["cfg"].get("tentativas") or 5)
        palavra_ok = None
        validar = None
        offline = bool(payload.offline_em)
        if payload.desfecho in ("entregue", "parcial") and palavra_ligada(s["mid"]):
            lib = (ev.get("liberacao") or {}).get("status")
            if lib != "liberada":
                certa = norm(payload.palavra) == norm(r["palavras"].get(k)) and bool(str(payload.palavra or "").strip())
                if offline and not certa:
                    # [v5.10] entregou SEM SINAL e a palavra não confere (ou não foi digitada): o registro vale,
                    # mas a FROTA precisa validar no Ao vivo
                    validar = {"status": "pendente", "digitada": str(payload.palavra or "")[:60], "em": agora(), "offline_em": payload.offline_em}
                elif int(ev.get("tentativas") or 0) >= maxt:
                    raise HTTPException(423, "Tentativas esgotadas. Peça a liberação ao PCL.")
                elif not str(payload.palavra or "").strip():
                    return {"resultado": "pede_palavra"}
                elif not certa:
                    ev["tentativas"] = int(ev.get("tentativas") or 0) + 1
                    ev["ultima_tentativa"] = agora()
                    _hist(c, s, "palavra da entrega incorreta", f"PV {p.get('pv')} · {ev['tentativas']}/{maxt}")
                    qld.gravar(c)
                    restam = maxt - ev["tentativas"]
                    return {"resultado": "palavra_errada", "restam": restam, "carga": carga_app(c, s["mid"])}
                else:
                    palavra_ok = True
            else:
                palavra_ok = False   # liberada pelo PCL sem palavra
        motivo = str(payload.motivo or "").strip()[:120]
        linhas = []
        if payload.desfecho in ("retorno", "parcial"):
            motivo = motivo or motivo_do_chat(c, p)[:120]
            if not motivo:
                raise HTTPException(400, "Escolha o motivo do retorno.")
            if not payload.foto:   # [v5.7] foto obrigatória em todo retorno e parcial
                raise HTTPException(400, "Tire a foto da peça para registrar o retorno.")
        if payload.desfecho == "parcial":
            for ln in payload.linhas or []:
                try:
                    i, n = int(ln.get("i")), int(ln.get("qtd") or 0)
                except (TypeError, ValueError, AttributeError):
                    continue
                its = p.get("entregar") or []
                if 0 <= i < len(its) and 0 < n <= int(its[i].get("qtd") or 0):
                    linhas.append({"i": i, "qtd": n, "desc": its[i].get("desc")})
            if not linhas:
                raise HTTPException(400, "Marque quais peças voltaram.")
        foto_id = ""
        if payload.foto:
            foto_id = _salvar_foto(payload.foto, c["numero"], p.get("pv"))
        ev.update(cid=str(payload.cid or "")[:40], desfecho=payload.desfecho, motivo=motivo if payload.desfecho != "entregue" else "",
                  linhas=linhas, obs=str(payload.obs or "")[:300], foto=foto_id, em=agora(), mid=s["mid"], por=s["nome"],
                  lat=payload.lat, lng=payload.lng, palavra_ok=palavra_ok, validar_palavra=validar,
                  offline_em=str(payload.offline_em or "")[:40] or None)
        if payload.desfecho in ("retorno", "parcial"):
            _retorno_do_app(c, p, ev, s)
        rot = {"entregue": "ENTREGUE", "retorno": "RETORNO", "parcial": "ENTREGA PARCIAL"}[payload.desfecho]
        _hist(c, s, f"{rot} (app)", f"PV {p.get('pv')} · {p.get('nome')}" + (f" · {motivo}" if motivo and payload.desfecho != "entregue" else "")
              + (" · palavra conferida" if palavra_ok else (" · liberado pelo PCL" if palavra_ok is False else ""))
              + (f" · registrado SEM SINAL às {str(payload.offline_em)[11:16]}" if offline else "")
              + (" · PALAVRA NÃO CONFERIU: frota precisa validar" if validar else ""))
        qld.gravar(c)
        return {"resultado": "ok", "validar_palavra": bool(validar), "carga": carga_app(c, s["mid"])}


# ══════════════════════════════════════════════════════════════════════════
# [v5.7] O RETORNO SE ALIMENTA DO APP: retorno/parcial registrado na rua já entra na aba Retorno
# como "informado pelo motorista"; o CD só confirma que a peça chegou (estado e destino).
# ══════════════════════════════════════════════════════════════════════════
def _retorno_do_app(c: dict, p: dict, ev: dict, s: dict) -> int:
    ret = c.get("retorno") or {"inicio": agora(), "por": "app do motorista", "email": "", "itens": [], "fim": None}
    c["retorno"] = ret
    linhas = ev.get("linhas") if ev.get("desfecho") == "parcial" else [{"i": i, "qtd": int(it.get("qtd") or 0)} for i, it in enumerate(p.get("entregar") or [])]
    its = p.get("entregar") or []
    avaria = bool(re.search(r"AVARI", ev.get("motivo") or "", re.I))
    n = 0
    for ln in linhas or []:
        i = int(ln.get("i"))
        if not 0 <= i < len(its):
            continue
        for _ in range(int(ln.get("qtd") or 0)):
            ret["itens"].append({"id": uuid.uuid4().hex[:8], "pv": p.get("pv"), "seq": p.get("seq"), "i": i, "sku": its[i].get("sku"), "desc": its[i].get("desc"),
                                 "nome": p.get("nome"), "origem": "nao_entregue", "motivo": ev.get("motivo") or "", "estado": "avariada" if avaria else "",
                                 "avaria": "", "destino": "", "obs": ev.get("obs") or "", "modo": "app", "fonte": "app", "recebido": False,
                                 "foto": ev.get("foto") or "", "app_cid": ev.get("cid") or "", "codigo": "", "lote": "",
                                 "por": s["nome"], "email": f"motorista:{s['placa']}", "em": agora(), "tabulacao": None})
            n += 1
    if c.get("status") == "validada" and n:
        c["status"] = "em_retorno"
    return n


def _tirar_retorno_do_app(c: dict, p: dict) -> int:
    """Registro do motorista desfeito/reaberto: sai do Retorno o que ainda não foi recebido no CD."""
    ret = c.get("retorno") or {}
    antes = len(ret.get("itens") or [])
    ret["itens"] = [x for x in ret.get("itens") or [] if not (x.get("fonte") == "app" and not x.get("recebido") and chave_pv(x.get("pv")) == chave_pv(p.get("pv")))]
    if ret and not ret["itens"] and c.get("status") == "em_retorno":
        c["status"] = "validada"
    return antes - len(ret.get("itens") or [])


@api.post("/cargas/{numero}/pedido/{pv}/desfazer")
def rota_desfazer(numero: str, pv: str, request: Request):
    s = sessao(request)
    with TRAVA:
        c = _carga_do_motorista(s, numero)
        p = _ped_da_carga(c, pv)
        ev = rua(c)["pedidos"].get(chave_pv(p.get("pv"))) or {}
        if not ev.get("desfecho"):
            raise HTTPException(404, "Não há registro para corrigir.")
        if ev.get("mid") != s["mid"] or _min_desde(ev.get("em")) > DESFAZER_MIN or ev.get("baixa_erp"):
            raise HTTPException(409, f"Só dá para corrigir o próprio registro em até {DESFAZER_MIN} minutos. Fale com o PCL.")
        ant = {k: ev.get(k) for k in ("desfecho", "motivo", "em")}
        _tirar_retorno_do_app(c, p)
        for k in ("cid", "desfecho", "motivo", "linhas", "obs", "foto", "em", "mid", "por", "lat", "lng", "palavra_ok"):
            ev.pop(k, None)
        ev.setdefault("corrigidos", []).append({**ant, "desfeito_em": agora()})
        _hist(c, s, "registro corrigido pelo motorista", f"PV {p.get('pv')} · era {ant['desfecho']}")
        qld.gravar(c)
        return carga_app(c, s["mid"])


@api.post("/cargas/{numero}/pedido/{pv}/liberacao")
def rota_pedir_liberacao(numero: str, pv: str, payload: PedidoLib, request: Request):
    s = sessao(request)
    with TRAVA:
        c = _carga_do_motorista(s, numero)
        p = _ped_da_carga(c, pv)
        ev = rua(c)["pedidos"].setdefault(chave_pv(p.get("pv")), {})
        if ev.get("desfecho"):
            raise HTTPException(409, "Este pedido já tem registro.")
        if (ev.get("liberacao") or {}).get("status") in ("pendente", "liberada"):
            return carga_app(c, s["mid"])
        ev["liberacao"] = {"status": "pendente", "pedido_em": agora(), "pedido_por": s["nome"], "mid": s["mid"],
                           "motivo": str(payload.motivo or "Cliente não tem a palavra")[:200]}
        _hist(c, s, "pediu liberação sem palavra", f"PV {p.get('pv')} · {ev['liberacao']['motivo']}")
        qld.gravar(c)
        return carga_app(c, s["mid"])


@api.post("/cargas/{numero}/chegada")
def rota_chegada(numero: str, payload: Posicao, request: Request):
    s = sessao(request)
    with TRAVA:
        c = _carga_do_motorista(s, numero)
        r = rua(c)
        faltam = [p.get("nome") for p in c.get("pedidos") or [] if not (r["pedidos"].get(chave_pv(p.get("pv"))) or {}).get("desfecho")]
        if faltam:
            raise HTTPException(409, f"Ainda falta registrar {len(faltam)} pedido(s): {', '.join(str(x) for x in faltam[:3])}.")
        r["chegada"] = {"em": agora(), "mid": s["mid"], "nome": s["nome"], "lat": payload.lat, "lng": payload.lng}
        voltam = sum(1 for e in r["pedidos"].values() if e.get("desfecho") in ("retorno", "parcial"))
        _hist(c, s, "chegou no CD (app)", f"{voltam} pedido(s) com peça voltando")
        qld.gravar(c)
        return carga_app(c, s["mid"])


# ── GPS ───────────────────────────────────────────────────────────────────
GPS: dict = {}   # placa → última posição (memória: ao vivo)


@api.post("/gps")
def rota_gps(payload: Posicao, request: Request):
    s = sessao(request)
    if payload.lat is None or payload.lng is None or not (-90 <= payload.lat <= 90 and -180 <= payload.lng <= 180):
        raise HTTPException(400, "Posição inválida.")
    pos = {"lat": round(payload.lat, 6), "lng": round(payload.lng, 6), "prec": round(payload.prec or 0), "em": agora(),
           "t": time.time(), "nome": s["nome"]}
    GPS[s["placa"]] = pos
    with TRAVA:
        for c in cargas_da_placa(s["placa"]):
            r = rua(c)
            if r.get("saida") and not r.get("chegada"):
                ult = r["trilha"][-1] if r["trilha"] else None
                if not ult or time.time() - float(ult[3] if len(ult) > 3 else 0) >= TRILHA_SEG:
                    r["trilha"].append([pos["lat"], pos["lng"], pos["em"][11:16], round(time.time())])
                    r["trilha"] = r["trilha"][-400:]
                    r["gps"] = {k: pos[k] for k in ("lat", "lng", "prec", "em")}
                    qld.gravar(c)
    return {"ok": True}


# ── FOTOS ─────────────────────────────────────────────────────────────────
def _salvar_foto(data_url: str, numero: str, pv) -> str:
    m = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", str(data_url or ""))
    if not m or len(m.group(2)) > FOTO_MAX:
        raise HTTPException(413, "Foto grande demais ou inválida. Tire de novo.")
    try:
        base64.b64decode(m.group(2), validate=True)
    except Exception:
        raise HTTPException(400, "Foto inválida.")
    fid = uuid.uuid4().hex[:16]
    _fs_set(COL_FOTOS, fid, {"tipo": m.group(1), "b64": m.group(2), "carga": numero, "pv": str(pv or ""), "em": agora()})
    return fid


# ══════════════════════════════════════════════════════════════════════════
# PQI — PAINEL AO VIVO (aba "aovivo") e MOTORISTAS E APP (aba "motoristas")
# ══════════════════════════════════════════════════════════════════════════
def _ev_pub(c: dict, p: dict, com_palavra: bool) -> dict:
    r = rua(c)
    k = chave_pv(p.get("pv"))
    ev = r["pedidos"].get(k) or {}
    cd = [x for x in (c.get("retorno") or {}).get("itens") or [] if chave_pv(x.get("pv")) == k and x.get("origem") == "nao_entregue" and x.get("recebido") is not False]
    esperado = 0
    if ev.get("desfecho") == "retorno":
        esperado = sum(int(i.get("qtd") or 0) for i in p.get("entregar") or [])
    elif ev.get("desfecho") == "parcial":
        esperado = sum(int(x.get("qtd") or 0) for x in ev.get("linhas") or [])
    out = {"seq": p.get("seq"), "pv": p.get("pv"), "nome": p.get("nome"), "bairro": p.get("bairro"), "cidade": p.get("cidade"),
           "cliente": p.get("cliente"), "nf": p.get("nf"), "cotacao": qld.cotacao(p),
           "validar_palavra": ev.get("validar_palavra"), "offline_em": ev.get("offline_em"),
           "troca": bool(p.get("troca")), "pecas": sum(int(i.get("qtd") or 0) for i in p.get("entregar") or []),
           "desfecho": ev.get("desfecho") or "", "motivo": ev.get("motivo") or "", "obs": ev.get("obs") or "", "em": ev.get("em") or "",
           "por": ev.get("por") or "", "foto": ev.get("foto") or "", "lat": ev.get("lat"), "lng": ev.get("lng"),
           "linhas": ev.get("linhas") or [], "palavra_ok": ev.get("palavra_ok"), "tentativas": int(ev.get("tentativas") or 0),
           "liberacao": ev.get("liberacao"), "baixa_erp": ev.get("baixa_erp"), "corrigidos": len(ev.get("corrigidos") or []),
           "retorno_esperado": esperado, "retorno_recebido": len(cd), "chat": _chat_resumo(c, p, "frota")}
    if com_palavra:
        out["palavra"] = r["palavras"].get(k, "")
    return out


def _carga_viva(c: dict, com_palavra: bool) -> dict:
    r = rua(c)
    peds = [_ev_pub(c, p, com_palavra) for p in c.get("pedidos") or []]
    mot = (r.get("saida") or {}).get("nome") or c.get("motorista") or ""
    mid = (r.get("saida") or {}).get("mid")
    pos = GPS.get(norm_placa(c.get("placa")))
    if r.get("chegada") or not r.get("saida"):
        pos_pub = None
    else:
        pos_pub = {**{k: pos[k] for k in ("lat", "lng", "prec", "em")}, "ha_seg": int(time.time() - pos["t"])} if pos else (
            {**r["gps"], "ha_seg": None} if r.get("gps") else None)
    fase = "chegou" if r.get("chegada") else ("rua" if r.get("saida") else "aguardando")
    return {"numero": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "transportadora": c.get("transportadora"),
            "motorista": mot, "palavra_ligada": palavra_ligada(mid) if mid else None, "status": c.get("status"), "fase": fase,
            "saida": r.get("saida"), "chegada": r.get("chegada"), "pos": pos_pub,
            "trilha": [t[:3] for t in r.get("trilha") or []], "pedidos": peds,
            "cont": {"total": len(peds), "entregue": sum(1 for p in peds if p["desfecho"] == "entregue"),
                     "retorno": sum(1 for p in peds if p["desfecho"] == "retorno"), "parcial": sum(1 for p in peds if p["desfecho"] == "parcial"),
                     "pendente": sum(1 for p in peds if not p["desfecho"]),
                     "sem_baixa_erp": sum(1 for p in peds if p["desfecho"] in ("entregue", "parcial") and not p["baixa_erp"]),
                     "liberacoes": sum(1 for p in peds if (p["liberacao"] or {}).get("status") == "pendente"),
                     "pecas_esperadas": sum(p["retorno_esperado"] for p in peds), "pecas_recebidas": sum(p["retorno_recebido"] for p in peds),
                     "chats_abertos": sum(1 for p in peds if p["chat"]["aberto"]), "chats_nao_lidos": sum(p["chat"]["nao_lidas"] for p in peds)}}


@painel_api.get("/ao-vivo")
def rota_ao_vivo(request: Request, data: str = ""):
    rk_exigir(request, ["aovivo"], 1, "Ver o painel ao vivo")
    dia = qld.iso_ok(data, qld.hoje())
    com_palavra = rk_nivel(request, "aovivo") >= 1
    with TRAVA:
        qld._carregar()
        cs = []
        for c in qld.CARGAS.values():
            if c.get("avulsa"):
                continue   # entrada avulsa no Retorno não sai para a rua
            r = c.get("rua") or {}
            ativa = c.get("status") in STATUS_RUA and not r.get("chegada") and (c.get("data_iso") or "") >= (
                datetime.fromisoformat(dia) - timedelta(days=DIAS_APP)).strftime("%Y-%m-%d")
            do_dia = any(str((x or {}).get("em", ""))[:10] == dia for x in (r.get("saida"), r.get("chegada"))) or (c.get("data_iso") == dia)
            if (dia == qld.hoje() and ativa) or do_dia:
                if garantir_palavras(c):
                    qld.gravar(c)
                cs.append(c)
        lista = [_carga_viva(c, com_palavra) for c in sorted(cs, key=lambda x: (x.get("data_iso") or "", x.get("numero") or ""))]
    soma = lambda k: sum(x["cont"][k] for x in lista)
    return {"data": dia, "servidor": agora(), "pode_tratar": rk_nivel(request, "aovivo") >= 2,
            "kpi": {"na_rua": sum(1 for x in lista if x["fase"] == "rua"), "aguardando_saida": sum(1 for x in lista if x["fase"] == "aguardando"),
                    "chegaram": sum(1 for x in lista if x["fase"] == "chegou"), "entregues": soma("entregue"), "retornos": soma("retorno") + soma("parcial"),
                    "pendentes": soma("pendente"), "sem_baixa_erp": soma("sem_baixa_erp"), "liberacoes": soma("liberacoes"),
                    "pecas_a_receber": max(0, soma("pecas_esperadas") - soma("pecas_recebidas")),
                    "chats_abertos": soma("chats_abertos"), "chats_nao_lidos": soma("chats_nao_lidos")},
            "cargas": lista}


# ══════════════════════════════════════════════════════════════════════════
# [v5.5] CHAT motorista ↔ frota (Ao vivo), por cliente da carga. Fica gravado na carga.
#   rua.chats[pv] = {"atual": id|None, "atendimentos": [{id, motivo, aberto_em, mid, nome,
#                    msgs:[{de: motorista|frota|sistema, nome, txt, em}], lido_frota, lido_motorista,
#                    fechado_em, fechado_por}]}
# ══════════════════════════════════════════════════════════════════════════
def motivos_chat() -> list:
    return list(app_cfg()["cfg"].get("motivos_chat") or MOTIVOS_CHAT)


def _chats(c: dict, p: dict) -> dict:
    r = rua(c)
    return r.setdefault("chats", {}).setdefault(chave_pv(p.get("pv")), {"atual": None, "atendimentos": []})


def motivo_do_chat(c: dict, p: dict) -> str:
    """O motivo escolhido no chat deste cliente (o atual ou o último) — vale para o Retorno/Parcial sem perguntar de novo."""
    ch = ((c.get("rua") or {}).get("chats") or {}).get(chave_pv(p.get("pv"))) or {}
    ats = ch.get("atendimentos") or []
    return str(ats[-1].get("motivo") or "") if ats else ""


def _chat_atual(ch: dict) -> Optional[dict]:
    return next((a for a in ch["atendimentos"] if a["id"] == ch.get("atual")), None) if ch.get("atual") else None


def chat_minutos() -> int:
    return int(app_cfg()["cfg"].get("chat_minutos") or CHAT_MINUTOS)


def _restante(a: dict) -> Optional[int]:
    """Segundos até o fim do prazo do atendimento (negativo = estourou). Contado no servidor (o relógio do celular não manda)."""
    if not a or a.get("fechado_em") or not a.get("prazo_ts"):
        return None
    return int(float(a["prazo_ts"]) - time.time())


def _a_pub(a: Optional[dict]) -> Optional[dict]:
    return {**a, "restante_seg": _restante(a)} if a else None


def _chat_resumo(c: dict, p: dict, lado: str) -> dict:
    ch = ((c.get("rua") or {}).get("chats") or {}).get(chave_pv(p.get("pv"))) or {"atual": None, "atendimentos": []}
    a = _chat_atual(ch)
    if not a:
        return {"aberto": False, "motivo": "", "nao_lidas": 0, "ult_em": "", "atendimentos": len(ch.get("atendimentos") or [])}
    outro = "motorista" if lado == "frota" else "frota"
    lidas = int(a.get(f"lido_{lado}") or 0)
    return {"aberto": True, "motivo": a.get("motivo"), "ult_em": (a["msgs"][-1]["em"] if a["msgs"] else a.get("aberto_em")),
            "restante_seg": _restante(a), "prazo_min": a.get("prazo_min"),
            "nao_lidas": sum(1 for m in a["msgs"][lidas:] if m.get("de") == outro), "atendimentos": len(ch["atendimentos"])}


def _chat_pub(ch: dict, lado: str) -> dict:
    a = _chat_atual(ch)
    if a:
        a[f"lido_{lado}"] = len(a["msgs"])
    return {"atual": _a_pub(a), "atendimentos": ch["atendimentos"]}


def _msg(a: dict, de: str, nome: str, txt: str) -> None:
    a["msgs"].append({"de": de, "nome": nome, "txt": txt[:500], "em": agora()})
    a["msgs"] = a["msgs"][-CHAT_MAX_MSGS:]


def _carga_chat_motorista(s: dict, numero: str) -> dict:
    c = qld.carga(numero)
    if norm_placa(c.get("placa")) != s["placa"]:
        raise HTTPException(404, "Esta carga não é da sua placa.")
    if c.get("status") not in STATUS_RUA:
        raise HTTPException(409, "Esta carga não está mais com você.")
    return c


class ChatIn(BaseModel):
    motivo: Optional[str] = ""
    texto: Optional[str] = ""


@api.get("/cargas/{numero}/pedido/{pv}/chat")
def rota_chat_app(numero: str, pv: str, request: Request):
    s = sessao(request)
    with TRAVA:
        c = _carga_chat_motorista(s, numero)
        ch = _chats(c, _ped_da_carga(c, pv))
        a = _chat_atual(ch)
        mudou = bool(a and a.get("lido_motorista") != len(a["msgs"]))
        out = {"atual": _chat_pub(ch, "motorista")["atual"], "motivos": motivos_chat()}
        if mudou:
            qld.gravar(c)
        return out


@api.post("/cargas/{numero}/pedido/{pv}/chat")
def rota_chat_app_enviar(numero: str, pv: str, payload: ChatIn, request: Request):
    """1ª vez: escolhe o motivo e abre o atendimento com a frota. Depois: manda mensagem."""
    s = sessao(request)
    txt = str(payload.texto or "").strip()
    with TRAVA:
        c = _carga_chat_motorista(s, numero)
        p = _ped_da_carga(c, pv)
        ch = _chats(c, p)
        a = _chat_atual(ch)
        if not a:
            motivo = str(payload.motivo or "").strip()
            if motivo not in motivos_chat():
                raise HTTPException(400, "Escolha o motivo para falar com a frota.")
            a = {"id": uuid.uuid4().hex[:8], "motivo": motivo, "aberto_em": agora(), "mid": s["mid"], "nome": s["nome"], "placa": s["placa"],
                 "msgs": [], "lido_frota": 0, "lido_motorista": 0, "fechado_em": None, "fechado_por": None,
                 "prazo_min": chat_minutos(), "prazo_ts": time.time() + chat_minutos() * 60}
            _msg(a, "sistema", "", f"{s['nome']} abriu o chat: {motivo}")
            ch["atendimentos"].append(a)
            ch["atual"] = a["id"]
            _hist(c, s, "chat com a frota aberto", f"PV {p.get('pv')} · {p.get('nome')} · {motivo}")
        if txt:
            _msg(a, "motorista", s["nome"], txt)
        a["lido_motorista"] = len(a["msgs"])
        qld.gravar(c)
        return {"atual": _a_pub(a), "motivos": motivos_chat()}


def _dados_cliente(c: dict, p: dict) -> dict:
    """Tudo do cliente para a frota: endereço, nota, peças, retirada, observações do Mapa e o que aconteceu na rua."""
    r = rua(c)
    k = chave_pv(p.get("pv"))
    return {"carga": c["numero"], "placa": c.get("placa"), "motorista": ((r.get("saida") or {}).get("nome") or c.get("motorista") or ""),
            "transportadora": c.get("transportadora"), "palavra": r["palavras"].get(k, ""),
            **{x: p.get(x) for x in ("seq", "tipo", "pv", "nf", "serie", "cliente", "nome", "endereco", "compl", "bairro", "cidade", "cep",
                                     "despesas", "troca", "pv_retira", "nf_troca", "protocolo", "obs")},
            "itens": [{"desc": it.get("desc"), "sku": it.get("sku"), "qtd": int(it.get("qtd") or 0)} for it in p.get("entregar") or []],
            "retirar": [{"desc": it.get("desc"), "qtd": int(it.get("qtd") or 0)} for it in p.get("col2") or []],
            "evento": _ev_pub(c, p, True), "cotacao": qld.cotacao(p), "data_mapa": c.get("data_mapa"), "data_iso": c.get("data_iso"),
            "saida": r.get("saida"), "chegada": r.get("chegada"), "trocas_placa": c.get("trocas_placa") or [],
            "corrigidos": (r["pedidos"].get(k) or {}).get("corrigidos") or [],
            "retorno_itens": [x for x in (c.get("retorno") or {}).get("itens") or [] if chave_pv(x.get("pv")) == k],
            "historico": [h for h in c.get("historico") or [] if str(p.get("pv")) in str(h.get("det") or "") or k in str(h.get("det") or "")]}


@painel_api.get("/cargas/{numero}/pedido/{pv}")
def rota_cliente(numero: str, pv: str, request: Request):
    """Ao vivo: o cliente completo + o chat com o motorista (abrir marca as mensagens como lidas pela frota)."""
    rk_exigir(request, ["aovivo"], 1, "Ver o cliente")
    with TRAVA:
        c = qld.carga(numero)
        p = _ped_da_carga(c, pv)
        ch = _chats(c, p)
        a = _chat_atual(ch)
        mudou = bool(a and a.get("lido_frota") != len(a["msgs"]))
        out = {**_dados_cliente(c, p), "chat": _chat_pub(ch, "frota"), "pode_tratar": rk_nivel(request, "aovivo") >= 2}
        if mudou:
            qld.gravar(c)
        return out


@painel_api.post("/cargas/{numero}/pedido/{pv}/chat")
def rota_chat_frota(numero: str, pv: str, payload: ChatIn, request: Request):
    rk_exigir(request, ["aovivo"], 2, "Responder o motorista")
    q = qld.quem(request)
    txt = str(payload.texto or "").strip()
    if not txt:
        raise HTTPException(400, "Mensagem vazia.")
    with TRAVA:
        c = qld.carga(numero)
        ch = _chats(c, _ped_da_carga(c, pv))
        a = _chat_atual(ch)
        if not a:
            raise HTTPException(409, "O motorista não tem chat aberto para este cliente.")
        _msg(a, "frota", q["por"], txt)
        a["lido_frota"] = len(a["msgs"])
        qld.gravar(c)
        return _chat_pub(ch, "frota")


@painel_api.post("/cargas/{numero}/pedido/{pv}/chat/encerrar")
def rota_chat_encerrar(numero: str, pv: str, payload: ChatIn, request: Request):
    rk_exigir(request, ["aovivo"], 2, "Encerrar o atendimento")
    q = qld.quem(request)
    with TRAVA:
        c = qld.carga(numero)
        p = _ped_da_carga(c, pv)
        ch = _chats(c, p)
        a = _chat_atual(ch)
        if not a:
            raise HTTPException(409, "Não há atendimento aberto.")
        nota = str(payload.texto or "").strip()
        _msg(a, "sistema", "", f"{q['por']} encerrou o atendimento" + (f": {nota}" if nota else "."))
        a.update(fechado_em=agora(), fechado_por=q["por"], lido_frota=len(a["msgs"]))
        ch["atual"] = None
        qld.hist(c, q, "chat com o motorista encerrado", f"PV {p.get('pv')} · {p.get('nome')} · {a.get('motivo')} · {len(a['msgs'])} mensagem(ns)")
        qld.gravar(c)
        return _chat_pub(ch, "frota")


class ValidaPalavra(BaseModel):
    acao: str            # aceitar | reabrir
    nota: Optional[str] = ""


@painel_api.put("/cargas/{numero}/validar-palavra/{pv}")
def rota_validar_palavra(numero: str, pv: str, payload: ValidaPalavra, request: Request):
    """[v5.10] Entrega feita SEM SINAL com a palavra errada: a frota aceita (vale a entrega) ou reabre (o motorista registra de novo)."""
    rk_exigir(request, ["aovivo"], 2, "Validar entrega sem palavra")
    q = qld.quem(request)
    with TRAVA:
        c = qld.carga(numero)
        p = _ped_da_carga(c, pv)
        ev = rua(c)["pedidos"].get(chave_pv(p.get("pv"))) or {}
        v = ev.get("validar_palavra") or {}
        if v.get("status") != "pendente":
            raise HTTPException(409, "Não há palavra para validar neste pedido.")
        if payload.acao == "aceitar":
            v.update(status="aceita", por=q["por"], decidido_em=agora(), nota=str(payload.nota or "")[:200])
            ev["palavra_ok"] = False
            ev["liberacao"] = {"status": "liberada", "pedido_em": v.get("em"), "pedido_por": "app (sem sinal)", "decidido_por": q["por"],
                               "decidido_em": agora(), "nota": "entrega sem sinal validada pela frota" + (f": {payload.nota}" if payload.nota else "")}
            qld.hist(c, q, "entrega sem sinal VALIDADA pela frota", f"PV {p.get('pv')} · {p.get('nome')} · palavra digitada: {v.get('digitada') or '—'}")
        elif payload.acao == "reabrir":
            v.update(status="reaberta", por=q["por"], decidido_em=agora(), nota=str(payload.nota or "")[:200])
            ant = {kk: ev.get(kk) for kk in ("desfecho", "motivo", "em", "por")}
            _tirar_retorno_do_app(c, p)
            for kk in ("cid", "desfecho", "motivo", "linhas", "obs", "foto", "em", "mid", "por", "lat", "lng", "palavra_ok", "validar_palavra", "offline_em"):
                ev.pop(kk, None)
            ev.setdefault("corrigidos", []).append({**ant, "desfeito_em": agora(), "pelo_pcl": q["por"], "nota": "palavra não conferiu (sem sinal)"})
            qld.hist(c, q, "entrega sem sinal REABERTA pela frota", f"PV {p.get('pv')} · {p.get('nome')} · palavra digitada: {v.get('digitada') or '—'}")
        else:
            raise HTTPException(400, "Ação inválida.")
        qld.gravar(c)
        return {"ok": True, "status": v.get("status")}


@painel_api.put("/cargas/{numero}/placa")
def rota_trocar_placa_rk(numero: str, payload: qld.TrocaPlaca, request: Request):
    rk_exigir(request, ["aovivo"], 2, "Trocar a placa da carga")
    return qld.trocar_placa(numero, payload, qld.quem(request))


class Decisao(BaseModel):
    acao: str                     # liberar | negar
    nota: Optional[str] = ""


@painel_api.put("/cargas/{numero}/liberacao/{pv}")
def rota_decidir(numero: str, pv: str, payload: Decisao, request: Request):
    rk_exigir(request, ["aovivo"], 2, "Liberar entrega sem palavra")
    q = qld.quem(request)
    if payload.acao not in ("liberar", "negar"):
        raise HTTPException(400, "Ação inválida.")
    with TRAVA:
        c = qld.carga(numero)
        qld.etapa_pessoa(c, "pcl", q)   # cada etapa por uma pessoa (o admin faz todas)
        p = _ped_da_carga(c, pv)
        ev = rua(c)["pedidos"].get(chave_pv(p.get("pv"))) or {}
        lib = ev.get("liberacao")
        if ev.get("desfecho"):
            raise HTTPException(409, "Este pedido já tem registro do motorista.")
        if not lib or lib.get("status") != "pendente":
            if payload.acao != "liberar":
                raise HTTPException(409, "Não há pedido de liberação aberto para este pedido.")
            # [v5.7] o PCL libera antes do motorista pedir (ex.: cliente avisou que não recebeu a palavra)
            lib = rua(c)["pedidos"].setdefault(chave_pv(p.get("pv")), {})["liberacao"] = {
                "status": "pendente", "pedido_em": agora(), "pedido_por": f"PCL ({q['por']})", "motivo": "liberado direto pelo PCL"}
        lib.update(status="liberada" if payload.acao == "liberar" else "negada", decidido_por=q["por"], decidido_email=q["email"],
                   decidido_em=agora(), nota=str(payload.nota or "")[:300])
        qld.hist(c, q, f"entrega sem palavra {'LIBERADA' if payload.acao == 'liberar' else 'negada'}", f"PV {p.get('pv')} · {payload.nota or ''}")
        qld.gravar(c)
        return _ev_pub(c, p, True)


class Baixa(BaseModel):
    feito: bool = True
    nota: Optional[str] = ""


@painel_api.put("/cargas/{numero}/baixa-erp/{pv}")
def rota_baixa_erp(numero: str, pv: str, payload: Baixa, request: Request):
    """O PCL lançou a baixa da entrega no Protheus (manual): fica marcado aqui."""
    rk_exigir(request, ["aovivo"], 2, "Marcar baixa no ERP")
    q = qld.quem(request)
    with TRAVA:
        c = qld.carga(numero)
        qld.etapa_pessoa(c, "pcl", q)   # cada etapa por uma pessoa (o admin faz todas)
        p = _ped_da_carga(c, pv)
        ev = rua(c)["pedidos"].get(chave_pv(p.get("pv")))
        if not ev or ev.get("desfecho") not in ("entregue", "parcial"):
            raise HTTPException(409, "Só pedido entregue (ou parcial) recebe baixa de entrega.")
        ev["baixa_erp"] = {"em": agora(), "por": q["por"], "email": q["email"], "nota": str(payload.nota or "")[:200]} if payload.feito else None
        qld.hist(c, q, "baixa da entrega no ERP" if payload.feito else "baixa no ERP desmarcada", f"PV {p.get('pv')}")
        qld.gravar(c)
        return _ev_pub(c, p, True)


class Correcao(BaseModel):
    nota: str


@painel_api.post("/cargas/{numero}/pedido/{pv}/reabrir")
def rota_pcl_reabrir(numero: str, pv: str, payload: Correcao, request: Request):
    """PCL apaga o registro do motorista (ex.: tocou errado) para ele registrar de novo."""
    rk_exigir(request, ["aovivo"], 3, "Reabrir registro do motorista")
    q = qld.quem(request)
    if not str(payload.nota or "").strip():
        raise HTTPException(400, "Diga o motivo.")
    with TRAVA:
        c = qld.carga(numero)
        qld.etapa_pessoa(c, "pcl", q)   # cada etapa por uma pessoa (o admin faz todas)
        p = _ped_da_carga(c, pv)
        ev = rua(c)["pedidos"].get(chave_pv(p.get("pv"))) or {}
        if not ev.get("desfecho"):
            raise HTTPException(404, "Não há registro para reabrir.")
        ant = {k: ev.get(k) for k in ("desfecho", "motivo", "em", "por")}
        _tirar_retorno_do_app(c, p)
        for k in ("cid", "desfecho", "motivo", "linhas", "obs", "foto", "em", "mid", "por", "lat", "lng", "palavra_ok", "baixa_erp"):
            ev.pop(k, None)
        ev.setdefault("corrigidos", []).append({**ant, "desfeito_em": agora(), "pelo_pcl": q["por"], "nota": payload.nota[:300]})
        qld.hist(c, q, "registro do motorista reaberto", f"PV {p.get('pv')} · era {ant['desfecho']} · {payload.nota}")
        qld.gravar(c)
        return _ev_pub(c, p, True)


@painel_api.get("/foto/{fid}")
def rota_foto(fid: str, request: Request):
    rk_exigir(request, ["aovivo", "historico"], 1, "Ver foto")
    return _foto(fid)


# ══════════════════════════════════════════════════════════════════════════
# [v5.9] HISTÓRICO DE ENTREGAS — todos os clientes do período, o que aconteceu e as conversas
# ══════════════════════════════════════════════════════════════════════════
@painel_api.get("/historico")
def rota_historico(request: Request, de: str = "", ate: str = "", busca: str = ""):
    rk_exigir(request, ["historico"], 1, "Ver o histórico de entregas")
    ate = qld.iso_ok(ate, qld.hoje())
    de = qld.iso_ok(de, (datetime.fromisoformat(ate) - timedelta(days=6)).strftime("%Y-%m-%d"))
    termo = norm(busca)
    out = []
    with TRAVA:
        for c in qld.no_periodo(de, ate):
            if c.get("avulsa"):
                continue
            r = c.get("rua") or {}
            mot = (r.get("saida") or {}).get("nome") or c.get("motorista") or ""
            for p in c.get("pedidos") or []:
                if termo and termo not in norm(" ".join(str(x or "") for x in (c["numero"], c.get("placa"), mot, p.get("pv"), p.get("nome"),
                                                                                 p.get("cliente"), p.get("nf"), p.get("bairro"), p.get("cidade")))):
                    continue
                ev = (r.get("pedidos") or {}).get(chave_pv(p.get("pv"))) or {}
                ch = (r.get("chats") or {}).get(chave_pv(p.get("pv"))) or {}
                out.append({"carga": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "motorista": mot,
                            "pv": p.get("pv"), "nome": p.get("nome"), "cliente": p.get("cliente"), "nf": p.get("nf"),
                            "bairro": p.get("bairro"), "cidade": p.get("cidade"), "desfecho": ev.get("desfecho") or "", "motivo": ev.get("motivo") or "",
                            "em": ev.get("em") or "", "por": ev.get("por") or "", "foto": bool(ev.get("foto")), "palavra_ok": ev.get("palavra_ok"),
                            "chats": len(ch.get("atendimentos") or []), "chat_aberto": bool(ch.get("atual")),
                            "motivos_chat": [a.get("motivo") for a in ch.get("atendimentos") or []], "baixa_erp": bool(ev.get("baixa_erp")),
                            "status_carga": c.get("status"), "cotacao": qld.cotacao(p)})
    out.sort(key=lambda x: (x["data_iso"] or "", x["carga"], x["em"] or ""), reverse=True)
    return {"de": de, "ate": ate, "total": len(out), "itens": out[:2000]}


@painel_api.get("/palavras")
def rota_palavras(request: Request, data: str = ""):
    """Palavra de cada pedido das cargas do dia — para o ERP do Desenvolvimento (via ponte) avisar o cliente."""
    rk_exigir(request, ["aovivo"], 1, "Ver palavras da entrega")
    dia = qld.iso_ok(data, qld.hoje())
    out = []
    with TRAVA:
        for c in qld.no_periodo(dia, dia) + [x for x in qld.CARGAS.values() if x.get("status") in STATUS_RUA and not (x.get("rua") or {}).get("chegada")]:
            if garantir_palavras(c):
                qld.gravar(c)
            mids = {m for m, v in app_cfg()["motoristas"].items() if norm_placa(c.get("placa")) in (v.get("placas") or [])}
            for p in c.get("pedidos") or []:
                out.append({"carga": c["numero"], "data_iso": c.get("data_iso"), "placa": c.get("placa"), "pv": p.get("pv"), "cliente": p.get("cliente"),
                            "nome": p.get("nome"), "bairro": p.get("bairro"), "palavra": rua(c)["palavras"].get(chave_pv(p.get("pv")), ""),
                            "exigida": any(palavra_ligada(m) for m in mids)})
    vistos, unicos = set(), []
    for x in out:
        if (x["carga"], x["pv"]) not in vistos:
            vistos.add((x["carga"], x["pv"]))
            unicos.append(x)
    return unicos


# ── Motoristas e app ──────────────────────────────────────────────────────
@painel_api.get("/motoristas")
def rota_motoristas(request: Request):
    rk_exigir(request, ["motoristas", "aovivo"], 1, "Ver motoristas")
    with TRAVA:
        if semear_motoristas():
            app_salvar()
        A = app_cfg()
        bloq = {b["valor"]: b["ate"] for b in seg.bloqueios_ativos() if b["chave"].startswith("placa:")}
        placas = []
        for pl, v in sorted(placas_conhecidas().items()):
            reg = A["placas"].get(pl) or {}
            placas.append({"placa": pl, "cadastro": v.get("motorista") or "", "acesso": "sem" if not reg else ("inicial" if reg.get("trocar") else "propria"),
                           "ultimo_acesso": reg.get("ultimo_acesso") or "", "bloqueada": bool(bloq.get(f"placa:{pl}") or bloq.get(pl))})
        mots = sorted([{"id": k, **{x: m.get(x) for x in ("nome", "placas", "palavra", "ativo", "em", "por")}} for k, m in A["motoristas"].items()],
                      key=lambda m: (not m.get("ativo", True), m["nome"]))
        return {"motoristas": mots, "placas": placas, "cfg": A["cfg"], "app_url": "/motorista/",
                "pode_editar": rk_nivel(request, "motoristas") >= 3}


class MotoristaIn(BaseModel):
    nome: str
    placas: list = []
    palavra: Optional[bool] = None
    ativo: Optional[bool] = None


def _limpa_placas(lst) -> list:
    return sorted({norm_placa(p) for p in lst or [] if len(norm_placa(p)) >= 5})


@painel_api.post("/motoristas")
def rota_motorista_novo(payload: MotoristaIn, request: Request):
    rk_exigir(request, "motoristas", 3, "Cadastrar motorista")
    q = qld.quem(request)
    nome = re.sub(r"\s+", " ", payload.nome).strip()[:120]
    if len(nome) < 3:
        raise HTTPException(400, "Escreva o nome do motorista.")
    with TRAVA:
        A = app_cfg()
        if any(norm(m.get("nome")) == norm(nome) for m in A["motoristas"].values()):
            raise HTTPException(409, "Já existe um motorista com esse nome.")
        mid = "m" + uuid.uuid4().hex[:8]
        A["motoristas"][mid] = {"nome": nome, "placas": _limpa_placas(payload.placas), "palavra": bool(payload.palavra), "ativo": True,
                                "em": agora(), "por": q["por"]}
        app_salvar()
        return {"id": mid, **A["motoristas"][mid]}


@painel_api.delete("/motoristas/{mid}")
def rota_motorista_excluir(mid: str, request: Request):
    """[v5.6] Exclui o motorista (o celular dele sai). Só volta se a placa aparecer de novo num PDF importado."""
    rk_exigir(request, "motoristas", 4, "Excluir motorista")
    q = qld.quem(request)
    with TRAVA:
        A = app_cfg()
        m = A["motoristas"].pop(mid, None)
        if not m:
            raise HTTPException(404, "Motorista não encontrado.")
        exc = A.setdefault("excluidos", {})
        for pl in m.get("placas") or []:
            exc[f"{norm(m.get('nome'))}|{pl}"] = {"nome": m.get("nome"), "placa": pl, "em": agora(), "por": q["por"]}
        app_salvar()
        return {"ok": True, "excluido": m.get("nome"), "placas": m.get("placas") or []}


@painel_api.put("/motoristas/{mid}")
def rota_motorista_editar(mid: str, payload: MotoristaIn, request: Request):
    rk_exigir(request, "motoristas", 3, "Editar motorista")
    q = qld.quem(request)
    with TRAVA:
        A = app_cfg()
        m = A["motoristas"].get(mid)
        if not m:
            raise HTTPException(404, "Motorista não encontrado.")
        antes = bool(m.get("palavra"))
        nome = re.sub(r"\s+", " ", payload.nome).strip()[:120]
        if len(nome) >= 3:
            m["nome"] = nome
        m["placas"] = _limpa_placas(payload.placas)
        if payload.palavra is not None:
            m["palavra"] = bool(payload.palavra)
        if payload.ativo is not None:
            m["ativo"] = bool(payload.ativo)
        m.setdefault("historico", []).append({"em": agora(), "por": q["por"], "palavra": m.get("palavra"), "ativo": m.get("ativo"),
                                             "placas": m["placas"]})
        m["historico"] = m["historico"][-50:]
        if antes != bool(m.get("palavra")):
            m["palavra_mudou_em"], m["palavra_mudou_por"] = agora(), q["por"]
        app_salvar()
        return {"id": mid, **{k: v for k, v in m.items() if k != "historico"}}


@painel_api.post("/placas/{placa}/resetar")
def rota_resetar(placa: str, request: Request):
    """Senha da placa volta para a inicial (troca obrigatória no próximo acesso) e derruba quem estiver logado."""
    rk_exigir(request, "motoristas", 3, "Resetar senha da placa")
    q = qld.quem(request)
    pl = norm_placa(placa)
    with TRAVA:
        A = app_cfg()
        reg = A["placas"].get(pl) or {}
        A["placas"][pl] = {**reg, "hash": seg.gerar_hash(SENHA_INICIAL), "trocar": True, "versao": int(reg.get("versao", 0)) + 1,
                           "reset_em": agora(), "reset_por": q["por"]}
        app_salvar()
    seg.limpar_falhas(f"placa:{pl}")
    return {"ok": True, "placa": pl}


class CfgIn(BaseModel):
    tentativas: Optional[int] = None
    chat_minutos: Optional[int] = None
    cotacao: Optional[dict] = None   # [v5.11] {"ufs_locais": ["SP"], "enderecos": [{"rua", "numero"}]}
    motivos_chat: Optional[list] = None


@painel_api.put("/cfg")
def rota_cfg(payload: CfgIn, request: Request):
    rk_exigir(request, "motoristas", 3, "Mudar a configuração do app")
    if payload.tentativas is not None and not 3 <= payload.tentativas <= 10:
        raise HTTPException(400, "Tentativas entre 3 e 10.")
    if payload.chat_minutos is not None and not 1 <= payload.chat_minutos <= 120:
        raise HTTPException(400, "Prazo do atendimento entre 1 e 120 minutos.")
    cot = None
    if payload.cotacao is not None:
        ufs = sorted({str(u).strip().upper()[:2] for u in payload.cotacao.get("ufs_locais") or [] if re.fullmatch(r"[A-Za-z]{2}", str(u).strip())})
        if not ufs:
            raise HTTPException(400, "Informe pelo menos uma UF de entrega direta (ex.: SP).")
        ends = []
        for e in payload.cotacao.get("enderecos") or []:
            rua = re.sub(r"\s+", " ", str((e or {}).get("rua") or "")).strip()[:120]
            num = re.sub(r"\D", "", str((e or {}).get("numero") or ""))[:8]
            if rua and qld.palavras_rua(rua):
                ends.append({"rua": rua.upper(), "numero": num})
        cot = {"ufs_locais": ufs, "enderecos": ends[:50]}
    mot = None
    if payload.motivos_chat is not None:
        mot = []
        for m in payload.motivos_chat:
            m = str(m or "").strip()[:60]
            if m and m.lower() not in [x.lower() for x in mot]:
                mot.append(m)
        if not mot:
            raise HTTPException(400, "Deixe pelo menos um motivo para o chat.")
    with TRAVA:
        if payload.tentativas is not None:
            app_cfg()["cfg"]["tentativas"] = int(payload.tentativas)
        if mot is not None:
            app_cfg()["cfg"]["motivos_chat"] = mot[:20]
        if payload.chat_minutos is not None:
            app_cfg()["cfg"]["chat_minutos"] = int(payload.chat_minutos)
        if cot is not None:
            app_cfg()["cfg"]["cotacao"] = cot
        app_salvar()
        return app_cfg()["cfg"]
