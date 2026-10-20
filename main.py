import os
import re
import json
import urllib.request
import urllib.error
import threading
import traceback
from contextlib import asynccontextmanager
from datetime import date, datetime
import time

# [v4.18] HORÁRIO DE BRASÍLIA em todo o servidor: o Render roda em UTC; com isto, todo
# datetime.now() (registros, históricos, vencimentos) já sai no horário de Brasília.
os.environ["TZ"] = "America/Sao_Paulo"
try:
    time.tzset()
except AttributeError:   # Windows (desenvolvimento local) não tem tzset — lá o relógio já é o local
    pass
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator
from typing import Optional, List, Any

import db_diario as db
import seguranca as seg
import modulos as mods
import permissoes as perm
import db_seguranca as dbs   # [v4.2] Configurações, Sessões ativas e pedidos de senha
import hmac                  # [v4.2] comparar a senha do administrador de fábrica

# ──────────────────────────────────────────────────────────────────────────
# [NOVO v4.2] ADMINISTRADOR DE FÁBRICA — para nunca ficar trancado de fora.
# Render → Environment:  ADMIN_FABRICA_EMAIL = seu e-mail (pode ser um que não existe no cadastro)
#                         ADMIN_FABRICA_SENHA = uma senha forte
# Ele NÃO fica gravado no banco: vale enquanto as duas variáveis existirem.
# Apagou as variáveis → ele deixa de entrar na hora (as sessões dele caem).
# ──────────────────────────────────────────────────────────────────────────
FAB_EMAIL = (os.environ.get("ADMIN_FABRICA_EMAIL") or "").strip().lower()
FAB_SENHA = os.environ.get("ADMIN_FABRICA_SENHA") or ""


def _usuario_fabrica() -> dict:
    return {"email": FAB_EMAIL, "nome": "Administrador de fábrica", "papel": "admin", "ativo": True,
            "fabrica": True, "versao_sessao": 0, "modulos": [], "perfil": "", "departamentos": []}


# [NOVO v4.2] CONFIGURAÇÕES DA SEGURANÇA (aba Configurações) — cache de 30 s
_cache_cfg: dict = {"t": 0.0, "dados": None}


def _cfg() -> dict:
    if _cache_cfg["dados"] is not None and time.time() - _cache_cfg["t"] < 30:
        return _cache_cfg["dados"]
    try:
        dados = dbs.config_ler()
    except Exception as e:
        print(f"[AVISO] não consegui ler as configurações da segurança: {e!r}")
        dados = _cache_cfg["dados"] or dict(dbs.CONFIG_PADRAO)
    seg.configurar(dados)
    _cache_cfg.update(t=time.time(), dados=dados)
    return dados


def _garantir_admin():
    """[NOVO v3.0] Cria o primeiro administrador a partir das variáveis
    ADMIN_EMAIL e ADMIN_SENHA do Render — SÓ se esse e-mail ainda não
    existir. Nunca sobrescreve a senha de quem já existe (depois do
    primeiro login, a senha passa a ser a que você escolher)."""
    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    senha = os.environ.get("ADMIN_SENHA") or ""
    if not email or not senha:
        print("[AVISO] ADMIN_EMAIL/ADMIN_SENHA não configurados — nenhum admin inicial criado.")
        return
    if db.usuario_ler(email):
        return
    db.usuario_criar(email, {
        "email": email, "nome": os.environ.get("ADMIN_NOME", "Administrador"), "papel": "admin",
        "modulos": [], "ativo": True, "senha_hash": seg.gerar_hash(senha), "versao_sessao": 1,
        "trocar_senha": True, "criado_em": datetime.now().isoformat(), "criado_por": "sistema",
    })
    print(f"[OK] Administrador inicial criado: {email}")


def _init_em_segundo_plano():
    try:
        db.init_dados_exemplo()
        print("[OK] Firestore conectado e dados de exemplo verificados.")
    except Exception as e:
        print(f"[AVISO] init_dados_exemplo falhou: {e!r}")
    try:
        _garantir_admin()
    except Exception as e:
        print(f"[AVISO] não consegui verificar o admin inicial: {e!r}")
    try:
        n = db.perfis_semear(mods.PERFIS_PADRAO)
        if n:
            print(f"[OK] {n} perfis de acesso padrão criados.")
    except Exception as e:
        print(f"[AVISO] não consegui verificar os perfis de acesso: {e!r}")
    try:
        _cfg()   # [v4.2] aplica limites de tentativas, sessão e regras de senha
    except Exception as e:
        print(f"[AVISO] configurações da segurança: {e!r}")
    if seg.SEGREDO_TEMPORARIO:
        print("[AVISO] SESSION_SECRET não configurado — todos serão deslogados a cada reinício do servidor.")


@asynccontextmanager
async def lifespan(_app):
    # [AJUSTADO v2.2] A conexão com o Firestore agora só é criada DEPOIS que
    # o servidor já subiu (e numa thread separada, pra não atrasar o health
    # check). Antes ela era criada no import do arquivo — e uma conexão gRPC
    # criada antes do servidor iniciar pode ficar travada para sempre.
    threading.Thread(target=_init_em_segundo_plano, daemon=True).start()
    yield


app = FastAPI(title="PQI — Painel de Sistemas", version="3.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# [REMOVIDO v3.0] O CORS liberado pra qualquer site ("*") saiu: com login
# por cookie, ele deixaria outro site fazer pedidos em nome de quem está
# logado. Tudo aqui é do mesmo endereço, então o CORS não faz falta.


# ══════════════════════════════════════════════════════════════════════════
# [NOVO v3.0] PORTEIRO — toda requisição passa por aqui antes de chegar na
# rota. Regra: sem login, nada abre (só a tela inicial e o próprio login);
# com login, cada página/API só abre se o módulo dela estiver liberado.
# Endereço que não pertence a módulo nenhum é negado por padrão.
# ══════════════════════════════════════════════════════════════════════════
ROTAS_PUBLICAS = {"/", "/healthz", "/_stcore/health", "/favicon.ico",
                  "/api/auth/login", "/api/auth/logout", "/api/auth/me",
                  "/api/auth/esqueci", "/api/auth/config-login"}          # [v4.2] Esqueci minha senha
ROTAS_PUBLICAS_PREFIXO = ("/api/auth/esqueci/",)                     # [v4.2] consulta/conclusão do pedido
ROTAS_SO_ADMIN_PREFIXO = ("/api/admin/",)
ROTAS_SO_ADMIN = {"/admin", "/api/diag"}
ROTAS_QUALQUER_LOGADO = {"/api/auth/senha", "/pqi-acesso.js", "/api/base/cadastros"}  # [v3.4] cadastro mestre

_cache_usuarios: dict = {}
_CACHE_SEG = 300   # [v4.18] 5 min (era 30 s): toda mudança feita por aqui já limpa o cache na hora; economiza leituras do Firestore


def _usuario_cacheado(email: str) -> Optional[dict]:
    agora = time.time()
    item = _cache_usuarios.get(email)
    if item and agora - item[1] < _CACHE_SEG:
        return item[0]
    u = db.usuario_ler(email)
    _cache_usuarios[email] = (u, agora)
    return u


def _esquecer_cache(email: str) -> None:
    _cache_usuarios.pop(email, None)


# [NOVO v3.3] PERFIS DE ACESSO — guardados em cache por 30 s, como os usuários
_cache_perfis: dict = {"t": 0.0, "dados": {}}


def _perfis() -> dict:
    if time.time() - _cache_perfis["t"] < _CACHE_SEG:
        return _cache_perfis["dados"]
    dados = {p["id"]: p for p in db.perfis_listar()}
    _cache_perfis.update(t=time.time(), dados=dados)
    return dados


def _esquecer_perfis() -> None:
    _cache_perfis["t"] = 0.0


def _perfil_do(u: dict) -> Optional[dict]:
    pid = u.get("perfil")
    return _perfis().get(pid) if pid else None


def _niveis(u: dict) -> dict:
    """Nível (0–3) da pessoa em cada sistema — ver permissoes.py.
    [v3.9] já inclui as exceções individuais (u["acessos"]) — ver modulos.py."""
    return mods.niveis_do_usuario(u, _perfil_do(u), _perfis())


_SETORES_OK = {"lido": False}


def _abas_setores(dados) -> None:
    """[v4.22] Cada SETOR da Carteira com motivo ativo vira uma aba (permissão por aba em Segurança)."""
    try:
        d = dados or {}
        ativos = {x.get("setor") for x in (d.get("motivos") or []) if x.get("ativo", True) is not False}
        mods.ABAS_EXTRA["carteira"] = [{"id": str(s.get("id")), "nome": str(s.get("nome") or s.get("id"))}
                                       for s in (d.get("setores") or []) if s.get("id") in ativos and re.fullmatch(r"[0-9A-Za-z_]{1,30}", str(s.get("id")))]
        _SETORES_OK["lido"] = True
    except Exception:
        pass


def _garantir_abas_setores() -> None:
    if _SETORES_OK["lido"]:
        return
    _SETORES_OK["lido"] = True
    try:
        lst = db.docs_listar(db.COLECAO_CART_CONFIG)
        _abas_setores((lst[0] or {}).get("dados") if lst else None)
    except Exception:
        _SETORES_OK["lido"] = False


def _abas(u: dict, niveis: Optional[dict] = None) -> dict:
    """[v4.16] Nível (0–4) de cada ABA de cada sistema — ver modulos.abas_do_usuario."""
    _garantir_abas_setores()
    return mods.abas_do_usuario(u, _perfil_do(u), niveis if niveis is not None else _niveis(u))


def _nivel_aba(request: Request, modulo_id: str, aba: str) -> int:
    return int(_abas(usuario_atual(request)).get(modulo_id, {}).get(aba, 0))


def _exigir_aba(request: Request, modulo_id: str, abas, minimo: int, acao: str) -> None:
    """[v4.16] Confere no servidor a permissão da aba (basta uma das abas listadas ter o nível)."""
    abas = [abas] if isinstance(abas, str) else list(abas)
    mapa = _abas(usuario_atual(request)).get(modulo_id, {})
    if any(int(mapa.get(a, 0)) >= minimo for a in abas):
        return
    nomes = {a["id"]: a["nome"] for a in mods.abas_de(modulo_id)}
    raise HTTPException(status_code=403, detail=f"{acao} exige “{mods.NIVEIS_ABA.get(minimo)}” na aba {' ou '.join('“'+nomes.get(a, a)+'”' for a in abas)}. Peça ao administrador.")


def _nome_perfil(u: dict) -> str:
    if u.get("papel") == "admin":
        return "Administrador"
    p = _perfil_do(u)
    return p.get("nome") if p else "Personalizado (módulos marcados)"


def _deps_permitidos(u: dict):
    """None = todos os departamentos; senão, o conjunto de ids liberados."""
    if u.get("papel") == "admin" or not u.get("departamentos"):
        return None
    return set(u.get("departamentos") or [])


def _dep_ok(u: dict, dep: str) -> bool:
    s = _deps_permitidos(u)
    return s is None or dep in s


_cache_sessoes: dict = {}   # [v4.2] sid → (sessão, quando leu)
_uso_gravado: dict = {}     # [v4.2] sid → quando gravou o "último uso" (grava no máx. a cada 5 min)


def _esquecer_sessao(sid: str) -> None:
    _cache_sessoes.pop(sid, None)


def _sessao_ok(sid: str, email: str, request: Request) -> bool:
    """[v4.2] A sessão listada em Sessões ativas ainda vale? (derrubada pelo ADM = não)."""
    agora = time.time()
    item = _cache_sessoes.get(sid)
    if item and agora - item[1] < _CACHE_SEG:
        s = item[0]
    else:
        s = dbs.sessao_ler(sid)
        _cache_sessoes[sid] = (s, agora)
    if not s or s.get("revogada") or (s.get("email") or "") != email:
        return False
    if agora - _uso_gravado.get(sid, 0) > 300:
        _uso_gravado[sid] = agora
        try:
            dbs.sessao_atualizar(sid, {"ultimo_uso": datetime.now().isoformat(), "ip": _ip(request)})
        except Exception:
            pass
    return True


def _usuario_da_requisicao(request: Request) -> Optional[dict]:
    dados = seg.ler_token(request.cookies.get(seg.NOME_COOKIE))
    if not dados:
        return None
    email = dados.get("e", "")
    if dados.get("f"):   # [v4.2] administrador de fábrica: só enquanto as variáveis do Render existirem
        if not (FAB_EMAIL and FAB_SENHA and email == FAB_EMAIL):
            return None
        u = _usuario_fabrica()
    else:
        u = _usuario_cacheado(email)
        if not u or not u.get("ativo", True) or int(u.get("versao_sessao", 1)) != int(dados.get("v", -1)):
            return None
    sid = dados.get("s")
    if sid and not _sessao_ok(sid, email, request):
        return None
    if sid:
        request.state.sid = sid
    return u


def _negar(eh_api: bool, status: int, mensagem: str):
    if eh_api:
        return JSONResponse({"detail": mensagem}, status_code=status)
    # Página dentro do túnel: mostra o aviso e manda o login abrir na janela
    # principal (target=_top), não dentro do iframe.
    return HTMLResponse(
        f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Acesso</title>
<style>body{{font:15px Inter,Arial,sans-serif;background:#06070b;color:#f2f0ea;display:grid;place-items:center;
height:100vh;margin:0}}div{{text-align:center;max-width:420px;padding:24px}}a{{color:#f3cd6f}}</style></head>
<body><div><p style="font-size:36px;margin:0">🔒</p><p>{mensagem}</p>
<p><a href="/" target="_top">Ir para o Painel</a></p></div></body></html>""",
        status_code=status,
    )


@app.middleware("http")
async def porteiro(request: Request, call_next):
    caminho = request.url.path
    if caminho in ROTAS_PUBLICAS or caminho.startswith(ROTAS_PUBLICAS_PREFIXO):
        return await call_next(request)

    eh_api = caminho.startswith("/api/")
    try:
        usuario = await run_in_threadpool(_usuario_da_requisicao, request)
    except Exception as e:
        return _negar(eh_api, 503, f"Não consegui verificar seu acesso agora ({e.__class__.__name__}). Tente de novo.")
    if not usuario:
        return _negar(eh_api, 401, "Sua sessão expirou ou você ainda não entrou. Faça login de novo.")
    request.state.usuario = usuario

    if caminho in ROTAS_QUALQUER_LOGADO:
        return await call_next(request)
    if caminho in ROTAS_SO_ADMIN or caminho.startswith(ROTAS_SO_ADMIN_PREFIXO):
        if usuario.get("papel") != "admin":
            return _negar(eh_api, 403, "Esta área é só para administradores.")
        return await call_next(request)

    modulo = mods.modulo_da_rota(caminho)
    if modulo is None:
        return _negar(eh_api, 404, "Endereço não encontrado.")
    # [v3.3] nível do perfil: ler = Visualizar · gravar = Adicionar (o resto
    # da regra do Adicionar é conferido na rota) · excluir = Editar
    try:
        niveis = await run_in_threadpool(_niveis, usuario)
    except Exception as e:
        return _negar(eh_api, 503, f"Não consegui verificar suas permissões agora ({e.__class__.__name__}).")
    try:   # [v4.16] uma aba com nível maior que o sistema também libera gravar (a rota confere a aba)
        nivel = mods.nivel_efetivo_sistema(niveis.get(modulo["id"], 0), (await run_in_threadpool(_abas, usuario, niveis)).get(modulo["id"]))
    except Exception:
        nivel = niveis.get(modulo["id"], 0)
    if nivel < perm.VISUALIZAR:
        return _negar(eh_api, 403, f"Você não tem acesso ao módulo “{modulo['nome']}”. Peça liberação ao administrador.")
    metodo = request.method.upper()
    precisa = perm.VISUALIZAR if metodo in ("GET", "HEAD", "OPTIONS") else (perm.EDITAR if metodo == "DELETE" else perm.ADICIONAR)
    if nivel < precisa:
        if nivel == perm.VISUALIZAR:
            return _negar(eh_api, 403, f"Seu perfil em “{modulo['nome']}” é “Visualizar”: você pode consultar, mas não gravar.")
        return _negar(eh_api, 403, "Excluir é só para quem tem perfil “Editar” neste sistema.")
    request.state.niveis = niveis
    return await call_next(request)


def usuario_atual(request: Request) -> dict:
    u = getattr(request.state, "usuario", None) or _usuario_da_requisicao(request)
    if not u:
        raise HTTPException(status_code=401, detail="Faça login de novo.")
    return u


def _usuario_publico(u: dict) -> dict:
    return {k: u.get(k) for k in ("email", "nome", "papel", "modulos", "ativo", "trocar_senha",
                                   "criado_em", "criado_por", "ultimo_login", "perfil", "departamentos",
                                   "acessos", "acessos_info", "fabrica", "abas")}


def _sessao_publica(u: dict) -> dict:
    niveis = _niveis(u)
    abas = _abas(u, niveis)
    efetivos = {k: mods.nivel_efetivo_sistema(v, abas.get(k)) for k, v in niveis.items()}
    return {"usuario": _usuario_publico(u), "modulos": mods.cards_do_usuario(u, efetivos), "permissoes": niveis,
            "perfil_nome": _nome_perfil(u), "so_acesso": sorted(mods.IDS_SO_ACESSO),
            "abas": abas, "niveis_aba": mods.NIVEIS_ABA}   # [v4.16] permissão por aba


def _email(request: Request) -> str:
    return usuario_atual(request)["email"]


def _exigir_editar_ou_dono(request: Request, modulo_id: str, dono_email: Optional[str]) -> None:
    """Alterar um registro: perfil Editar, ou perfil Adicionar sendo o autor dele."""
    u = usuario_atual(request)
    n = _niveis(u).get(modulo_id, 0)
    if n >= perm.EDITAR or (n == perm.ADICIONAR and dono_email and dono_email == u["email"]):
        return
    raise HTTPException(status_code=403, detail=perm.MENSAGEM_ADICIONAR)


def _conferir_acrescimo(request: Request, modulo_id: str, colecao: str, doc_id: str, dados):
    """Perfil Adicionar gravando um documento inteiro (Mapa, Organograma): só passa
    se não mexeu no que outra pessoa registrou (ou se o documento é dele)."""
    u = usuario_atual(request)
    if _niveis(u).get(modulo_id, 0) >= perm.EDITAR:
        return dados
    atual = db.docs_ler(colecao, doc_id)
    if not atual or atual.get("criado_por_email") == u["email"]:
        return dados
    try:
        return perm.so_acrescimos(atual.get("dados"), dados, u["email"])
    except perm.Recusado as e:
        raise HTTPException(status_code=403, detail=f"{perm.MENSAGEM_ADICIONAR} ({e})")


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _gravar_cookie(resposta: Response, u: dict, duracao: Optional[int] = None, sid: Optional[str] = None) -> None:
    duracao = int(duracao or seg.DURACAO_SESSAO_SEG)
    resposta.set_cookie(
        seg.NOME_COOKIE, seg.criar_token(u["email"], int(u.get("versao_sessao", 1)), duracao, sid, bool(u.get("fabrica"))),
        max_age=duracao, httponly=True, secure=True, samesite="lax", path="/",
    )


def _abrir_sessao(request: Request, resposta: Response, u: dict, lembrar: bool = False) -> None:
    """[v4.2] Cria a sessão (aparece em Sessões ativas) e grava o cookie.
    Lembrar de mim → dura os dias de Configurações; senão, as horas de Configurações."""
    cfg = _cfg()
    lembrar = bool(lembrar and cfg.get("lembrar_ativo", True))
    duracao = int(cfg.get("lembrar_dias", 30)) * 86400 if lembrar else seg.DURACAO_SESSAO_SEG
    sid = seg.novo_id()
    agora = datetime.now()
    try:
        dbs.sessao_criar(sid, {
            "email": u["email"], "nome": u.get("nome") or u["email"], "criado_em": agora.isoformat(),
            "ultimo_uso": agora.isoformat(), "expira_em": datetime.fromtimestamp(time.time() + duracao).isoformat(),
            "ip": _ip(request), "navegador": (request.headers.get("user-agent") or "")[:220],
            "lembrar": lembrar, "fabrica": bool(u.get("fabrica")),
        })
        _uso_gravado[sid] = time.time()
    except Exception as e:   # sem o banco, entra assim mesmo (só não aparece em Sessões ativas)
        print(f"[AVISO] não consegui registrar a sessão: {e!r}")
        sid = None
    _gravar_cookie(resposta, u, duracao, sid)


# ──────────────────────────────────────────────────────────────────────────
# LOGIN / SESSÃO
# ──────────────────────────────────────────────────────────────────────────
_EMAIL_VALIDO = re.compile(r"^[^@\s/]+@[^@\s/]+\.[^@\s/]+$")


class LoginEntrada(BaseModel):
    email: str
    senha: str
    lembrar: bool = False   # [v4.2] Lembrar de mim


class TrocaSenha(BaseModel):
    senha_atual: str
    senha_nova: str


@app.post("/api/auth/login")
def auth_login(payload: LoginEntrada, request: Request, response: Response):
    email = payload.email.strip().lower()
    chave_email, chave_ip = f"email:{email}", f"ip:{_ip(request)}"
    for chave in (chave_email, chave_ip):
        ate = seg.bloqueado_ate(chave)
        if ate:
            minutos = max(1, round((ate - time.time()) / 60))
            raise HTTPException(status_code=429, detail=f"Muitas tentativas erradas. Tente de novo em {minutos} min.")

    _cfg()   # [v4.2] garante os limites de Configurações
    # [v4.2] administrador de fábrica (variáveis do Render) — nunca fica gravado no banco
    if FAB_EMAIL and FAB_SENHA and email == FAB_EMAIL and hmac.compare_digest(payload.senha.encode(), FAB_SENHA.encode()):
        seg.limpar_falhas(chave_email)
        u = _usuario_fabrica()
        _abrir_sessao(request, response, u, payload.lembrar)
        return _sessao_publica(u)

    u = db.usuario_ler(email) if _EMAIL_VALIDO.match(email) else None
    senha_ok = seg.conferir_senha(payload.senha, (u or {}).get("senha_hash"))
    if not u or not senha_ok or not u.get("ativo", True):
        bloqueou = seg.registrar_falha(chave_email, "email")
        seg.registrar_falha(chave_ip, "ip")
        if bloqueou and u:
            _esquecer_avisos()
            try:
                db.log_permissao({"por": email, "por_nome": u.get("nome") or email, "tipo": "senha", "alvo": email,
                                  "descricao": f"Conta bloqueada por {seg.LIMITES['email'][0]} senhas erradas seguidas (IP {_ip(request)})"})
            except Exception:
                pass
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")

    seg.limpar_falhas(chave_email)
    db.usuario_atualizar(email, {"ultimo_login": datetime.now().isoformat()})
    _esquecer_cache(email)
    _abrir_sessao(request, response, u, payload.lembrar)
    return _sessao_publica(u)


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    dados = seg.ler_token(request.cookies.get(seg.NOME_COOKIE)) or {}
    if dados.get("s"):   # [v4.2] sai da lista de Sessões ativas
        try:
            dbs.sessao_atualizar(dados["s"], {"revogada": True, "revogada_por": "saiu", "revogada_em": datetime.now().isoformat()})
        except Exception:
            pass
        _esquecer_sessao(dados["s"])
    response.delete_cookie(seg.NOME_COOKIE, path="/")
    return {"sucesso": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    u = _usuario_da_requisicao(request)
    if not u:
        return JSONResponse({"detail": "não logado"}, status_code=401)
    return _sessao_publica(u)


@app.post("/api/auth/senha")
def auth_trocar_senha(payload: TrocaSenha, request: Request, response: Response):
    u = usuario_atual(request)
    if u.get("fabrica"):
        raise HTTPException(status_code=400, detail="A senha do administrador de fábrica é a variável ADMIN_FABRICA_SENHA do Render.")
    if not seg.conferir_senha(payload.senha_atual, u.get("senha_hash")):
        raise HTTPException(status_code=400, detail="A senha atual não confere.")
    erro = seg.senha_aceitavel(payload.senha_nova)
    if erro:
        raise HTTPException(status_code=400, detail=erro)
    if payload.senha_nova == payload.senha_atual:
        raise HTTPException(status_code=400, detail="A senha nova precisa ser diferente da atual.")
    nova_versao = int(u.get("versao_sessao", 1)) + 1  # desloga outras sessões abertas com a senha velha
    db.usuario_atualizar(u["email"], {"senha_hash": seg.gerar_hash(payload.senha_nova),
                                      "versao_sessao": nova_versao, "trocar_senha": False})
    _esquecer_cache(u["email"])
    _gravar_cookie(response, {**u, "versao_sessao": nova_versao}, None, getattr(request.state, "sid", None))
    return {"sucesso": True}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v4.2] ESQUECI MINHA SENHA — com liberação do administrador
#   1) a pessoa pede (tela de login) → o navegador dela guarda um CÓDIGO
#      secreto; o banco guarda só o hash dele (ninguém mais consegue usar);
#   2) o ADM vê o pedido (sino do Painel / Segurança → Liberações) e libera;
#   3) a tela da pessoa, que fica conferindo sozinha, abre o campo de senha
#      nova — vale só naquele navegador e só por alguns minutos.
# A resposta nunca diz se o e-mail existe (ninguém descobre quem tem cadastro).
# ──────────────────────────────────────────────────────────────────────────
class EsqueciEntrada(BaseModel):
    email: str


class EsqueciSenha(BaseModel):
    senha: str
    lembrar: bool = False


_esqueci_ip: dict = {}   # ip → [horários dos pedidos] (no máx. 5 por hora)


def _expirou(iso: Optional[str]) -> bool:
    return bool(iso) and str(iso) < datetime.now().isoformat()


def _situacao_pedido(p: dict) -> dict:
    """Atualiza pendente/liberado vencido para 'expirado' e devolve o pedido."""
    if (p.get("status") == "pendente" and _expirou(p.get("expira_em"))) or \
       (p.get("status") == "liberado" and _expirou(p.get("liberado_ate"))):
        p["status"] = "expirado"
        dbs.pedido_atualizar(p["id"], {"status": "expirado", "expirado_em": datetime.now().isoformat()})
    return p


@app.get("/api/auth/config-login")
def auth_config_login():
    cfg = _cfg()
    return {"esqueci": bool(cfg.get("esqueci_ativo", True)), "lembrar": bool(cfg.get("lembrar_ativo", True)),
            "lembrar_dias": int(cfg.get("lembrar_dias", 30)), "senha_minimo": int(cfg.get("senha_minimo", 8)),
            "senha_numero": bool(cfg.get("senha_numero")), "senha_letra": bool(cfg.get("senha_letra"))}


@app.post("/api/auth/esqueci")
def auth_esqueci(payload: EsqueciEntrada, request: Request):
    cfg = _cfg()
    if not cfg.get("esqueci_ativo", True):
        raise HTTPException(status_code=403, detail="O pedido de senha nova está desligado. Fale com o administrador.")
    ip, agora = _ip(request), time.time()
    hist = [t for t in _esqueci_ip.get(ip, []) if agora - t < 3600]
    if len(hist) >= 5:
        raise HTTPException(status_code=429, detail="Muitos pedidos deste computador. Tente de novo daqui a uma hora.")
    _esqueci_ip[ip] = hist + [agora]
    email = payload.email.strip().lower()
    if not _EMAIL_VALIDO.match(email):
        raise HTTPException(status_code=400, detail="Digite um e-mail válido.")
    codigo = seg.novo_id(24)
    validade = int(cfg.get("pedido_validade_horas", 24))
    u = db.usuario_ler(email) if email != FAB_EMAIL else None
    if u and u.get("ativo", True):
        for antigo in dbs.pedidos_do_email(email):   # só vale o pedido mais novo
            if antigo.get("status") in ("pendente", "liberado"):
                dbs.pedido_atualizar(antigo["id"], {"status": "substituido", "substituido_em": datetime.now().isoformat()})
        dbs.pedido_criar(seg.resumo(codigo), {
            "email": email, "nome": u.get("nome") or email, "status": "pendente",
            "criado_em": datetime.now().isoformat(),
            "expira_em": datetime.fromtimestamp(agora + validade * 3600).isoformat(),
            "ip": ip, "navegador": (request.headers.get("user-agent") or "")[:220],
        })
        _esquecer_avisos()
    # mesma resposta exista ou não o e-mail
    return {"codigo": codigo, "validade_horas": validade}


@app.get("/api/auth/esqueci/{codigo}")
def auth_esqueci_situacao(codigo: str):
    p = dbs.pedido_ler(seg.resumo(codigo))
    if not p:
        return {"status": "pendente"}   # não revela que o e-mail não existe — a tela vence sozinha
    p = _situacao_pedido(p)
    saida = {"status": p.get("status")}
    if p["status"] == "liberado":
        saida.update(liberado_ate=p.get("liberado_ate"), nome=(p.get("nome") or "").split(" ")[0],
                     liberado_por=p.get("liberado_por_nome"))
    if p["status"] == "recusado":
        saida["motivo"] = p.get("motivo") or ""
    return saida


@app.post("/api/auth/esqueci/{codigo}/senha")
def auth_esqueci_concluir(codigo: str, payload: EsqueciSenha, request: Request, response: Response):
    p = dbs.pedido_ler(seg.resumo(codigo))
    p = _situacao_pedido(p) if p else None
    if not p or p.get("status") != "liberado":
        raise HTTPException(status_code=400, detail="Este pedido não está liberado (ou a liberação venceu). Peça de novo.")
    email = p["email"]
    u = db.usuario_ler(email)
    if not u or not u.get("ativo", True):
        raise HTTPException(status_code=400, detail="Esta conta está desativada. Fale com o administrador.")
    _cfg()
    erro = seg.senha_aceitavel(payload.senha)
    if erro:
        raise HTTPException(status_code=400, detail=erro)
    nova_versao = int(u.get("versao_sessao", 1)) + 1   # derruba todas as sessões antigas
    agora = datetime.now().isoformat()
    db.usuario_atualizar(email, {"senha_hash": seg.gerar_hash(payload.senha), "versao_sessao": nova_versao,
                                 "trocar_senha": False, "senha_trocada_em": agora, "ultimo_login": agora})
    _esquecer_cache(email)
    dbs.pedido_atualizar(p["id"], {"status": "concluido", "concluido_em": agora})
    seg.limpar_falhas(f"email:{email}")
    try:
        dbs.sessoes_revogar_email(email, "senha nova")
    except Exception:
        pass
    _esquecer_avisos()
    try:
        db.log_permissao({"por": email, "por_nome": u.get("nome") or email, "tipo": "senha", "alvo": email,
                          "descricao": f"Criou senha nova pelo “Esqueci minha senha” (liberado por {p.get('liberado_por_nome') or p.get('liberado_por') or '?'})"})
    except Exception:
        pass
    u2 = {**u, "versao_sessao": nova_versao, "trocar_senha": False}
    _abrir_sessao(request, response, u2, payload.lembrar)
    return _sessao_publica(u2)


# ──────────────────────────────────────────────────────────────────────────
# ADMIN — usuários e permissões (o porteiro já garante que só admin chega)
# ──────────────────────────────────────────────────────────────────────────
class UsuarioNovo(BaseModel):
    nome: str
    email: str
    senha: str
    papel: str = "usuario"
    modulos: List[str] = []
    ativo: bool = True
    perfil: Optional[str] = ""
    departamentos: List[str] = []


class UsuarioEdicao(BaseModel):
    nome: Optional[str] = None
    papel: Optional[str] = None
    modulos: Optional[List[str]] = None
    ativo: Optional[bool] = None
    nova_senha: Optional[str] = None
    perfil: Optional[str] = None
    departamentos: Optional[List[str]] = None


def _validar_perfil_deps(perfil: Optional[str], deps: Optional[List[str]]):
    if perfil:
        p = _perfis().get(perfil)
        if not p or p.get("arquivado"):
            raise HTTPException(status_code=400, detail="Perfil inválido ou arquivado.")
    if deps:
        ids = {d["id"] for d in _todos_setores()}
        invalidos = [d for d in deps if d not in ids]
        if invalidos:
            raise HTTPException(status_code=400, detail="Departamento(s) inexistente(s).")


def _log(request: Request, tipo: str, alvo: str, descricao: str, antes=None, depois=None) -> None:
    try:
        db.log_permissao({"por": _email(request), "por_nome": _nome_de(request), "tipo": tipo, "alvo": alvo,
                          "descricao": descricao, "antes": antes, "depois": depois})
    except Exception as e:
        print(f"[AVISO] não consegui gravar o histórico de permissões: {e!r}")


def _validar_papel_modulos(papel: Optional[str], modulos_: Optional[List[str]]):
    if papel is not None and papel not in mods.PAPEIS:
        raise HTTPException(status_code=400, detail="Papel inválido.")
    if modulos_ is not None:
        invalidos = [m for m in modulos_ if m not in mods.IDS_VALIDOS]
        if invalidos:
            raise HTTPException(status_code=400, detail=f"Módulo(s) inexistente(s): {', '.join(invalidos)}")


@app.get("/api/admin/usuarios")
def admin_listar():
    # [v4.2] pedido de senha aberto de cada pessoa (botão 🔓 Liberar ao lado do nome)
    try:
        pedidos = {}
        for p in _pedidos_abertos():
            pedidos.setdefault(p.get("email"), {k: p.get(k) for k in ("id", "status", "criado_em", "liberado_ate")})
        bloq = {b["valor"]: {"chave": b["chave"], "ate": b["ate"]} for b in _bloqueios({}) if b["tipo"] == "email"}
    except Exception as e:
        print(f"[AVISO] pedidos de senha: {e!r}")
        pedidos, bloq = {}, {}
    return {"usuarios": [{**_usuario_publico(u), "niveis": _niveis(u), "abas_ef": _abas(u), "perfil_nome": _nome_perfil(u),
                          "pedido_senha": pedidos.get(u.get("email")), "bloqueio": bloq.get(u.get("email"))} for u in db.usuarios_listar()],
            "modulos": mods.catalogo_publico(), "papeis": mods.PAPEIS, "niveis": perm.NIVEIS,
            "perfis": list(_perfis().values()),
            "departamentos": [{"id": d["id"], "nome": (d.get("dados") or {}).get("nome", "")} for d in _todos_setores()]}


@app.post("/api/admin/usuarios")
def admin_criar(payload: UsuarioNovo, request: Request):
    admin = usuario_atual(request)
    email = payload.email.strip().lower()
    if not _EMAIL_VALIDO.match(email):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    if not payload.nome.strip():
        raise HTTPException(status_code=400, detail="Informe o nome.")
    erro = seg.senha_aceitavel(payload.senha)
    if erro:
        raise HTTPException(status_code=400, detail=erro)
    _validar_papel_modulos(payload.papel, payload.modulos)
    _validar_perfil_deps(payload.perfil, payload.departamentos)
    criado = db.usuario_criar(email, {
        "email": email, "nome": payload.nome.strip(), "papel": payload.papel,
        "modulos": sorted(set(payload.modulos)), "ativo": payload.ativo,
        "perfil": payload.perfil or "", "departamentos": sorted(set(payload.departamentos)),
        "senha_hash": seg.gerar_hash(payload.senha), "versao_sessao": 1, "trocar_senha": True,
        "criado_em": datetime.now().isoformat(), "criado_por": admin["email"],
    })
    if not criado:
        raise HTTPException(status_code=409, detail="Já existe um usuário com esse e-mail.")
    _log(request, "usuario", email, f"Usuário criado com papel “{payload.papel}” e perfil “{(_perfis().get(payload.perfil) or {}).get('nome', 'sem perfil')}”.")
    return {"sucesso": True}


@app.put("/api/admin/usuarios/{email}")
def admin_editar(email: str, payload: UsuarioEdicao, request: Request):
    admin = usuario_atual(request)
    email = email.strip().lower()
    alvo = db.usuario_ler(email)
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    _validar_papel_modulos(payload.papel, payload.modulos)
    _validar_perfil_deps(payload.perfil, payload.departamentos)
    if email == admin["email"] and (payload.ativo is False or (payload.papel and payload.papel != "admin")):
        raise HTTPException(status_code=400, detail="Você não pode desativar nem tirar o admin de você mesmo.")

    campos: dict = {}
    if payload.nome is not None:
        if not payload.nome.strip():
            raise HTTPException(status_code=400, detail="O nome não pode ficar vazio.")
        campos["nome"] = payload.nome.strip()
    if payload.papel is not None:
        campos["papel"] = payload.papel
    if payload.modulos is not None:
        campos["modulos"] = sorted(set(payload.modulos))
    if payload.ativo is not None:
        campos["ativo"] = payload.ativo
    if payload.perfil is not None:
        campos["perfil"] = payload.perfil
    if payload.departamentos is not None:
        campos["departamentos"] = sorted(set(payload.departamentos))
    derrubar_sessoes = False
    if payload.nova_senha:
        erro = seg.senha_aceitavel(payload.nova_senha)
        if erro:
            raise HTTPException(status_code=400, detail=erro)
        campos["senha_hash"] = seg.gerar_hash(payload.nova_senha)
        campos["trocar_senha"] = True
        derrubar_sessoes = True
    if payload.ativo is False and alvo.get("ativo", True):
        derrubar_sessoes = True
    if derrubar_sessoes:
        campos["versao_sessao"] = int(alvo.get("versao_sessao", 1)) + 1
    if campos:
        campos["alterado_em"] = datetime.now().isoformat()
        campos["alterado_por"] = admin["email"]
        db.usuario_atualizar(email, campos)
        _esquecer_cache(email)
        if derrubar_sessoes:   # [v4.2] tira também da lista de Sessões ativas
            try:
                dbs.sessoes_revogar_email(email, admin["email"])
                _cache_sessoes.clear()
            except Exception:
                pass
        nomes_p = lambda pid: (_perfis().get(pid) or {}).get("nome", "sem perfil") if pid else "sem perfil"
        nomes_d = lambda ids: ", ".join(sorted((d.get("dados") or {}).get("nome", "?") for d in _todos_setores() if d["id"] in (ids or []))) or "todos"
        for chave, rotulo, fmt in (("papel", "Papel", str), ("perfil", "Perfil", nomes_p), ("departamentos", "Departamentos", nomes_d), ("ativo", "Ativo", lambda v: "sim" if v else "não")):
            if chave in campos and campos[chave] != alvo.get(chave):
                _log(request, "usuario", email, f"{rotulo} alterado", fmt(alvo.get(chave)), fmt(campos[chave]))
        if payload.nova_senha:
            _log(request, "usuario", email, "Senha redefinida pelo administrador")
    return {"sucesso": True}


@app.get("/api/admin/senha-aleatoria")
def admin_senha_aleatoria():
    return {"senha": seg.senha_aleatoria()}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.3] PERFIS DE ACESSO (aba Permissões) + HISTÓRICO + DEPARTAMENTOS
# ──────────────────────────────────────────────────────────────────────────
class PerfilNovo(BaseModel):
    nome: str
    descricao: str = ""


class PerfilEdicao(BaseModel):
    nome: Optional[str] = None
    descricao: Optional[str] = None
    niveis: Optional[dict] = None
    abas: Optional[dict] = None   # [v4.16] {sistema: {aba: 0–4 | null (segue o sistema)}}


class PerfilArquivar(BaseModel):
    arquivado: bool


def _uso_perfis() -> dict:
    uso: dict = {}
    for u in db.usuarios_listar():
        if u.get("perfil"):
            uso[u["perfil"]] = uso.get(u["perfil"], 0) + 1
    return uso


@app.get("/api/admin/perfis")
def admin_perfis():
    return {"perfis": list(_perfis().values()), "modulos": mods.catalogo_publico(), "niveis": perm.NIVEIS, "uso": _uso_perfis(), "niveis_aba": mods.NIVEIS_ABA}


@app.post("/api/admin/perfis")
def admin_perfil_criar(payload: PerfilNovo, request: Request):
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do perfil.")
    if any((p.get("nome") or "").strip().lower() == nome.lower() for p in _perfis().values()):
        raise HTTPException(status_code=409, detail="Já existe um perfil com esse nome.")
    base = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-")[:40] or "perfil"
    pid, i = base, 2
    while pid in _perfis():
        pid, i = f"{base}-{i}", i + 1
    # nasce SEM nenhuma permissão: o administrador marca o que ele pode
    db.perfil_gravar(pid, {"nome": nome, "descricao": payload.descricao.strip(), "niveis": {}, "arquivado": False,
                           "ordem": 50, "criado_em": datetime.now().isoformat(), "criado_por": _email(request)})
    _esquecer_perfis()
    _log(request, "perfil", nome, "Perfil criado (sem nenhuma permissão)")
    return {"sucesso": True, "id": pid}


@app.put("/api/admin/perfis/{perfil_id}")
def admin_perfil_editar(perfil_id: str, payload: PerfilEdicao, request: Request):
    atual = _perfis().get(perfil_id)
    if not atual:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    campos: dict = {}
    if payload.nome is not None:
        nome = payload.nome.strip()
        if not nome:
            raise HTTPException(status_code=400, detail="O nome não pode ficar vazio.")
        if any(pid != perfil_id and (p.get("nome") or "").strip().lower() == nome.lower() for pid, p in _perfis().items()):
            raise HTTPException(status_code=409, detail="Já existe um perfil com esse nome.")
        if nome != atual.get("nome"):
            campos["nome"] = nome
            _log(request, "perfil", atual.get("nome"), "Perfil renomeado", atual.get("nome"), nome)
    if payload.descricao is not None:
        campos["descricao"] = payload.descricao.strip()
    if payload.niveis is not None:
        cat = {m["id"]: m for m in mods.MODULOS}
        niveis = dict(atual.get("niveis") or {})
        for mid, n in payload.niveis.items():
            if mid not in cat:
                raise HTTPException(status_code=400, detail=f"Sistema inexistente: {mid}")
            try:
                n = max(0, min(3, int(n)))
            except Exception:
                raise HTTPException(status_code=400, detail="Nível inválido.")
            if mods.so_acesso(cat[mid]):
                n = min(n, 1)
            antes = int(niveis.get(mid, 0) or 0)
            if antes != n:
                niveis[mid] = n
                _log(request, "perfil", atual.get("nome"), f"{cat[mid]['nome']}", perm.NIVEIS[antes], perm.NIVEIS[n])
        campos["niveis"] = niveis
    if payload.abas is not None:   # [v4.16] permissão por aba no perfil
        abas = {k: dict(v or {}) for k, v in (atual.get("abas") or {}).items()}
        for mid, mapa in payload.abas.items():
            validas = {a["id"]: a for a in mods.abas_de(mid)}
            if not validas:
                raise HTTPException(status_code=400, detail=f"O sistema {mid} não tem abas.")
            for aid, n in (mapa or {}).items():
                if aid not in validas:
                    raise HTTPException(status_code=400, detail=f"Aba inexistente: {aid}")
                antes = (abas.get(mid) or {}).get(aid)
                novo = None if n is None or n == "" else max(0, min(4, int(n)))
                if antes == novo:
                    continue
                abas.setdefault(mid, {})[aid] = novo
                txt = lambda v: "Segue o sistema" if v is None else mods.NIVEIS_ABA[v]
                _log(request, "perfil", atual.get("nome"), f"{mods.POR_ID[mid]['nome']} › aba {validas[aid]['nome']}", txt(antes), txt(novo))
        campos["abas"] = abas
    if campos:
        campos["alterado_em"] = datetime.now().isoformat()
        campos["alterado_por"] = _email(request)
        db.perfil_gravar(perfil_id, campos)
        _esquecer_perfis()
    return {"sucesso": True}


@app.post("/api/admin/perfis/{perfil_id}/arquivar")
def admin_perfil_arquivar(perfil_id: str, payload: PerfilArquivar, request: Request):
    atual = _perfis().get(perfil_id)
    if not atual:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    if payload.arquivado and _uso_perfis().get(perfil_id):
        raise HTTPException(status_code=400, detail="Não dá para arquivar um perfil em uso — as pessoas ficariam sem regra. Troque o perfil delas antes.")
    db.perfil_gravar(perfil_id, {"arquivado": payload.arquivado})
    _esquecer_perfis()
    _log(request, "perfil", atual.get("nome"), "Perfil arquivado" if payload.arquivado else "Perfil reativado")
    return {"sucesso": True}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.9] ACESSO INDIVIDUAL POR SISTEMA (aba Permissões → clicar no sistema)
# Grava no usuário só a EXCEÇÃO daquele sistema; sem exceção = segue o perfil.
#   nivel: 0–3 → nível fixo · perfil: "gestor" → vale como aquele perfil
#   nenhum dos dois → volta a seguir o perfil da pessoa
# ──────────────────────────────────────────────────────────────────────────
class AcessoIndividual(BaseModel):
    nivel: Optional[int] = None
    perfil: Optional[str] = None


def _texto_acesso(m: dict, valor) -> str:
    if valor is None:
        return "Segue o perfil"
    if isinstance(valor, str) and valor.startswith("perfil:"):
        p = _perfis().get(valor[7:]) or {}
        n = mods.resolver_excecao(m, valor, _perfis())
        return f"Como {p.get('nome', valor[7:])} ({perm.NIVEIS.get(n, '?') if n is not None else '?'})"
    n = mods.resolver_excecao(m, valor, _perfis())
    return ("Acessa" if n else "Sem acesso") if mods.so_acesso(m) else perm.NIVEIS.get(n, str(valor))


@app.put("/api/admin/acessos/{modulo_id}/{email}")
def admin_acesso_individual(modulo_id: str, email: str, payload: AcessoIndividual, request: Request):
    m = mods.POR_ID.get(modulo_id)
    if not m:
        raise HTTPException(status_code=404, detail="Sistema inexistente.")
    email = email.strip().lower()
    alvo = db.usuario_ler(email)
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if alvo.get("papel") == "admin":
        raise HTTPException(status_code=400, detail="Administrador já tem acesso total — não precisa de acesso individual.")
    if payload.nivel is not None and payload.perfil:
        raise HTTPException(status_code=400, detail="Escolha um nível OU um perfil, não os dois.")
    if payload.perfil:
        p = _perfis().get(payload.perfil)
        if not p or p.get("arquivado"):
            raise HTTPException(status_code=400, detail="Perfil inválido ou arquivado.")
        novo = f"perfil:{payload.perfil}"
    elif payload.nivel is not None:
        if not 0 <= int(payload.nivel) <= 3:
            raise HTTPException(status_code=400, detail="Nível inválido (0 a 3).")
        novo = min(int(payload.nivel), 1) if mods.so_acesso(m) else int(payload.nivel)
    else:
        novo = None
    acessos = dict(alvo.get("acessos") or {})
    info = dict(alvo.get("acessos_info") or {})
    antes = acessos.get(modulo_id)
    if antes == novo:
        return {"sucesso": True, "niveis": _niveis(alvo)}
    if novo is None:
        # grava null (e não apaga a chave): funciona igual se o db fizer update ou set(merge=True)
        acessos[modulo_id] = None
        info[modulo_id] = None
    else:
        acessos[modulo_id] = novo
        info[modulo_id] = {"por": _email(request), "por_nome": _nome_de(request), "em": datetime.now().isoformat()}
    db.usuario_atualizar(email, {"acessos": acessos, "acessos_info": info,
                                 "alterado_em": datetime.now().isoformat(), "alterado_por": _email(request)})
    _esquecer_cache(email)
    _log(request, "acesso", f"{alvo.get('nome') or email} · {m['nome']}", "Acesso individual no sistema",
         _texto_acesso(m, antes), _texto_acesso(m, novo))
    return {"sucesso": True, "niveis": _niveis({**alvo, "acessos": acessos})}


class AcessoAba(BaseModel):
    aba: str
    nivel: Optional[int] = None   # None = segue o perfil / o sistema


@app.put("/api/admin/acessos-abas/{modulo_id}/{email}")
def admin_acesso_aba(modulo_id: str, email: str, payload: AcessoAba, request: Request):
    """[v4.16] Acesso individual de UMA aba para UMA pessoa."""
    validas = {a["id"]: a for a in mods.abas_de(modulo_id)}
    if payload.aba not in validas:
        raise HTTPException(status_code=404, detail="Aba inexistente.")
    email = email.strip().lower()
    alvo = db.usuario_ler(email)
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    if alvo.get("papel") == "admin":
        raise HTTPException(status_code=400, detail="Administrador já tem acesso total.")
    novo = None if payload.nivel is None else max(0, min(4, int(payload.nivel)))
    abas = {k: dict(v or {}) for k, v in (alvo.get("abas") or {}).items()}
    antes = (abas.get(modulo_id) or {}).get(payload.aba)
    if antes == novo:
        return {"sucesso": True}
    abas.setdefault(modulo_id, {})[payload.aba] = novo
    db.usuario_atualizar(email, {"abas": abas, "alterado_em": datetime.now().isoformat(), "alterado_por": _email(request)})
    _esquecer_cache(email)
    txt = lambda v: "Segue o perfil" if v is None else mods.NIVEIS_ABA[v]
    _log(request, "acesso", f"{alvo.get('nome') or email} · {mods.POR_ID[modulo_id]['nome']} › {validas[payload.aba]['nome']}", "Acesso individual na aba", txt(antes), txt(novo))
    return {"sucesso": True, "abas": _abas({**alvo, "abas": abas}).get(modulo_id, {})}


@app.get("/api/admin/permissoes-log")
def admin_permissoes_log():
    return {"historico": db.log_permissoes_listar()}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v4.2] LIBERAÇÕES · SESSÕES ATIVAS · CONFIGURAÇÕES (só admin — o porteiro garante)
# ──────────────────────────────────────────────────────────────────────────
_cache_avisos: dict = {"t": 0.0, "dados": None}


def _esquecer_avisos() -> None:
    _cache_avisos["t"] = 0.0


def _nomes_por_email() -> dict:
    try:
        return {u.get("email"): (u.get("nome") or u.get("email")) for u in db.usuarios_listar()}
    except Exception:
        return {}


def _bloqueios(nomes: Optional[dict] = None) -> list:
    nomes = nomes if nomes is not None else _nomes_por_email()
    saida = []
    for b in seg.bloqueios_ativos():
        saida.append({**b, "ate": datetime.fromtimestamp(b["ate"]).isoformat(),
                      "nome": nomes.get(b["valor"]) if b["tipo"] == "email" else None})
    return saida


def _pedidos_abertos() -> list:
    return [p for p in (_situacao_pedido(x) for x in dbs.pedidos_abertos()) if p.get("status") in ("pendente", "liberado")]


@app.get("/api/admin/avisos")
def admin_avisos():
    """O sino: pedidos de senha esperando + contas bloqueadas. Cache de 10 s (economiza leituras)."""
    if _cache_avisos["dados"] is not None and time.time() - _cache_avisos["t"] < 10:
        return _cache_avisos["dados"]
    abertos = _pedidos_abertos()
    bloq = [b for b in _bloqueios() if b["tipo"] == "email" and b.get("nome")] if _cfg().get("avisar_bloqueio", True) else []
    itens = [{"tipo": "senha", "id": p["id"], "email": p.get("email"), "nome": p.get("nome"), "em": p.get("criado_em"),
              "status": p.get("status")} for p in abertos]
    itens += [{"tipo": "bloqueio", "chave": b["chave"], "email": b["valor"], "nome": b["nome"], "ate": b["ate"]} for b in bloq]
    dados = {"pendentes": sum(1 for p in abertos if p.get("status") == "pendente"),
             "liberados": sum(1 for p in abertos if p.get("status") == "liberado"),
             "bloqueados": len(bloq), "itens": itens[:20]}
    _cache_avisos.update(t=time.time(), dados=dados)
    return dados


@app.get("/api/admin/liberacoes")
def admin_liberacoes():
    nomes = _nomes_por_email()
    abertos = _pedidos_abertos()
    ids_abertos = {p["id"] for p in abertos}
    recentes = [p for p in dbs.pedidos_recentes(60) if p["id"] not in ids_abertos]
    publico = lambda p: {k: p.get(k) for k in ("id", "email", "nome", "status", "criado_em", "expira_em", "ip", "navegador",
                                                "liberado_por_nome", "liberado_em", "liberado_ate", "recusado_por_nome",
                                                "recusado_em", "motivo", "concluido_em", "expirado_em", "substituido_em")}
    return {"abertos": [publico(p) for p in abertos], "recentes": [publico(p) for p in recentes],
            "bloqueios": _bloqueios(nomes), "minutos": int(_cfg().get("liberacao_minutos", 15))}


class Recusa(BaseModel):
    motivo: str = ""


@app.post("/api/admin/liberacoes/{pid}/liberar")
def admin_liberar(pid: str, request: Request):
    p = dbs.pedido_ler(pid)
    if not p:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    p = _situacao_pedido(p)
    if p.get("status") != "pendente":
        raise HTTPException(status_code=400, detail=f"Este pedido não está mais esperando (situação: {p.get('status')}).")
    minutos = int(_cfg().get("liberacao_minutos", 15))
    agora = datetime.now()
    dbs.pedido_atualizar(pid, {"status": "liberado", "liberado_por": _email(request), "liberado_por_nome": _nome_de(request),
                               "liberado_em": agora.isoformat(),
                               "liberado_ate": datetime.fromtimestamp(time.time() + minutos * 60).isoformat()})
    seg.limpar_falhas(f"email:{p.get('email')}")   # se estava bloqueado por senha errada, desbloqueia junto
    _esquecer_avisos()
    _log(request, "senha", p.get("nome") or p.get("email"), f"Liberou o pedido de senha nova (vale {minutos} min)")
    return {"sucesso": True, "minutos": minutos}


@app.post("/api/admin/liberacoes/{pid}/recusar")
def admin_recusar(pid: str, payload: Recusa, request: Request):
    p = dbs.pedido_ler(pid)
    if not p:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")
    if p.get("status") not in ("pendente", "liberado"):
        raise HTTPException(status_code=400, detail="Este pedido já foi encerrado.")
    dbs.pedido_atualizar(pid, {"status": "recusado", "recusado_por": _email(request), "recusado_por_nome": _nome_de(request),
                               "recusado_em": datetime.now().isoformat(), "motivo": payload.motivo.strip()[:300]})
    _esquecer_avisos()
    _log(request, "senha", p.get("nome") or p.get("email"), "Recusou o pedido de senha nova" + (f" — {payload.motivo.strip()[:300]}" if payload.motivo.strip() else ""))
    return {"sucesso": True}


class Desbloqueio(BaseModel):
    chave: str


@app.post("/api/admin/bloqueios/desbloquear")
def admin_desbloquear(payload: Desbloqueio, request: Request):
    seg.limpar_falhas(payload.chave)
    _esquecer_avisos()
    _log(request, "senha", payload.chave.split(":", 1)[-1], "Desbloqueou as tentativas de login")
    return {"sucesso": True}


@app.get("/api/admin/sessoes")
def admin_sessoes(request: Request):
    publico = lambda s: {k: s.get(k) for k in ("sid", "email", "nome", "criado_em", "ultimo_uso", "expira_em", "ip",
                                                "navegador", "lembrar", "fabrica")}
    return {"sessoes": [publico(s) for s in dbs.sessoes_ativas()], "minha": getattr(request.state, "sid", None)}


@app.delete("/api/admin/sessoes/{sid}")
def admin_sessao_derrubar(sid: str, request: Request):
    s = dbs.sessao_ler(sid)
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    dbs.sessao_atualizar(sid, {"revogada": True, "revogada_por": _email(request), "revogada_em": datetime.now().isoformat()})
    _esquecer_sessao(sid)
    _log(request, "sessao", s.get("nome") or s.get("email"), "Derrubou uma sessão", s.get("ip"), "encerrada")
    return {"sucesso": True}


class DerrubarUsuario(BaseModel):
    email: str


@app.post("/api/admin/sessoes/derrubar-usuario")
def admin_derrubar_usuario(payload: DerrubarUsuario, request: Request):
    email = payload.email.strip().lower()
    if email == _email(request):
        raise HTTPException(status_code=400, detail="Para sair de todas as suas sessões, troque a sua senha.")
    alvo = db.usuario_ler(email)
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    db.usuario_atualizar(email, {"versao_sessao": int(alvo.get("versao_sessao", 1)) + 1})   # vale também p/ cookies antigos
    _esquecer_cache(email)
    n = dbs.sessoes_revogar_email(email, _email(request))
    _cache_sessoes.clear()
    _log(request, "sessao", alvo.get("nome") or email, f"Derrubou todas as sessões ({n})")
    return {"sucesso": True, "sessoes": n}


CONFIG_LIMITES = {   # campo: (mínimo, máximo) — números da aba Configurações
    "liberacao_minutos": (5, 120), "pedido_validade_horas": (1, 168),
    "tentativas_email": (3, 20), "bloqueio_email_min": (1, 240),
    "tentativas_ip": (5, 100), "bloqueio_ip_min": (1, 240),
    "sessao_horas": (1, 72), "lembrar_dias": (1, 90), "senha_minimo": (6, 32),
}
CONFIG_BOOLEANOS = ("esqueci_ativo", "lembrar_ativo", "senha_numero", "senha_letra", "avisar_bloqueio")


@app.get("/api/admin/configuracoes")
def admin_config():
    return {"config": _cfg(), "padrao": dbs.CONFIG_PADRAO, "limites": CONFIG_LIMITES,
            "fabrica": bool(FAB_EMAIL and FAB_SENHA), "fabrica_email": FAB_EMAIL or None,
            "segredo_temporario": seg.SEGREDO_TEMPORARIO}


@app.put("/api/admin/configuracoes")
def admin_config_salvar(payload: dict, request: Request):
    atual = _cfg()
    campos = {}
    for k, v in (payload or {}).items():
        if k in CONFIG_LIMITES:
            lo, hi = CONFIG_LIMITES[k]
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"Valor inválido em {k}.")
            if not lo <= n <= hi:
                raise HTTPException(status_code=400, detail=f"{k}: use um número entre {lo} e {hi}.")
            campos[k] = n
        elif k in CONFIG_BOOLEANOS:
            campos[k] = bool(v)
    mudou = {k: v for k, v in campos.items() if atual.get(k) != v}
    if mudou:
        dbs.config_salvar({**mudou, "alterado_em": datetime.now().isoformat(), "alterado_por": _email(request)})
        _cache_cfg["t"] = 0.0
        _cfg()
        for k, v in mudou.items():
            _log(request, "config", k, "Configuração da segurança", atual.get(k), v)
    return {"sucesso": True, "config": _cfg()}


@app.get("/api/admin/departamentos")
def admin_departamentos():
    usuarios = db.usuarios_listar()
    cks = db.diagdep_checklists()
    saida = []
    for d in _todos_setores():
        dados = d.get("dados") or {}
        saida.append({"id": d["id"], "nome": dados.get("nome", ""), "descricao": dados.get("descricao", ""), "cor": dados.get("cor"),
                      "pessoas": len(dados.get("pessoas") or []), "checklist": cks.get(d["id"]) or {},
                      "restritos": [u.get("nome") or u["email"] for u in usuarios if d["id"] in (u.get("departamentos") or [])]})
    return {"departamentos": saida}


# Rota de "estou vivo" (health check do Render).
# [NOVO v2.1] Rota de "estou vivo" exigida pelo Streamlit Cloud. Ele chama
# /healthz (e, em algumas versões, /_stcore/health) pra saber se o app subiu;
# sem estas rotas o FastAPI responde 404 e o Cloud derruba o app.
@app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/_stcore/health", methods=["GET", "HEAD"], include_in_schema=False)
def health():
    return PlainTextResponse("ok")


STATUS_VALIDOS = [
    "Planejamento", "Iniciar", "Em Andamento", "Aprovação", "Reprovado",
    "Concluído", "Bloqueado", "Pausado", "Cancelado",
]


# [NOVO v2.2] Diagnóstico: testa o Firestore e devolve o erro REAL na tela
# (em vez de ficar carregando). Abra /api/diag no navegador.
@app.get("/api/diag", include_in_schema=False)
def diagnostico():
    try:
        qtd = len(db.atividades_listar(excluido=False))
        return {"firestore": "ok", "atividades_ativas": qtd, "pid": os.getpid()}
    except Exception as e:
        return {"firestore": "ERRO", "erro": repr(e), "detalhe": traceback.format_exc()[-1500:]}


class AtividadeCreate(BaseModel):
    data: str
    responsavel: str
    area_impactada: str
    problema_causa: str
    demanda: str
    atividade: str
    prazo_previsto: str
    prioridade: str = "Baixa"
    indicador_relacionado: str
    volume: Optional[int] = 0
    bloqueio: Optional[str] = "—"
    valor_gerado: Optional[str] = "Produtividade"
    observacoes: Optional[str] = ""
    evidencia: Optional[str] = ""          # [VOLTOU v2.3] o front envia e a tela usa
    prazo_entregue: Optional[str] = None   # [VOLTOU v2.3]

    # Validação que faltava por completo na primeira versão — é o motivo
    # direto de existirem atividades sem nome no SQLite antigo. Os campos
    # obrigatórios do cadastro (seção "Regras do Cadastro de Atividades" da
    # especificação) agora são checados de verdade: string vazia ou só
    # espaço é rejeitada com erro 422.
    @field_validator(
        "responsavel", "area_impactada", "problema_causa", "demanda",
        "atividade", "prazo_previsto", "indicador_relacionado",
    )
    @classmethod
    def nao_pode_ser_vazio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("campo obrigatório não pode ficar em branco")
        return v.strip()


class AcaoCreate(BaseModel):
    descricao: str
    lembrete: Optional[str] = None  # data opcional (YYYY-MM-DD)

    @field_validator("descricao")
    @classmethod
    def nao_vazio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("descrição da ação não pode ficar em branco")
        return v.strip()


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def status_valido(cls, v: str) -> str:
        if v not in STATUS_VALIDOS:
            raise ValueError(f"status precisa ser um de: {', '.join(STATUS_VALIDOS)}")
        return v


def _dias_ate_prazo(prazo_iso: str) -> Optional[int]:
    try:
        d = datetime.strptime(prazo_iso, "%Y-%m-%d").date()
        return (d - date.today()).days
    except Exception:
        return None


@app.get("/api/kpis")
def get_kpis():
    ativas_docs = db.atividades_listar(excluido=False)

    ativas = sum(1 for a in ativas_docs if a.get("status") not in ("Concluído", "Cancelado", "Reprovado"))
    bloqueios = sum(1 for a in ativas_docs if a.get("bloqueio", "—") != "—")

    vencimento = 0
    atrasados = 0
    for a in ativas_docs:
        if a.get("status") in ("Concluído", "Cancelado", "Reprovado"):
            continue
        dias = _dias_ate_prazo(a.get("prazo", ""))
        if dias is not None and dias <= 7:
            vencimento += 1
            if dias < 0:
                atrasados += 1

    return {
        "ativas": ativas,
        "vencimento": vencimento,
        "vencimento_atrasados": atrasados,
        "bloqueios": bloqueios,
        "valor_mes": "R$ 482 mil",
    }


@app.get("/api/atividades", response_model=List[dict])
def get_atividades(excluido: Optional[bool] = False):
    return db.atividades_listar(excluido=bool(excluido))


@app.post("/api/atividades")
def create_atividade(payload: AtividadeCreate, request: Request):
    mes_val = payload.prazo_previsto.split("-")[1] if "-" in payload.prazo_previsto else "10"
    bloqueio_val = payload.bloqueio if payload.bloqueio else "—"

    novo_id = db.atividades_inserir({
        "nome": payload.atividade,
        "area": payload.area_impactada,
        "resp": payload.responsavel,
        "prio": payload.prioridade,
        "status": "Planejamento",
        "prazo": payload.prazo_previsto,
        "bloqueio": bloqueio_val,
        "mes": mes_val,
        # [VOLTOU v2.3] campos que a versão SQLite gravava e a tela usa
        # (indicadores, governança, tabela de acompanhamento) — na
        # primeira versão Firestore eles tinham ficado de fora.
        "indicador": payload.indicador_relacionado,
        "volume": payload.volume or 0,
        "valor_gerado": payload.valor_gerado,
        "observacoes": payload.observacoes,
        "evidencia": payload.evidencia,
        "prazo_entregue": payload.prazo_entregue,
        "problema_causa": payload.problema_causa,
        "demanda": payload.demanda,
        "data_cadastro": payload.data,
        "criado_por_email": _email(request),   # [v3.3] base do perfil "Adicionar"
    })
    return {"sucesso": True, "mensagem": "Atividade salva com sucesso no Firestore!", "id": novo_id}


@app.put("/api/atividades/{atividade_id}/status")
def atualizar_status(atividade_id: str, payload: StatusUpdate, request: Request):
    _exigir_editar_ou_dono(request, "diario", db.atividade_dono(atividade_id))
    ok = db.atividades_status_atualizar(atividade_id, payload.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Atividade não encontrada (ou já excluída).")
    return {"sucesso": True}


@app.delete("/api/atividades/{atividade_id}")
def excluir_atividade(atividade_id: str):
    ok = db.atividades_excluir(atividade_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    return {"sucesso": True}


# ──────────────────────────────────────────────────────────────────────────
# [VOLTOU v2.3] AÇÕES dentro de uma atividade + RELATÓRIO de ações
# ──────────────────────────────────────────────────────────────────────────
@app.get("/api/atividades/{atividade_id}/acoes", response_model=List[dict])
def listar_acoes(atividade_id: str):
    return db.acoes_listar(atividade_id)


@app.post("/api/atividades/{atividade_id}/acoes")
def criar_acao(atividade_id: str, payload: AcaoCreate, request: Request):
    if not db.atividade_existe(atividade_id):
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    novo_id = db.acoes_inserir(atividade_id, payload.descricao, payload.lembrete, _email(request))
    return {"sucesso": True, "id": novo_id}


@app.delete("/api/acoes/{acao_id}")
def excluir_acao(acao_id: str):
    if not db.acoes_excluir(acao_id):
        raise HTTPException(status_code=404, detail="Ação não encontrada.")
    return {"sucesso": True}


@app.put("/api/acoes/{acao_id}/encerrar-lembrete")
def encerrar_lembrete(acao_id: str, request: Request):
    _exigir_editar_ou_dono(request, "diario", db.acao_dono(acao_id))
    if not db.acoes_encerrar_lembrete(acao_id):
        raise HTTPException(status_code=404, detail="Ação não encontrada.")
    return {"sucesso": True}


@app.get("/api/acoes", response_model=List[dict])
def listar_todas_acoes():
    return db.acoes_listar_todas()


# ──────────────────────────────────────────────────────────────────────────
# [VOLTOU v2.3] UPLOAD de evidência — agora salvo no Firestore (o disco do
# Render Free é apagado a cada reinício). Limite ~900 KB por arquivo.
# ──────────────────────────────────────────────────────────────────────────
@app.post("/api/upload")
async def fazer_upload(arquivo: UploadFile = File(...)):
    conteudo = await arquivo.read()
    if len(conteudo) > db.LIMITE_ARQUIVO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(f"Arquivo de {len(conteudo)//1024} KB passa do limite de "
                    f"{db.LIMITE_ARQUIVO_BYTES//1024} KB. Use o modo Link (Drive/OneDrive)."),
        )
    novo_id = db.arquivos_salvar(arquivo.filename or "arquivo", arquivo.content_type, conteudo)
    return {"sucesso": True, "url": f"/api/arquivos/{novo_id}", "nome_original": arquivo.filename}


@app.get("/api/arquivos/{arquivo_id}", include_in_schema=False)
def baixar_arquivo(arquivo_id: str):
    dados = db.arquivos_ler(arquivo_id)
    if not dados:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    nome = (dados.get("nome") or "arquivo").replace('"', "")
    return Response(
        content=bytes(dados.get("conteudo") or b""),
        media_type=dados.get("tipo") or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{nome}"'},
    )


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v2.4] DIAGNÓSTICO — API GENÉRICA
# 3 rotas que guardam/devolvem QUALQUER JSON por seção. Não tem nenhum
# nome de campo aqui de propósito: campos e seções novas se resolvem só
# no diagnostico.html, sem precisar mexer neste arquivo.
# ──────────────────────────────────────────────────────────────────────────
_SECAO_VALIDA = re.compile(r"^[a-z0-9_]{1,60}$")
_LIMITE_SECAO_BYTES = 950 * 1024  # Firestore aceita até 1 MB por documento


class SecaoDiagnostico(BaseModel):
    dados: Any = None


def _validar_secao(secao: str) -> str:
    if not _SECAO_VALIDA.match(secao or ""):
        raise HTTPException(status_code=400, detail="Nome de seção inválido (use só letras minúsculas, números e _).")
    return secao


# [v3.3] Cada diagnóstico pertence a um DEPARTAMENTO, que é o mesmo SETOR do
# Organograma (mesmo id) — assim os dois sistemas falam do mesmo lugar.
COLECAO_ORGANOGRAMA = "organograma_setores"


def _todos_setores() -> list:
    return [d for d in db.docs_listar(COLECAO_ORGANOGRAMA) if (d.get("dados") or {}).get("_tipo") != "niveis"]


def _setores_do(u: dict) -> list:
    return [d for d in _todos_setores() if _dep_ok(u, d["id"])]


def _setor_ou_erro(u: dict, dep: str) -> dict:
    if not _dep_ok(u, dep):
        raise HTTPException(status_code=403, detail="Você não tem acesso a este departamento. Peça liberação ao administrador.")
    d = db.docs_ler(COLECAO_ORGANOGRAMA, dep)
    if not d or (d.get("dados") or {}).get("_tipo") == "niveis":
        raise HTTPException(status_code=404, detail="Departamento não encontrado (pode ter sido excluído no Organograma).")
    return d


class DepartamentoNovo(BaseModel):
    nome: str
    descricao: str = ""
    cor: str = "#2E4A7A"


def _criar_departamento(payload: DepartamentoNovo, request: Request) -> dict:
    u = usuario_atual(request)
    if _deps_permitidos(u) is not None:
        raise HTTPException(status_code=403, detail="Seu acesso é restrito a alguns departamentos — peça ao administrador para criar este.")
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do departamento.")
    if any(((d.get("dados") or {}).get("nome") or "").strip().lower() == nome.lower() for d in _todos_setores()):
        raise HTTPException(status_code=409, detail="Já existe um departamento (setor do Organograma) com esse nome.")
    dados = {"_tipo": "setor", "nome": nome, "descricao": payload.descricao.strip(), "cor": payload.cor or "#2E4A7A",
             "responsavel": "", "subsetores": [], "pessoas": []}
    return db.docs_criar(COLECAO_ORGANOGRAMA, dados, _nome_de(request), u["email"])


@app.post("/api/admin/departamentos")
def admin_departamento_criar(payload: DepartamentoNovo, request: Request):
    reg = _criar_departamento(payload, request)
    _log(request, "departamento", payload.nome.strip(), "Departamento criado (também aparece no Organograma)")
    return reg


@app.get("/api/diagnostico/departamentos")
def diag_departamentos(request: Request):
    u = usuario_atual(request)
    cks = db.diagdep_checklists()
    deps = []
    for d in _setores_do(u):
        dados = d.get("dados") or {}
        deps.append({"id": d["id"], "nome": dados.get("nome", ""), "descricao": dados.get("descricao", ""), "cor": dados.get("cor"),
                     "pessoas": len(dados.get("pessoas") or []), "checklist": cks.get(d["id"]) or {}})
    deps.sort(key=lambda x: (x["nome"] or "").lower())
    legado = db.diag_legado_resumo() if _niveis(u).get("diagnostico", 0) >= perm.EDITAR else {"secoes": 0}
    return {"departamentos": deps, "legado": legado}


@app.post("/api/diagnostico/departamentos")
def diag_departamento_criar(payload: DepartamentoNovo, request: Request):
    return _criar_departamento(payload, request)


@app.get("/api/diagnostico/dep/{dep}")
def diag_dep_tudo(dep: str, request: Request):
    d = _setor_ou_erro(usuario_atual(request), dep)
    dados = d.get("dados") or {}
    return {"departamento": {"id": dep, "nome": dados.get("nome", ""), "descricao": dados.get("descricao", ""), "cor": dados.get("cor")},
            "secoes": db.diagdep_ler_todos(dep)}


@app.get("/api/diagnostico/dep/{dep}/organograma-oficial")
def diag_dep_organograma(dep: str, request: Request):
    """As pessoas do setor no módulo Organogramas (somente leitura aqui)."""
    d = _setor_ou_erro(usuario_atual(request), dep)
    pessoas = (d.get("dados") or {}).get("pessoas") or []
    nome = {p.get("id"): (p.get("nome") or "").strip() or "(vaga)" for p in pessoas}
    return {"pessoas": [{"id": p.get("id"), "nome": (p.get("nome") or "").strip(), "cargo": p.get("cargo", ""),
                         "subsetor": p.get("subsetor", ""), "nivel": p.get("nivel", ""), "vaga": bool(p.get("vaga")),
                         "gestor": nome.get(p.get("gestor_id"), ""),
                         "tambem": [nome[x] for x in (p.get("extras") or []) if x in nome]} for p in pessoas],
            "atualizado_em": d.get("atualizado_em"), "atualizado_por": d.get("atualizado_por")}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.4] CADASTRO MESTRE — o Organograma é a fonte única de SETORES e
# PESSOAS. Mapa, Diário e Diagnóstico leem daqui para montar as listas de
# área e "quem faz" (somente leitura; respeita os departamentos liberados).
# ──────────────────────────────────────────────────────────────────────────
@app.get("/api/base/cadastros")
def base_cadastros(request: Request):
    u = usuario_atual(request)
    setores = []
    for d in _setores_do(u):
        dados = d.get("dados") or {}
        pessoas = [{"id": p.get("id"), "nome": (p.get("nome") or "").strip(), "cargo": (p.get("cargo") or "").strip(),
                    "subsetor": (p.get("subsetor") or "").strip()}
                   for p in (dados.get("pessoas") or []) if (p.get("nome") or "").strip() and not p.get("vaga")]
        setores.append({"id": d["id"], "nome": (dados.get("nome") or "").strip(), "cor": dados.get("cor"),
                        "responsavel": (dados.get("responsavel") or "").strip(),
                        "subsetores": [s if isinstance(s, str) else (s or {}).get("nome", "") for s in (dados.get("subsetores") or [])],
                        "pessoas": pessoas})
    setores = [s for s in setores if s["nome"]]
    setores.sort(key=lambda s: s["nome"].lower())
    return {"setores": setores}


@app.post("/api/diagnostico/dep/{dep}/importar-legado")
def diag_dep_importar(dep: str, request: Request):
    u = usuario_atual(request)
    if _niveis(u).get("diagnostico", 0) < perm.EDITAR:
        raise HTTPException(status_code=403, detail="Trazer o diagnóstico antigo é só para quem tem perfil “Editar” no Diagnóstico.")
    _setor_ou_erro(u, dep)
    return {"sucesso": True, "secoes": db.diagdep_importar_legado(dep, _nome_de(request))}


# [NOVO v3.5] PONTE DIAGNÓSTICO → MAPA: quais atividades deste departamento já
# viraram processo no Mapa Digital, e em que situação o processo está.
def _status_processo(dados: dict) -> str:
    f = dados.get("fases") or {}
    if (f.get("f7") or {}).get("concluido"):
        return "Concluído"
    if str((dados.get("validacao_tobe") or {}).get("parecer", "")).startswith("Aprovado"):
        return "TO BE aprovado"
    if (f.get("f5") or {}).get("concluido"):
        return "TO BE em validação"
    if (f.get("f4") or {}).get("concluido"):
        return "Desenhando o TO BE"
    if str((dados.get("validacao") or {}).get("parecer", "")).startswith("Validado"):
        return "Em diagnóstico"
    if any((x or {}).get("descricao") for x in (dados.get("passos_real") or [])):
        return "Em levantamento"
    return "Cadastrado"


@app.get("/api/diagnostico/dep/{dep}/processos-mapa")
def diag_dep_processos_mapa(dep: str, request: Request):
    setor = _setor_ou_erro(usuario_atual(request), dep)
    nome_dep = ((setor.get("dados") or {}).get("nome") or "").strip().lower()
    saida = []
    for p in db.mapa_listar():
        dados = p.get("dados") or {}
        if dados.get("_tipo"):
            continue
        origem = dados.get("origem") or {}
        if origem.get("dep") != dep and (dados.get("area") or "").strip().lower() != nome_dep:
            continue
        saida.append({"id": p["id"], "codigo": dados.get("codigo", ""), "nome": dados.get("nome", ""),
                      "atividade": origem.get("atividade") or "", "macroprocesso": dados.get("macroprocesso", ""),
                      "status": _status_processo(dados)})
    return {"processos": saida}


@app.get("/api/diagnostico/dep/{dep}/{secao}")
def diag_dep_ler(dep: str, secao: str, request: Request):
    _setor_ou_erro(usuario_atual(request), dep)
    return {"secao": secao, "dados": db.diagdep_ler(dep, _validar_secao(secao))}


@app.put("/api/diagnostico/dep/{dep}/{secao}")
def diag_dep_salvar(dep: str, secao: str, payload: SecaoDiagnostico, request: Request):
    u = usuario_atual(request)
    _setor_ou_erro(u, dep)
    _validar_secao(secao)
    tamanho = len(json.dumps(payload.dados, ensure_ascii=False, default=str).encode("utf-8"))
    if tamanho > _LIMITE_SECAO_BYTES:
        raise HTTPException(status_code=413, detail=f"A seção '{secao}' ficou com {tamanho // 1024} KB e passa do limite do Firestore (~1 MB por documento).")
    dados = payload.dados
    if _niveis(u).get("diagnostico", 0) < perm.EDITAR:
        try:
            dados = perm.so_acrescimos(db.diagdep_ler(dep, secao), dados, u["email"])
        except perm.Recusado as e:
            raise HTTPException(status_code=403, detail=f"{perm.MENSAGEM_ADICIONAR} ({e})")
    db.diagdep_salvar(dep, secao, dados, _nome_de(request))
    return {"sucesso": True}


@app.post("/api/diagnostico-ia")
def diagnostico_resumo_ia(request: Request, dep: str = ""):
    """Resumo executivo com a API da Anthropic. A chave fica SÓ no servidor
    (variável de ambiente ANTHROPIC_API_KEY no Render), nunca no HTML."""
    chave = os.environ.get("ANTHROPIC_API_KEY", "")
    if not chave:
        return {"erro": "Configure a variável ANTHROPIC_API_KEY no Render (Environment) para usar esta função."}

    if not dep:
        return {"erro": "Abra o diagnóstico de um departamento antes de gerar o resumo."}
    setor = _setor_ou_erro(usuario_atual(request), dep)
    todos = db.diagdep_ler_todos(dep)
    secoes = ["inventario", "organograma", "raci", "raci_pessoas", "raci_matriz", "diario",
              "entrevistas", "gemba", "matriz", "jornadas", "respostas"]
    dados = {s: todos.get(s) for s in secoes}
    prompt = (
        "Você está ajudando a consolidar um mapeamento de atividades (estilo Lean/Gemba) do departamento "
        f"“{(setor.get('dados') or {}).get('nome', '')}”. Abaixo estão os dados brutos coletados, em JSON. Escreva um RESUMO "
        "EXECUTIVO em português, objetivo e assertivo, cobrindo nesta ordem: 1) o que a equipe faz de "
        "fato; 2) como o trabalho é dividido; 3) de onde vêm as demandas; 4) onde o tempo é mais "
        "consumido; 5) principais dependências externas; 6) trabalho invisível identificado; e termine "
        "com 3 a 5 recomendações práticas priorizadas. Não invente dados que não estejam no JSON — "
        "se uma seção estiver vazia, apenas mencione que precisa de mais coleta ali.\n\n"
        f"DADOS COLETADOS (JSON):\n{json.dumps(dados, ensure_ascii=False, default=str)}"
    )
    corpo = json.dumps({
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=corpo, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": chave, "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            resposta = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", f"HTTP {e.code}")
        except Exception:
            msg = f"HTTP {e.code}"
        return {"erro": f"Erro da API Anthropic: {msg}"}
    except Exception as e:
        return {"erro": f"Erro de conexão com a API: {e}"}

    texto = "".join(b.get("text", "") for b in resposta.get("content", []) if b.get("type") == "text")
    return {"texto": texto}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.1] MAPA DIGITAL KING STAR — processos (IT × como é feito hoje)
# Rotas genéricas: o servidor não conhece os campos do processo. Campo
# novo = só mexer no mapa.html.
# ──────────────────────────────────────────────────────────────────────────
class ProcessoEntrada(BaseModel):
    dados: Any = None
    versao: Optional[int] = None


def _nome_de(request: Request) -> str:
    u = usuario_atual(request)
    return u.get("nome") or u.get("email")


def _checar_tamanho(dados):
    tamanho = len(json.dumps(dados, ensure_ascii=False, default=str).encode("utf-8"))
    if tamanho > _LIMITE_SECAO_BYTES:
        raise HTTPException(status_code=413, detail=f"Este processo ficou com {tamanho // 1024} KB e passa do limite do Firestore (~1 MB).")


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.7] DIÁRIO DE GESTÃO — registros pessoais (log, cobranças,
# feedbacks, ATAs, plano do dia). O servidor não conhece os campos: campo
# novo = só mexer no diario.html. Cada pessoa só lê/altera o que é dela.
# ──────────────────────────────────────────────────────────────────────────
def _dg_meu(request: Request, item_id: str) -> None:
    dono = db.dg_dono(item_id)
    if dono is None:
        raise HTTPException(status_code=404, detail="Registro não encontrado (pode ter sido excluído).")
    if dono != _email(request):
        raise HTTPException(status_code=403, detail="Este registro é do diário de outra pessoa.")


@app.get("/api/diario/itens")
def dg_listar(request: Request):
    return db.dg_listar(_email(request))


@app.post("/api/diario/itens")
def dg_criar(payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict) or not payload.dados.get("_tipo"):
        raise HTTPException(status_code=400, detail="Registro do diário inválido.")
    _checar_tamanho(payload.dados)
    return db.docs_criar(db.COLECAO_DIARIO_GESTAO, payload.dados, _nome_de(request), _email(request))


@app.put("/api/diario/itens/{item_id}")
def dg_salvar(item_id: str, payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Registro do diário inválido.")
    _checar_tamanho(payload.dados)
    _dg_meu(request, item_id)
    resultado, registro = db.docs_salvar(db.COLECAO_DIARIO_GESTAO, item_id, payload.dados, payload.versao, _nome_de(request))
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Registro não encontrado (pode ter sido excluído).")
    if resultado == "conflito":
        return JSONResponse({"detail": "conflito", "atual": registro}, status_code=409)
    return registro


# [NOVO v3.8] RITMO DO DIA ↔ INVENTÁRIO DO DIAGNÓSTICO
# O Ritmo do dia é montado com as atividades do setor da pessoa (o Inventário
# do Diagnóstico). Atividade nova criada no Ritmo entra no Inventário.
def _sem_acento(t) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", str(t or "")).encode("ascii", "ignore").decode()
    return " ".join(t.lower().split())


@app.get("/api/diario/atividades-setor")
def dg_atividades_setor(request: Request):
    u = usuario_atual(request)
    email, nome = (u.get("email") or "").lower(), _sem_acento(u.get("nome"))
    saida = []
    for d in _setores_do(u):
        dados = d.get("dados") or {}
        pessoas = dados.get("pessoas") or []
        meu = any((email and (p.get("email") or "").lower() == email) or (nome and _sem_acento(p.get("nome")) == nome) for p in pessoas)
        inv = db.diagdep_ler(d["id"], "inventario") or []
        saida.append({
            "id": d["id"], "nome": (dados.get("nome") or "").strip(), "meu": meu,
            "subsetores": sorted({(x or "").strip() for x in (dados.get("subsetores") or [])} | {(p.get("subsetor") or "").strip() for p in pessoas} - {""}),
            "atividades": [{"atividade": (i.get("atividade") or "").strip(), "subsetor": (i.get("subsetor") or "").strip(), "categoria": i.get("categoria") or ""}
                           for i in inv if isinstance(i, dict) and (i.get("atividade") or "").strip()],
        })
    return {"departamentos": saida}


class AtividadeNova(BaseModel):
    dep: str
    atividade: str
    subsetor: str = ""


@app.post("/api/diario/atividades-setor")
def dg_nova_atividade(payload: AtividadeNova, request: Request):
    u = usuario_atual(request)
    nome_at = " ".join((payload.atividade or "").split())[:200]
    if not nome_at:
        raise HTTPException(status_code=400, detail="Informe o nome da atividade.")
    if payload.dep not in {d["id"] for d in _setores_do(u)}:
        raise HTTPException(status_code=403, detail="Você não tem acesso a este departamento.")
    inv = db.diagdep_ler(payload.dep, "inventario") or []
    if not isinstance(inv, list):
        inv = []
    if any(isinstance(i, dict) and _sem_acento(i.get("atividade")) == _sem_acento(nome_at) for i in inv):
        return {"criada": False}
    inv.append({"atividade": nome_at, "subsetor": (payload.subsetor or "").strip()[:120], "categoria": "",
                "descricao": "Incluída pelo Ritmo do dia (Diário de Bordo)", "registrada": "", "_por": _email(request)})
    inv.sort(key=lambda i: _sem_acento((i or {}).get("atividade")))
    db.diagdep_salvar(payload.dep, "inventario", inv, _nome_de(request))
    return {"criada": True}


@app.post("/api/diario/itens/{item_id}/excluir")
def dg_excluir(item_id: str, request: Request):
    # POST (e não DELETE) de propósito: no diário pessoal, quem tem perfil
    # "Adicionar" também pode apagar o que é seu.
    _dg_meu(request, item_id)
    db.docs_excluir(db.COLECAO_DIARIO_GESTAO, item_id, _nome_de(request))
    return {"sucesso": True}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v4.0] CARTEIRA DE ENCOMENDAS
#  · fotos do dia: a página lê as planilhas do BI, une por loja+pedido e envia
#    a foto compactada; o servidor só guarda (não conhece os campos)
#  · tratativas: classificação do pedido → motivo → setor responsável
#  · config: setores e motivos
# Importar e configurar exigem perfil "Editar" no módulo; classificar, "Adicionar".
# ──────────────────────────────────────────────────────────────────────────
class CarteiraDia(BaseModel):
    meta: dict
    envio: str
    n: int


class CarteiraParte(BaseModel):
    envio: str
    conteudo: str


def _exigir_editar_carteira(request: Request, aba: str = "importar") -> None:
    # [v4.16] agora pela aba: Importar → aba Importar · Configurações → aba Configurar (ou CX, que guarda a equipe)
    if aba == "config":
        _exigir_aba(request, "carteira", ["config", "cx"], 3, "Mudar as configurações da Carteira")
    else:
        _exigir_aba(request, "carteira", "importar", 3, "Importar a carteira")


@app.get("/api/carteira/dias")
def cart_dias(request: Request):
    return db.cart_dias_listar()


@app.get("/api/carteira/dias/{data}")
def cart_dia(data: str, request: Request):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        raise HTTPException(status_code=400, detail="Data inválida.")
    r = db.cart_dia_ler(data)
    if r is None:
        raise HTTPException(status_code=404, detail="Não há carteira importada nesse dia.")
    return r


def _validar_data_envio(data: str, envio: str) -> None:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        raise HTTPException(status_code=400, detail="Data inválida.")
    if not re.fullmatch(r"[a-z0-9]{6,24}", envio or ""):
        raise HTTPException(status_code=400, detail="Identificador de envio inválido.")


@app.post("/api/carteira/dias/{data}/partes/{ordem}")
def cart_parte_gravar(data: str, ordem: int, payload: CarteiraParte, request: Request):
    _exigir_editar_carteira(request)
    _validar_data_envio(data, payload.envio)
    if not 0 <= ordem < 60 or not payload.conteudo or len(payload.conteudo) > 300_000:
        raise HTTPException(status_code=400, detail="Parte da carteira com tamanho inválido.")
    db.cart_parte_salvar(data, payload.envio, ordem, payload.conteudo)
    return {"ok": True}


@app.post("/api/carteira/dias/{data}")
def cart_dia_gravar(data: str, payload: CarteiraDia, request: Request):
    _exigir_editar_carteira(request)
    _validar_data_envio(data, payload.envio)
    if not 1 <= payload.n <= 60:
        raise HTTPException(status_code=400, detail="Número de partes inválido.")
    if len(json.dumps(payload.meta, ensure_ascii=False)) > 200_000:
        raise HTTPException(status_code=400, detail="Resumo do dia grande demais.")
    try:
        return db.cart_dia_fechar(data, payload.meta, payload.envio, payload.n, _nome_de(request), _email(request))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/api/carteira/dias/{data}")
def cart_dia_excluir(data: str, request: Request):
    """[v4.2] Excluir uma importação errada — só administrador."""
    u = usuario_atual(request)
    if u.get("papel") != "admin":
        raise HTTPException(status_code=403, detail="Só o administrador pode excluir uma carteira importada.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        raise HTTPException(status_code=400, detail="Data inválida.")
    if not db.cart_dia_excluir(data):
        raise HTTPException(status_code=404, detail="Não há carteira importada nesse dia.")
    return {"ok": True, "excluido": data, "por": _nome_de(request)}


_CHAVE_PEDIDO = re.compile(r"[0-9A-Za-z]{1,6}-[0-9A-Za-z]{1,20}(\|[0-9A-Za-z_]{1,30})?")   # [v4.17] + reserva por área: "loja-pedido|cx"


def _reserva_de_outro(chave: str, request: Request) -> Optional[dict]:
    r = db.cart_reserva_ativa(chave) if _CHAVE_PEDIDO.fullmatch(chave or "") else None
    return r if r and (r.get("email") or "").lower() != _email(request).lower() else None


_RES_CACHE: dict = {"itens": None}   # [v4.18] reservas em memória (quem está tratando o quê) — a trava continua no Firestore


def _res_lista() -> list:
    if _RES_CACHE["itens"] is None:
        _RES_CACHE["itens"] = {r.get("chave"): r for r in db.cart_reservas_ativas() if r.get("chave")}
    agora = datetime.utcnow().isoformat()
    return [r for r in _RES_CACHE["itens"].values() if str(r.get("expira") or "") > agora]


@app.get("/api/carteira/reservas")
def cart_reservas(request: Request):
    return _res_lista()


@app.post("/api/carteira/reservas/{chave}")
def cart_reservar(chave: str, request: Request):
    """[v4.4] Pega o pedido para si. 409 se outra pessoa já está com ele."""
    if not _CHAVE_PEDIDO.fullmatch(chave):
        raise HTTPException(status_code=400, detail="Pedido inválido.")
    ok, reg = db.cart_reservar(chave, _nome_de(request), _email(request))   # a regra "só um trata" é garantida aqui (transação no Firestore)
    if _RES_CACHE["itens"] is not None and reg:
        _RES_CACHE["itens"][chave] = reg
    if not ok:
        return JSONResponse({"detail": f"Este pedido já está sendo tratado por {reg.get('por') or reg.get('email')}.", "reserva": reg}, status_code=409)
    return reg


@app.post("/api/carteira/reservas/{chave}/liberar")
def cart_liberar(chave: str, request: Request):
    if not _CHAVE_PEDIDO.fullmatch(chave):
        raise HTTPException(status_code=400, detail="Pedido inválido.")
    ok = db.cart_liberar(chave, _email(request), usuario_atual(request).get("papel") == "admin")
    if ok and _RES_CACHE["itens"] is not None:
        _RES_CACHE["itens"].pop(chave, None)
    return {"liberado": ok}


# ──────────────────────────────────────────────────────────────────────────
# [v4.18] CACHE DAS LISTAS DA CARTEIRA — economia do plano grátis do Firestore
# A lista inteira (tratativas, CX × Compras) é lida do Firestore UMA vez quando o
# servidor liga; depois, toda gravação passa por este servidor e atualiza a lista
# em memória. Assim, abrir a Carteira ou atualizar a tela não gasta leitura.
# (Vale com UM processo do servidor — que é o caso do Render com uvicorn.)
# ──────────────────────────────────────────────────────────────────────────
class _CacheColecao:
    def __init__(self, colecao: str):
        self.colecao, self.itens, self.trava = colecao, None, threading.Lock()

    def listar(self) -> list:
        with self.trava:
            if self.itens is None:
                self.itens = {d["id"]: d for d in db.docs_listar(self.colecao)}
            return list(self.itens.values())

    def obter(self, doc_id: str):
        self.listar()
        return self.itens.get(doc_id)

    def por_chave(self, chave: str):
        return next((d for d in self.listar() if (d.get("dados") or {}).get("chave") == chave), None)

    def gravar(self, reg) -> None:
        if self.itens is not None and isinstance(reg, dict) and reg.get("id"):
            self.itens[reg["id"]] = reg

    def esquecer(self) -> None:
        self.itens = None


_CACHE_TRAT = _CacheColecao(db.COLECAO_CART_TRATATIVAS)


@app.post("/api/carteira/recarregar")
def cart_recarregar(request: Request):
    """[v4.18] Relê as listas do Firestore (use só se alguém mexeu direto no banco)."""
    _CACHE_TRAT.esquecer()
    _CACHE_INTER.esquecer()
    return {"sucesso": True}


@app.get("/api/carteira/tratativas")
def cart_tratativas(request: Request):
    return _CACHE_TRAT.listar()


@app.post("/api/carteira/tratativas")
def cart_tratativa_criar(payload: ProcessoEntrada, request: Request):
    _exigir_aba(request, "carteira", ["triagem", "filas", "solucoes", "carteira", "importar"], 2, "Classificar pedidos")   # [v4.16]
    if not isinstance(payload.dados, dict) or not payload.dados.get("chave"):
        raise HTTPException(status_code=400, detail="Tratativa inválida.")
    _checar_tamanho(payload.dados)
    outro = _reserva_de_outro(payload.dados["chave"], request)
    if outro:
        raise HTTPException(status_code=423, detail=f"Este pedido está sendo tratado por {outro.get('por') or outro.get('email')}.")
    reg = db.docs_criar(db.COLECAO_CART_TRATATIVAS, payload.dados, _nome_de(request), _email(request))
    _CACHE_TRAT.gravar(reg)
    db.cart_liberar(payload.dados["chave"], _email(request))   # classificou: devolve o pedido
    return reg


@app.put("/api/carteira/tratativas/{item_id}")
def cart_tratativa_salvar(item_id: str, payload: ProcessoEntrada, request: Request):
    _exigir_aba(request, "carteira", ["triagem", "filas", "solucoes", "carteira", "importar"], 2, "Classificar pedidos")   # [v4.16]
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Tratativa inválida.")
    _checar_tamanho(payload.dados)
    outro = _reserva_de_outro(payload.dados.get("chave") or "", request)
    if outro:
        raise HTTPException(status_code=423, detail=f"Este pedido está sendo tratado por {outro.get('por') or outro.get('email')}.")
    resultado, registro = db.docs_salvar(db.COLECAO_CART_TRATATIVAS, item_id, payload.dados, payload.versao, _nome_de(request))
    _CACHE_TRAT.gravar(registro)   # [v4.18] (no conflito, devolve o atual — também atualiza a lista)
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Tratativa não encontrada.")
    if resultado == "conflito":
        return JSONResponse({"detail": "conflito", "atual": registro}, status_code=409)
    return registro


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v4.15] CX × COMPRAS — o "pingue-pongue" de cada pedido.
# Um documento por pedido (loja+pedido) na coleção "carteira_interacoes":
# devolutiva do CX, cobrança/previsão do Compras por item, produto alternativo
# e os pedidos que uma área faz para a outra. Fica SEPARADO da tratativa da
# triagem de propósito: CX e Compras gravam sem esperar a reserva da triagem.
# ──────────────────────────────────────────────────────────────────────────
COLECAO_CART_INTERACOES = "carteira_interacoes"
_CACHE_INTER = _CacheColecao(COLECAO_CART_INTERACOES)   # [v4.18]


def _inter_criar(dados: dict, request: Request):
    """[v4.18] Cria com id FIXO por pedido ("i_loja-pedido"): se duas pessoas criarem ao mesmo
    tempo, o Firestore aceita só a primeira — sem precisar ler a coleção inteira para conferir."""
    agora = datetime.now().isoformat()
    doc_id = "i_" + re.sub(r"[^0-9A-Za-z_-]", "_", str(dados["chave"]))
    reg = {"dados": dados, "versao": 1, "excluido": False, "criado_por": _nome_de(request), "criado_por_email": _email(request),
           "criado_em": agora, "atualizado_por": _nome_de(request), "atualizado_em": agora}
    try:
        db.get_db().collection(COLECAO_CART_INTERACOES).document(doc_id).create(reg, timeout=db.TIMEOUT_FS)
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            return None, doc_id
        raise
    publico = {"id": doc_id, "dados": dados, "versao": 1, "atualizado_por": reg["atualizado_por"], "atualizado_em": agora,
               "criado_por": reg["criado_por"], "criado_em": agora}
    return publico, doc_id


def _conferir_interacao(request: Request, antes: dict, novo: dict) -> None:
    """[v4.16] Quem mexe na parte do CX precisa da aba CX; na parte do Compras, da aba Compras.
    Apagar algo que já estava registrado exige “Excluir” na aba daquela parte."""
    j = lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)
    antes, precisa = antes or {}, {}
    def pede(aba, n):
        precisa[aba] = max(precisa.get(aba, 0), n)
    if j(antes.get("cx")) != j(novo.get("cx")) or j(antes.get("cx_resp")) != j(novo.get("cx_resp")) or len(novo.get("cx_hist") or []) > len(antes.get("cx_hist") or []):
        pede("cx", 2)
    if len(novo.get("cx_hist") or []) < len(antes.get("cx_hist") or []):
        pede("cx", 4)
    ca, cn = antes.get("compras") or {}, novo.get("compras") or {}
    if j(ca) != j(cn):
        pede("compras", 4 if any(k not in cn for k in ca) else 2)
    aa = {a.get("id"): a for a in antes.get("alternativos") or []}
    an = {a.get("id"): a for a in novo.get("alternativos") or []}
    for i, a in an.items():
        if i not in aa:
            pede("compras", 2)
        elif j(a) != j(aa[i]):
            pede("cx" if a.get("status") != aa[i].get("status") else "compras", 2)
    if any(i not in an for i in aa):
        pede("compras", 4)
    # [v4.20] "Resolver" da Triagem: cada área grava só a sua tabulação
    # [v4.22] qualquer setor: res_<setor> é a tabulação daquele setor
    for campo in {k for k in list(antes) + list(novo) if k.startswith("res_") and k != "res_hist"}:
        if j(antes.get(campo)) != j(novo.get(campo)):
            pede(campo[4:], 4 if antes.get(campo) and not novo.get(campo) else 2)
    ha, hn = antes.get("res_hist") or [], novo.get("res_hist") or []
    if len(hn) < len(ha):
        pede("cx", 4)
    for x in hn[len(ha):]:
        if re.fullmatch(r"[0-9A-Za-z_]{1,30}", str(x.get("area") or "")):
            pede(str(x.get("area")), 2)
    sa = {x.get("id"): x for x in antes.get("solicitacoes") or []}
    sn = {x.get("id"): x for x in novo.get("solicitacoes") or []}
    for i, x in sn.items():
        if i not in sa:
            pede(x.get("de") if x.get("de") in ("cx", "compras") else "cx", 2)
        elif x.get("status") == "encerrada" and sa[i].get("status") == "aberta":
            pede("importar", 3)   # [v4.18] encerrado pela planilha na importação
        elif j(x) != j(sa[i]):
            quem = x.get("para") if x.get("status") == "respondida" else x.get("de")
            pede(quem if quem in ("cx", "compras") else "cx", 2)
    for i, x in sa.items():
        if i not in sn:
            pede(x.get("de") if x.get("de") in ("cx", "compras") else "cx", 4)
    for aba, n in precisa.items():
        if aba == "importar":
            _exigir_aba(request, "carteira", "importar", n, "Encerrar pela planilha")
            continue
        _nome = {"cx": "CX", "compras": "Compras", "logistica": "Logística"}.get(aba) or next((a.get("nome") for a in mods.abas_de("carteira") if a.get("id") == aba), "setor")
        _exigir_aba(request, "carteira", aba, n, "Gravar na parte do " + _nome)
        # [v4.17] quem abriu primeiro o pedido nesta área é quem trata — o outro espera ele sair
        r = db.cart_reserva_ativa(f"{novo.get('chave') or antes.get('chave')}|{aba}")
        if r and (r.get("email") or "").lower() != _email(request).lower():
            raise HTTPException(status_code=423, detail=f"Não é possível gravar: {r.get('por') or r.get('email')} já está tratando este pedido no {_nome}.")


@app.get("/api/carteira/interacoes")
def cart_interacoes(request: Request):
    return _CACHE_INTER.listar()


@app.post("/api/carteira/interacoes")
def cart_interacao_criar(payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict) or not payload.dados.get("chave"):
        raise HTTPException(status_code=400, detail="Registro inválido.")
    _checar_tamanho(payload.dados)
    _conferir_interacao(request, {}, payload.dados)   # [v4.16]
    ja = _CACHE_INTER.por_chave(payload.dados["chave"])
    if ja:   # duas pessoas abriram o mesmo pedido ao mesmo tempo: devolve o que já existe para a tela juntar
        return JSONResponse({"detail": "conflito", "atual": ja}, status_code=409)
    reg, doc_id = _inter_criar(payload.dados, request)
    if reg is None:
        atual = db.docs_ler(COLECAO_CART_INTERACOES, doc_id)
        _CACHE_INTER.gravar(atual)
        return JSONResponse({"detail": "conflito", "atual": atual}, status_code=409)
    _CACHE_INTER.gravar(reg)
    return reg


@app.put("/api/carteira/interacoes/{item_id}")
def cart_interacao_salvar(item_id: str, payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Registro inválido.")
    _checar_tamanho(payload.dados)
    atual = _CACHE_INTER.obter(item_id) or db.docs_ler(COLECAO_CART_INTERACOES, item_id)   # [v4.16] confere o que mudou (CX × Compras)
    if atual:
        _conferir_interacao(request, atual.get("dados") or {}, payload.dados)
    resultado, registro = db.docs_salvar(COLECAO_CART_INTERACOES, item_id, payload.dados, payload.versao, _nome_de(request))
    _CACHE_INTER.gravar(registro)   # [v4.18]
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    if resultado == "conflito":
        return JSONResponse({"detail": "conflito", "atual": registro}, status_code=409)
    return registro


@app.get("/api/carteira/config")
def cart_config(request: Request):
    lst = db.docs_listar(db.COLECAO_CART_CONFIG)
    if lst:
        _abas_setores((lst[0] or {}).get("dados"))
    return lst[0] if lst else None


@app.post("/api/carteira/config")
def cart_config_criar(payload: ProcessoEntrada, request: Request):
    _exigir_editar_carteira(request, "config")
    if db.docs_listar(db.COLECAO_CART_CONFIG):
        raise HTTPException(status_code=409, detail="A configuração já existe — atualize em vez de criar.")
    _abas_setores(payload.dados)
    return db.docs_criar(db.COLECAO_CART_CONFIG, payload.dados or {}, _nome_de(request), _email(request))


@app.put("/api/carteira/config/{item_id}")
def cart_config_salvar(item_id: str, payload: ProcessoEntrada, request: Request):
    _exigir_editar_carteira(request, "config")
    _checar_tamanho(payload.dados)
    resultado, registro = db.docs_salvar(db.COLECAO_CART_CONFIG, item_id, payload.dados, payload.versao, _nome_de(request))
    if resultado == "conflito":
        return JSONResponse({"detail": "conflito", "atual": registro}, status_code=409)
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Configuração não encontrada.")
    _abas_setores(payload.dados)
    return registro


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v4.1] CARTAS DE DÉBITO (veio do Streamlit — cartas.py)
#  · carta: NOME, CPF, COD_CLI, VALOR, LOJA, DATA, MOTIVO, status, id_lote, anexo
#    status: "Aguardando Assinatura" → "CARTA RECEBIDA" (subiu a assinada) → "LOTE_FECHADO"
#  · o Word é preenchido NO NAVEGADOR a partir do modelo carta_preenchida.docx
#  · a carta assinada fica no banco de arquivos (db.arquivos_salvar)
# Níveis: Visualizar = consulta e baixa o Word · Adicionar = cria carta, cadastra
# colaborador, sobe a assinada e edita a carta que ele mesmo criou ·
# Editar = edita/exclui qualquer carta, fecha lote, exporta, reabre, apaga anexos.
# ──────────────────────────────────────────────────────────────────────────
COLECAO_CARTAS = "cartas_debito"
COLECAO_CARTAS_COLAB = "cartas_colaboradores"
COLECAO_CARTAS_LOTES = "cartas_lotes"
CARTA_AGUARDANDO, CARTA_RECEBIDA, CARTA_FECHADA = "Aguardando Assinatura", "CARTA RECEBIDA", "LOTE_FECHADO"
MODELO_CARTA = "carta_preenchida.docx"


class CartaEntrada(BaseModel):
    NOME: str
    CPF: str
    COD_CLI: str
    VALOR: float = 0
    LOJA: str = ""
    DATA: str = ""
    MOTIVO: str = ""
    versao: Optional[int] = None


class ColaboradorCarta(BaseModel):
    nome: str
    cpf: str


class ColaboradoresImportar(BaseModel):
    linhas: List[ColaboradorCarta]


class LoteFechar(BaseModel):
    ids: List[str]


def _nivel_cartas(request: Request) -> int:
    return _niveis(usuario_atual(request)).get("cartas", 0)


def _exigir_editar_cartas(request: Request, acao: str) -> None:
    if _nivel_cartas(request) < perm.EDITAR:
        raise HTTPException(status_code=403, detail=f"{acao} exige o perfil “Editar” no módulo Cartas de Débito.")


def _limpa_carta(p: CartaEntrada) -> dict:
    nome, cpf, cod = " ".join(p.NOME.upper().split()), p.CPF.strip(), p.COD_CLI.strip()
    if not nome or not cpf or not cod:
        raise HTTPException(status_code=400, detail="Preencha Nome, CPF e Código do Cliente.")
    if not re.fullmatch(r"(\d{2}/\d{2}/\d{4})?", p.DATA.strip()):
        raise HTTPException(status_code=400, detail="Data inválida (use dd/mm/aaaa).")
    if p.VALOR < 0 or p.VALOR > 10_000_000:
        raise HTTPException(status_code=400, detail="Valor inválido.")
    return {"NOME": nome, "CPF": cpf, "COD_CLI": cod, "VALOR": round(float(p.VALOR), 2),
            "LOJA": " ".join(p.LOJA.upper().split()), "DATA": p.DATA.strip(), "MOTIVO": p.MOTIVO.strip().upper()[:2000]}


def _carta_ou_erro(carta_id: str) -> dict:
    c = db.docs_ler(COLECAO_CARTAS, carta_id)
    if not c:
        raise HTTPException(status_code=404, detail="Carta não encontrada (pode ter sido excluída).")
    c.setdefault("id", carta_id)
    return c


def _salvar_carta(request: Request, c: dict, dados: dict, evento: str):
    """Grava a carta inteira (com o evento no histórico); 409 se outra pessoa mexeu antes."""
    dados = dict(dados)
    dados["historico"] = list(dados.get("historico") or [])[-40:] + [{"em": datetime.now().isoformat(), "por": _nome_de(request), "acao": evento}]
    resultado, registro = db.docs_salvar(COLECAO_CARTAS, c["id"], dados, c.get("versao"), _nome_de(request))
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Carta não encontrada (pode ter sido excluída).")
    if resultado == "conflito":
        raise HTTPException(status_code=409, detail="Outra pessoa alterou esta carta agora — a tela vai recarregar, confira e repita.")
    return registro


def _upsert_colaborador(request: Request, nome: str, cpf: str, base: Optional[dict] = None) -> str:
    """Cadastra o colaborador (ou atualiza o CPF). `base` = {NOME: doc} já lido, para importar em lote sem reler o banco."""
    nome, cpf = " ".join((nome or "").upper().split()), (cpf or "").strip()
    if not nome or not cpf:
        return "ignorado"
    if base is None:
        base = {((d.get("dados") or {}).get("nome") or "").upper(): d for d in db.docs_listar(COLECAO_CARTAS_COLAB)}
    d = base.get(nome)
    if d:
        dados = d.get("dados") or {}
        if dados.get("cpf") == cpf:
            return "igual"
        resultado, reg = db.docs_salvar(COLECAO_CARTAS_COLAB, d["id"], {**dados, "cpf": cpf}, d.get("versao"), _nome_de(request))
        if isinstance(reg, dict):
            base[nome] = reg
        return "atualizado"
    reg = db.docs_criar(COLECAO_CARTAS_COLAB, {"nome": nome, "cpf": cpf}, _nome_de(request), _email(request))
    base[nome] = reg if isinstance(reg, dict) else {"id": "", "dados": {"nome": nome, "cpf": cpf}}
    return "novo"


def _apagar_arquivo(arquivo_id: str) -> None:
    """Apaga o arquivo da carta assinada do banco de arquivos (se o db_diario tiver a função)."""
    for nome in ("arquivos_excluir", "arquivos_apagar", "arquivos_deletar"):
        f = getattr(db, nome, None)
        if f and arquivo_id:
            try:
                f(arquivo_id)
            except Exception as e:
                print(f"[AVISO] não consegui apagar o arquivo {arquivo_id}: {e!r}")
            return


@app.get("/api/cartas")
def cartas_tudo(request: Request):
    return {"cartas": db.docs_listar(COLECAO_CARTAS), "colaboradores": db.docs_listar(COLECAO_CARTAS_COLAB),
            "lotes": db.docs_listar(COLECAO_CARTAS_LOTES), "nivel": _nivel_cartas(request),
            "abas": _abas(usuario_atual(request)).get("cartas", {}),
            "limite_arquivo": db.LIMITE_ARQUIVO_BYTES}


@app.get("/api/cartas/modelo", include_in_schema=False)
def cartas_modelo():
    caminho = os.path.join(BASE_DIR, MODELO_CARTA)
    if not os.path.exists(caminho):
        raise HTTPException(status_code=404, detail=f"Modelo {MODELO_CARTA} não encontrado no repositório (mesma pasta do main.py).")
    return FileResponse(caminho, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Cache-Control": "no-cache"})


@app.post("/api/cartas")
def cartas_criar(payload: CartaEntrada, request: Request):
    _exigir_aba(request, "cartas", "nova", 2, "Registrar carta")   # [v4.16]
    dados = _limpa_carta(payload)
    dados.update(status=CARTA_AGUARDANDO, id_lote="", anexo=None,
                 historico=[{"em": datetime.now().isoformat(), "por": _nome_de(request), "acao": "Carta registrada"}])
    _upsert_colaborador(request, dados["NOME"], dados["CPF"])
    return db.docs_criar(COLECAO_CARTAS, dados, _nome_de(request), _email(request))


@app.put("/api/cartas/{carta_id}")
def cartas_editar(carta_id: str, payload: CartaEntrada, request: Request):
    c = _carta_ou_erro(carta_id)
    # [v4.16] Editar na aba Painel/Fechamento — ou Adicionar, sendo quem registrou a carta
    dono = c.get("criado_por_email") and c.get("criado_por_email") == _email(request)
    _exigir_aba(request, "cartas", ["painel", "fechamento"], 2 if dono else 3, "Editar carta")
    atual = c.get("dados") or {}
    if atual.get("status") == CARTA_FECHADA:
        raise HTTPException(status_code=400, detail="Esta carta já está num lote fechado. Reabra antes de editar.")
    if payload.versao is not None:
        c["versao"] = payload.versao
    novos = _limpa_carta(payload)
    mudou = [k for k, v in novos.items() if atual.get(k) != v]
    _upsert_colaborador(request, novos["NOME"], novos["CPF"])
    return _salvar_carta(request, c, {**atual, **novos}, "Editou: " + (", ".join(mudou) if mudou else "sem mudanças"))


@app.delete("/api/cartas/{carta_id}")
def cartas_excluir(carta_id: str, request: Request):
    _exigir_aba(request, "cartas", "painel", 4, "Excluir carta")   # [v4.16]
    c = _carta_ou_erro(carta_id)
    if (c.get("dados") or {}).get("status") == CARTA_FECHADA:
        raise HTTPException(status_code=400, detail="Carta de lote fechado não pode ser excluída — reabra antes.")
    _apagar_arquivo(((c.get("dados") or {}).get("anexo") or {}).get("arquivo_id"))
    db.docs_excluir(COLECAO_CARTAS, carta_id, _nome_de(request))
    return {"sucesso": True}


@app.post("/api/cartas/{carta_id}/assinatura")
async def cartas_assinatura(carta_id: str, request: Request, arquivo: UploadFile = File(...)):
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")
    if len(conteudo) > db.LIMITE_ARQUIVO_BYTES:
        raise HTTPException(status_code=413, detail=f"Arquivo de {len(conteudo)//1024} KB passa do limite de {db.LIMITE_ARQUIVO_BYTES//1024} KB. "
                                                    "Foto: a tela reduz sozinha; PDF: salve em qualidade menor.")

    _exigir_aba(request, "cartas", ["painel", "fechamento"], 2, "Enviar a carta assinada")   # [v4.16]

    def gravar():
        c = _carta_ou_erro(carta_id)
        dados = dict(c.get("dados") or {})
        if dados.get("status") == CARTA_FECHADA:
            raise HTTPException(status_code=400, detail="Esta carta já está num lote fechado.")
        antigo = (dados.get("anexo") or {}).get("arquivo_id")
        novo_id = db.arquivos_salvar(arquivo.filename or "carta_assinada", arquivo.content_type, conteudo)
        dados["anexo"] = {"arquivo_id": novo_id, "nome": arquivo.filename or "carta_assinada", "tipo": arquivo.content_type,
                          "tamanho": len(conteudo), "por": _nome_de(request), "em": datetime.now().isoformat()}
        dados["status"] = CARTA_RECEBIDA
        dados["recebida_em"] = datetime.now().isoformat()
        reg = _salvar_carta(request, c, dados, f"Carta assinada recebida ({arquivo.filename})")
        if antigo and antigo != novo_id:
            _apagar_arquivo(antigo)
        return reg
    return await run_in_threadpool(gravar)


@app.get("/api/cartas/{carta_id}/assinatura")
def cartas_assinatura_baixar(carta_id: str):
    c = _carta_ou_erro(carta_id)
    anexo = (c.get("dados") or {}).get("anexo") or {}
    arq = db.arquivos_ler(anexo.get("arquivo_id")) if anexo.get("arquivo_id") and not anexo.get("removido") else None
    if not arq:
        raise HTTPException(status_code=404, detail="Esta carta não tem o arquivo assinado guardado.")
    nome = (anexo.get("nome") or arq.get("nome") or "carta_assinada").replace('"', "")
    return Response(content=bytes(arq.get("conteudo") or b""), media_type=arq.get("tipo") or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.post("/api/cartas/{carta_id}/reabrir")
def cartas_reabrir(carta_id: str, request: Request):
    _exigir_aba(request, "cartas", "historico", 3, "Reabrir carta")   # [v4.16]
    c = _carta_ou_erro(carta_id)
    dados = dict(c.get("dados") or {})
    lote_ant = dados.get("id_lote") or ""
    dados.update(status=CARTA_AGUARDANDO, id_lote="")
    return _salvar_carta(request, c, dados, f"Reaberta (estava no lote {lote_ant or '—'})")


@app.post("/api/cartas/lotes")
def cartas_fechar_lote(payload: LoteFechar, request: Request):
    _exigir_aba(request, "cartas", "fechamento", 3, "Fechar o lote")   # [v4.16]
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Nenhuma carta para fechar.")
    cartas = []
    for cid in dict.fromkeys(payload.ids):
        c = _carta_ou_erro(cid)
        if (c.get("dados") or {}).get("status") != CARTA_RECEBIDA:
            raise HTTPException(status_code=409, detail=f"A carta de {(c.get('dados') or {}).get('NOME', cid)} não está mais como “CARTA RECEBIDA”. Recarregue a tela.")
        cartas.append(c)
    agora = datetime.now()
    codigo = agora.strftime("L%Y%m%d-%H%M%S")
    lote = db.docs_criar(COLECAO_CARTAS_LOTES, {
        "codigo": codigo, "data": agora.strftime("%d/%m/%Y %H:%M"), "fechado_em": agora.isoformat(),
        "fechado_por": _nome_de(request), "total": len(cartas),
        "valor_total": round(sum(float((c.get("dados") or {}).get("VALOR") or 0) for c in cartas), 2),
        "ids_cartas": [c["id"] for c in cartas]}, _nome_de(request), _email(request))
    for c in cartas:
        dados = dict(c.get("dados") or {})
        dados.update(status=CARTA_FECHADA, id_lote=codigo)
        _salvar_carta(request, c, dados, f"Lote {codigo} fechado")
    return {"sucesso": True, "codigo": codigo, "lote": lote}


@app.post("/api/cartas/lotes/{lote_id}/limpar-anexos")
def cartas_limpar_anexos(lote_id: str, request: Request):
    _exigir_aba(request, "cartas", "historico", 4, "Excluir os arquivos do lote")   # [v4.16]
    lote = db.docs_ler(COLECAO_CARTAS_LOTES, lote_id)
    if not lote:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    limpas = 0
    for cid in (lote.get("dados") or {}).get("ids_cartas") or []:
        c = db.docs_ler(COLECAO_CARTAS, cid)
        anexo = ((c or {}).get("dados") or {}).get("anexo") or {}
        if not c or not anexo.get("arquivo_id") or anexo.get("removido"):
            continue
        c.setdefault("id", cid)
        _apagar_arquivo(anexo["arquivo_id"])
        dados = dict(c.get("dados") or {})
        dados["anexo"] = {**anexo, "removido": True, "removido_em": datetime.now().isoformat(), "removido_por": _nome_de(request)}
        _salvar_carta(request, c, dados, "Arquivo assinado excluído do banco (o registro continua)")
        limpas += 1
    return {"sucesso": True, "limpas": limpas}


@app.post("/api/cartas/colaboradores")
def cartas_colab_salvar(payload: ColaboradorCarta, request: Request):
    _exigir_aba(request, "cartas", "config", 2, "Cadastrar colaborador")   # [v4.16]
    r = _upsert_colaborador(request, payload.nome, payload.cpf)
    if r == "ignorado":
        raise HTTPException(status_code=400, detail="Preencha nome e CPF.")
    return {"sucesso": True, "resultado": r}


@app.post("/api/cartas/colaboradores/importar")
def cartas_colab_importar(payload: ColaboradoresImportar, request: Request):
    _exigir_aba(request, "cartas", "config", 3, "Importar colaboradores")   # [v4.16]
    if len(payload.linhas) > 5000:
        raise HTTPException(status_code=400, detail="Máximo de 5.000 linhas por importação.")
    cont: dict = {}
    base = {((d.get("dados") or {}).get("nome") or "").upper(): d for d in db.docs_listar(COLECAO_CARTAS_COLAB)}
    for l in payload.linhas:
        r = _upsert_colaborador(request, l.nome, l.cpf, base)
        cont[r] = cont.get(r, 0) + 1
    return {"sucesso": True, **cont}


@app.delete("/api/cartas/colaboradores/{colab_id}")
def cartas_colab_excluir(colab_id: str, request: Request):
    _exigir_aba(request, "cartas", "config", 4, "Remover colaborador")   # [v4.16]
    if not db.docs_excluir(COLECAO_CARTAS_COLAB, colab_id, _nome_de(request)):
        raise HTTPException(status_code=404, detail="Colaborador não encontrado.")
    return {"sucesso": True}


@app.get("/api/mapa/processos")
def mapa_listar():
    return db.mapa_listar()


@app.post("/api/mapa/processos")
def mapa_criar(payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Dados do processo inválidos.")
    _checar_tamanho(payload.dados)
    return db.mapa_criar(payload.dados, _nome_de(request), _email(request))


@app.put("/api/mapa/processos/{processo_id}")
def mapa_salvar(processo_id: str, payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Dados do processo inválidos.")
    _checar_tamanho(payload.dados)
    dados = _conferir_acrescimo(request, "mapa", db.COLECAO_MAPA, processo_id, payload.dados)
    resultado, registro = db.mapa_salvar(processo_id, dados, payload.versao, _nome_de(request))
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Processo não encontrado (pode ter sido excluído).")
    if resultado == "conflito":
        return JSONResponse({"detail": "conflito", "atual": registro}, status_code=409)
    return registro


# [NOVO v3.5] PONTE MAPA ← DIAGNÓSTICO: as atividades levantadas no inventário de
# cada departamento aparecem no Mapa para virar processo (com o subsetor responsável).
@app.get("/api/mapa/atividades-diagnostico")
def mapa_atividades_diagnostico(request: Request):
    u = usuario_atual(request)
    saida = []
    for d in _setores_do(u):
        inv = db.diagdep_ler(d["id"], "inventario") or []
        diario = db.diagdep_ler(d["id"], "diario") or []
        ativs = [{"atividade": (i.get("atividade") or "").strip(), "subsetor": (i.get("subsetor") or "").strip(),
                  "categoria": i.get("categoria") or "", "descricao": i.get("descricao") or "",
                  "executores": (i.get("executores") or "").strip(), "modo": i.get("modo") or "", "equipe_n": i.get("equipe_n") or "",
                  "diario": _resumo_diario_da_atividade(diario, i.get("atividade"))}
                 for i in inv if isinstance(i, dict) and (i.get("atividade") or "").strip()]
        if ativs:
            saida.append({"id": d["id"], "nome": ((d.get("dados") or {}).get("nome") or "").strip(), "atividades": ativs})
    return {"departamentos": saida}


# [NOVO v3.9] o que o Diário de Bordo já mediu de uma atividade: vai para o Mapa
def _resumo_diario_da_atividade(diario: list, atividade) -> dict:
    from datetime import date as _date
    alvo = _sem_acento(atividade)
    regs = [r for r in (diario or []) if isinstance(r, dict) and _sem_acento(r.get("atividade")) == alvo]
    if not regs:
        return {"registros": 0}
    mins = [int(r["duracao"]) for r in regs if str(r.get("duracao") or "").isdigit()]
    datas = sorted({str(r.get("data") or "")[:10] for r in regs if r.get("data")})
    try:
        dias = max(1, (_date.fromisoformat(datas[-1]) - _date.fromisoformat(datas[0])).days + 1) if datas else 1
    except ValueError:
        dias = 1
    contar = lambda campo: sorted({(r.get(campo) or "").strip() for r in regs} - {""})
    passos, vistos = [], set()
    for r in regs:   # na ordem em que foram observados, sem repetir
        for linha in str(r.get("passos") or "").splitlines():
            t = linha.strip().lstrip("-•*0123456789.) ").strip()
            if t and _sem_acento(t) not in vistos:
                vistos.add(_sem_acento(t)); passos.append({"texto": t, "quem": (r.get("analista") or "").strip()})
    return {"registros": len(regs), "tempo_medio_min": round(sum(mins) / len(mins)) if mins else None,
            "vezes_mes": round(len(regs) / dias * 30, 1), "dias_observados": len(datas),
            "analistas": contar("analista"), "sistemas": contar("sistemas"), "origens": contar("origem"), "passos": passos[:40]}


@app.delete("/api/mapa/processos/{processo_id}")
def mapa_excluir(processo_id: str, request: Request):
    if not db.mapa_excluir(processo_id, _nome_de(request)):
        raise HTTPException(status_code=404, detail="Processo não encontrado.")
    return {"sucesso": True}


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.2] ORGANOGRAMAS — um documento por setor (coleção própria).
# [v3.3] Cada setor é também um DEPARTAMENTO do Diagnóstico (mesmo id).
# ──────────────────────────────────────────────────────────────────────────
def _eh_config_niveis(dados) -> bool:
    return isinstance(dados, dict) and dados.get("_tipo") == "niveis"


@app.get("/api/organograma/setores")
def org_listar(request: Request):
    u = usuario_atual(request)
    return [d for d in db.docs_listar(COLECAO_ORGANOGRAMA) if _eh_config_niveis(d.get("dados")) or _dep_ok(u, d["id"])]


@app.get("/api/organograma/resumo-diagnostico")
def org_resumo_diagnostico(request: Request):
    """Andamento do diagnóstico de cada setor (só o checklist) — o Organograma mostra no card."""
    u = usuario_atual(request)
    return {dep: ck for dep, ck in db.diagdep_checklists().items() if dep and _dep_ok(u, dep)}


@app.post("/api/organograma/setores")
def org_criar(payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Dados do setor inválidos.")
    _checar_tamanho(payload.dados)
    u = usuario_atual(request)
    if _eh_config_niveis(payload.dados):
        if _niveis(u).get("organograma", 0) < perm.EDITAR:
            raise HTTPException(status_code=403, detail="Os níveis hierárquicos valem para todos os setores: só quem tem perfil “Editar” no Organograma altera.")
    elif _deps_permitidos(u) is not None:
        raise HTTPException(status_code=403, detail="Seu acesso é restrito a alguns departamentos — peça ao administrador para criar este setor.")
    return db.docs_criar(COLECAO_ORGANOGRAMA, payload.dados, _nome_de(request), u["email"])


@app.put("/api/organograma/setores/{setor_id}")
def org_salvar(setor_id: str, payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Dados do setor inválidos.")
    _checar_tamanho(payload.dados)
    u = usuario_atual(request)
    if _eh_config_niveis(payload.dados):
        if _niveis(u).get("organograma", 0) < perm.EDITAR:
            raise HTTPException(status_code=403, detail="Os níveis hierárquicos valem para todos os setores: só quem tem perfil “Editar” no Organograma altera.")
        dados = payload.dados
    else:
        if not _dep_ok(u, setor_id):
            raise HTTPException(status_code=403, detail="Você não tem acesso a este departamento.")
        dados = _conferir_acrescimo(request, "organograma", COLECAO_ORGANOGRAMA, setor_id, payload.dados)
    resultado, registro = db.docs_salvar(COLECAO_ORGANOGRAMA, setor_id, dados, payload.versao, _nome_de(request))
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Setor não encontrado (pode ter sido excluído).")
    if resultado == "conflito":
        return JSONResponse({"detail": "conflito", "atual": registro}, status_code=409)
    return registro


@app.delete("/api/organograma/setores/{setor_id}")
def org_excluir(setor_id: str, request: Request):
    if not _dep_ok(usuario_atual(request), setor_id):
        raise HTTPException(status_code=403, detail="Você não tem acesso a este departamento.")
    if not db.docs_excluir(COLECAO_ORGANOGRAMA, setor_id, _nome_de(request)):
        raise HTTPException(status_code=404, detail="Setor não encontrado.")
    return {"sucesso": True}


# ──────────────────────────────────────────────────────────────────────────
# [AJUSTADO v2.3] PÁGINAS HTML — o mesmo servidor agora entrega o Painel
# (túnel) e todos os sistemas que são arquivo HTML.
#
# É uma LISTA FIXA de propósito: só estes arquivos podem ser abertos pelo
# navegador. Nunca "abrir a pasta inteira" — senão qualquer pessoa poderia
# baixar o main.py, o db_diario.py ou um firestore_key.json esquecido.
#
# Pra adicionar um sistema novo em HTML: suba o arquivo no repositório e
# acrescente uma linha aqui.
# ──────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PAGINAS = {
    "/": "index.html",                                                   # Painel de Sistemas (túnel)
    "/diario": "diario.html",                                            # Diário de gestão [v3.7]
    "/ata": "ata.html",                                                  # Gerador de ATA King Star
    "/carteira": "carteira.html",                                        # Carteira de Encomendas [v4.0]
    "/cartas": "cartas.html",                                            # Cartas de Débito [v4.1]
    "/rg-pedido-acompanhamento.html": "rg-pedido-acompanhamento.html",   # RG do Pedido
    "/diagnostico": "diagnostico.html",                                  # Diagnóstico
    "/mapa": "mapa.html",
    "/organograma": "organograma.html",                                  # Organogramas                                                # Mapa Digital King Star
    "/admin": "admin.html",                                              # Usuários e Permissões (só admin)
}


def _criar_rota_pagina(nome_arquivo: str):
    def servir():
        caminho = os.path.join(BASE_DIR, nome_arquivo)
        if not os.path.exists(caminho):
            raise HTTPException(
                status_code=404,
                detail=f"{nome_arquivo} não encontrado no repositório (mesma pasta do main.py).",
            )
        # no-cache: depois de um commit novo, o navegador já pega a versão nova
        return FileResponse(caminho, media_type="text/html", headers={"Cache-Control": "no-cache"})
    return servir


@app.get("/pqi-acesso.js", include_in_schema=False)
def acesso_js():
    return FileResponse(os.path.join(BASE_DIR, "pqi-acesso.js"), media_type="application/javascript", headers={"Cache-Control": "no-cache"})


for _rota, _arquivo in PAGINAS.items():
    app.add_api_route(_rota, _criar_rota_pagina(_arquivo), methods=["GET", "HEAD"], include_in_schema=False)


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=(porta == 8000))
