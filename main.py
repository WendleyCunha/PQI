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
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, JSONResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator
from typing import Optional, List, Any

import db_diario as db
import seguranca as seg
import modulos as mods
import permissoes as perm


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
                  "/api/auth/login", "/api/auth/logout", "/api/auth/me"}
ROTAS_SO_ADMIN_PREFIXO = ("/api/admin/",)
ROTAS_SO_ADMIN = {"/admin", "/api/diag"}
ROTAS_QUALQUER_LOGADO = {"/api/auth/senha", "/pqi-acesso.js", "/api/base/cadastros"}  # [v3.4] cadastro mestre

_cache_usuarios: dict = {}
_CACHE_SEG = 30


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
    """Nível (0–3) da pessoa em cada sistema — ver permissoes.py."""
    return mods.niveis_do_usuario(u, _perfil_do(u))


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


def _usuario_da_requisicao(request: Request) -> Optional[dict]:
    dados = seg.ler_token(request.cookies.get(seg.NOME_COOKIE))
    if not dados:
        return None
    u = _usuario_cacheado(dados.get("e", ""))
    if not u or not u.get("ativo", True) or int(u.get("versao_sessao", 1)) != int(dados.get("v", -1)):
        return None
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
    if caminho in ROTAS_PUBLICAS:
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
                                   "criado_em", "criado_por", "ultimo_login", "perfil", "departamentos")}


def _sessao_publica(u: dict) -> dict:
    niveis = _niveis(u)
    return {"usuario": _usuario_publico(u), "modulos": mods.cards_do_usuario(u, niveis), "permissoes": niveis,
            "perfil_nome": _nome_perfil(u), "so_acesso": sorted(mods.IDS_SO_ACESSO)}


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


def _gravar_cookie(resposta: Response, u: dict) -> None:
    resposta.set_cookie(
        seg.NOME_COOKIE, seg.criar_token(u["email"], int(u.get("versao_sessao", 1))),
        max_age=seg.DURACAO_SESSAO_SEG, httponly=True, secure=True, samesite="lax", path="/",
    )


# ──────────────────────────────────────────────────────────────────────────
# LOGIN / SESSÃO
# ──────────────────────────────────────────────────────────────────────────
_EMAIL_VALIDO = re.compile(r"^[^@\s/]+@[^@\s/]+\.[^@\s/]+$")


class LoginEntrada(BaseModel):
    email: str
    senha: str


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

    u = db.usuario_ler(email) if _EMAIL_VALIDO.match(email) else None
    senha_ok = seg.conferir_senha(payload.senha, (u or {}).get("senha_hash"))
    if not u or not senha_ok or not u.get("ativo", True):
        seg.registrar_falha(chave_email, "email")
        seg.registrar_falha(chave_ip, "ip")
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")

    seg.limpar_falhas(chave_email)
    db.usuario_atualizar(email, {"ultimo_login": datetime.now().isoformat()})
    _esquecer_cache(email)
    _gravar_cookie(response, u)
    return _sessao_publica(u)


@app.post("/api/auth/logout")
def auth_logout(response: Response):
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
    _gravar_cookie(response, {**u, "versao_sessao": nova_versao})
    return {"sucesso": True}


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
    return {"usuarios": [{**_usuario_publico(u), "niveis": _niveis(u), "perfil_nome": _nome_perfil(u)} for u in db.usuarios_listar()],
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
    return {"perfis": list(_perfis().values()), "modulos": mods.catalogo_publico(), "niveis": perm.NIVEIS, "uso": _uso_perfis()}


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


@app.get("/api/admin/permissoes-log")
def admin_permissoes_log():
    return {"historico": db.log_permissoes_listar()}


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
# [NOVO v2.4] DIAGNÓSTICO N2 — API GENÉRICA
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
    "/diario": "diario.html",                                            # Diário de Bordo
    "/rg-pedido-acompanhamento.html": "rg-pedido-acompanhamento.html",   # RG do Pedido
    "/diagnostico": "diagnostico.html",                                  # Diagnóstico N2
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
