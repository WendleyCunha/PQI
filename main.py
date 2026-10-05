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
ROTAS_QUALQUER_LOGADO = {"/api/auth/senha"}

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
    if not mods.tem_acesso(usuario, modulo["id"]):
        return _negar(eh_api, 403, f"Você não tem acesso ao módulo “{modulo['nome']}”. Peça liberação ao administrador.")
    return await call_next(request)


def usuario_atual(request: Request) -> dict:
    u = getattr(request.state, "usuario", None) or _usuario_da_requisicao(request)
    if not u:
        raise HTTPException(status_code=401, detail="Faça login de novo.")
    return u


def _usuario_publico(u: dict) -> dict:
    return {k: u.get(k) for k in ("email", "nome", "papel", "modulos", "ativo", "trocar_senha",
                                   "criado_em", "criado_por", "ultimo_login")}


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
    return {"usuario": _usuario_publico(u), "modulos": mods.cards_do_usuario(u)}


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(seg.NOME_COOKIE, path="/")
    return {"sucesso": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    u = _usuario_da_requisicao(request)
    if not u:
        return JSONResponse({"detail": "não logado"}, status_code=401)
    return {"usuario": _usuario_publico(u), "modulos": mods.cards_do_usuario(u)}


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


class UsuarioEdicao(BaseModel):
    nome: Optional[str] = None
    papel: Optional[str] = None
    modulos: Optional[List[str]] = None
    ativo: Optional[bool] = None
    nova_senha: Optional[str] = None


def _validar_papel_modulos(papel: Optional[str], modulos_: Optional[List[str]]):
    if papel is not None and papel not in mods.PAPEIS:
        raise HTTPException(status_code=400, detail="Papel inválido.")
    if modulos_ is not None:
        invalidos = [m for m in modulos_ if m not in mods.IDS_VALIDOS]
        if invalidos:
            raise HTTPException(status_code=400, detail=f"Módulo(s) inexistente(s): {', '.join(invalidos)}")


@app.get("/api/admin/usuarios")
def admin_listar():
    return {"usuarios": [_usuario_publico(u) for u in db.usuarios_listar()],
            "modulos": mods.catalogo_publico(), "papeis": mods.PAPEIS}


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
    criado = db.usuario_criar(email, {
        "email": email, "nome": payload.nome.strip(), "papel": payload.papel,
        "modulos": sorted(set(payload.modulos)), "ativo": payload.ativo,
        "senha_hash": seg.gerar_hash(payload.senha), "versao_sessao": 1, "trocar_senha": True,
        "criado_em": datetime.now().isoformat(), "criado_por": admin["email"],
    })
    if not criado:
        raise HTTPException(status_code=409, detail="Já existe um usuário com esse e-mail.")
    return {"sucesso": True}


@app.put("/api/admin/usuarios/{email}")
def admin_editar(email: str, payload: UsuarioEdicao, request: Request):
    admin = usuario_atual(request)
    email = email.strip().lower()
    alvo = db.usuario_ler(email)
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    _validar_papel_modulos(payload.papel, payload.modulos)
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
    return {"sucesso": True}


@app.get("/api/admin/senha-aleatoria")
def admin_senha_aleatoria():
    return {"senha": seg.senha_aleatoria()}


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
def create_atividade(payload: AtividadeCreate):
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
    })
    return {"sucesso": True, "mensagem": "Atividade salva com sucesso no Firestore!", "id": novo_id}


@app.put("/api/atividades/{atividade_id}/status")
def atualizar_status(atividade_id: str, payload: StatusUpdate):
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
def criar_acao(atividade_id: str, payload: AcaoCreate):
    if not db.atividade_existe(atividade_id):
        raise HTTPException(status_code=404, detail="Atividade não encontrada.")
    novo_id = db.acoes_inserir(atividade_id, payload.descricao, payload.lembrete)
    return {"sucesso": True, "id": novo_id}


@app.delete("/api/acoes/{acao_id}")
def excluir_acao(acao_id: str):
    if not db.acoes_excluir(acao_id):
        raise HTTPException(status_code=404, detail="Ação não encontrada.")
    return {"sucesso": True}


@app.put("/api/acoes/{acao_id}/encerrar-lembrete")
def encerrar_lembrete(acao_id: str):
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


@app.get("/api/diagnostico")
def diagnostico_tudo():
    """Todas as seções de uma vez — o HTML carrega isso ao abrir."""
    return db.diag_ler_todos()


@app.get("/api/diagnostico/{secao}")
def diagnostico_ler(secao: str):
    return {"secao": secao, "dados": db.diag_ler(_validar_secao(secao))}


@app.put("/api/diagnostico/{secao}")
def diagnostico_salvar(secao: str, payload: SecaoDiagnostico):
    _validar_secao(secao)
    tamanho = len(json.dumps(payload.dados, ensure_ascii=False, default=str).encode("utf-8"))
    if tamanho > _LIMITE_SECAO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"A seção '{secao}' ficou com {tamanho // 1024} KB e passa do limite do Firestore (~1 MB por documento).",
        )
    db.diag_salvar(secao, payload.dados)
    return {"sucesso": True}


@app.post("/api/diagnostico-ia")
def diagnostico_resumo_ia():
    """Resumo executivo com a API da Anthropic. A chave fica SÓ no servidor
    (variável de ambiente ANTHROPIC_API_KEY no Render), nunca no HTML."""
    chave = os.environ.get("ANTHROPIC_API_KEY", "")
    if not chave:
        return {"erro": "Configure a variável ANTHROPIC_API_KEY no Render (Environment) para usar esta função."}

    todos = db.diag_ler_todos()
    secoes = ["inventario", "organograma", "raci", "raci_pessoas", "raci_matriz", "diario",
              "entrevistas", "gemba", "matriz", "jornadas", "respostas"]
    dados = {s: todos.get(s) for s in secoes}
    prompt = (
        "Você está ajudando a consolidar um mapeamento de atividades (estilo Lean/Gemba) de uma "
        "equipe de backoffice N2. Abaixo estão os dados brutos coletados, em JSON. Escreva um RESUMO "
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
    return db.mapa_criar(payload.dados, _nome_de(request))


@app.put("/api/mapa/processos/{processo_id}")
def mapa_salvar(processo_id: str, payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Dados do processo inválidos.")
    _checar_tamanho(payload.dados)
    resultado, registro = db.mapa_salvar(processo_id, payload.dados, payload.versao, _nome_de(request))
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
# Rotas genéricas: campos novos se resolvem só no organograma.html.
# ──────────────────────────────────────────────────────────────────────────
COLECAO_ORGANOGRAMA = "organograma_setores"


@app.get("/api/organograma/setores")
def org_listar():
    return db.docs_listar(COLECAO_ORGANOGRAMA)


@app.post("/api/organograma/setores")
def org_criar(payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Dados do setor inválidos.")
    _checar_tamanho(payload.dados)
    return db.docs_criar(COLECAO_ORGANOGRAMA, payload.dados, _nome_de(request))


@app.put("/api/organograma/setores/{setor_id}")
def org_salvar(setor_id: str, payload: ProcessoEntrada, request: Request):
    if not isinstance(payload.dados, dict):
        raise HTTPException(status_code=400, detail="Dados do setor inválidos.")
    _checar_tamanho(payload.dados)
    resultado, registro = db.docs_salvar(COLECAO_ORGANOGRAMA, setor_id, payload.dados, payload.versao, _nome_de(request))
    if resultado == "nao_existe":
        raise HTTPException(status_code=404, detail="Setor não encontrado (pode ter sido excluído).")
    if resultado == "conflito":
        return JSONResponse({"detail": "conflito", "atual": registro}, status_code=409)
    return registro


@app.delete("/api/organograma/setores/{setor_id}")
def org_excluir(setor_id: str, request: Request):
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


for _rota, _arquivo in PAGINAS.items():
    app.add_api_route(_rota, _criar_rota_pagina(_arquivo), methods=["GET", "HEAD"], include_in_schema=False)


if __name__ == "__main__":
    import uvicorn
    porta = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=porta, reload=(porta == 8000))
