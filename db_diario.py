"""
db_diario.py — Diário de Bordo
Módulo de acesso ao Firestore (projeto: wendleydesenvolvimento).

Mesmo projeto Firestore do Lila Closet Atelier (`database.py` de lá), mas
com coleção EXCLUSIVA deste projeto — `diario_atividades` — pra nunca
encostar nos dados do `lila_*`. É o mesmo banco, só que cada projeto usa a
sua própria "gaveta" dentro dele (igual o Firestore já organiza tudo por
coleção).

──────────────────────────────────────────────────────────────────────────
DIFERENÇAS DELIBERADAS em relação ao database.py do Lila Closet (aquele é
Streamlit, este é FastAPI puro — os dois frameworks pedem abordagens
diferentes pro mesmo problema):

  1. SEM `st.cache_data` — no Streamlit, isso existe porque o script inteiro
     reexecuta a cada clique, então sem cache a mesma tela relê o Firestore
     dezenas de vezes por minuto. No FastAPI, cada rota já roda só quando é
     chamada de verdade (um request = uma execução) — não existe esse
     problema de "rerun". Por isso as funções abaixo não têm TTL nenhum.

  2. CREDENCIAL — [AJUSTADO] o app roda no Streamlit Cloud (que sobe o
     FastAPI como app ASGI), e nesse modo os Secrets NÃO chegam como
     variável de ambiente. Por isso `get_db()` agora procura, nesta ordem:
       a) variável de ambiente FIRESTORE_KEY_JSON (Render, local etc.)
       b) st.secrets["FIRESTORE_KEY_JSON"] ou st.secrets["textkey"]
          (painel Settings → Secrets do Streamlit Cloud — pode colar o
          MESMO secret do Lila Closet, do mesmo jeito que está lá)
       c) arquivo firestore_key.json nesta pasta (só desenvolvimento local,
          NUNCA vai pro Git — ver `.gitignore`)

  3. SEM `st.session_state` — isso também é exclusivo do Streamlit (é por
     sessão de navegador). Aqui o equivalente de "conectar uma vez só" é um
     singleton de módulo (uma variável global comum, `_db_client`).

O resto do ESPÍRITO é o mesmo do arquivo do Lila Closet: nunca usar
`.order_by()` do Firestore pra ordenar (mesma armadilha do bug v17 de lá —
excluiria silenciosamente qualquer atividade sem o campo usado pra
ordenar) — sempre buscar tudo e ordenar em Python.
"""

import os
import json
import datetime
from typing import Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import service_account

PROJETO_FIRESTORE = "wendleydesenvolvimento"
COLECAO = "diario_atividades"
COLECAO_ACOES = "diario_acoes"        # [NOVO v2.3] ações dentro de cada atividade
COLECAO_ARQUIVOS = "diario_arquivos"  # [NOVO v2.3] evidências enviadas como arquivo

# [NOVO v2.3] Limite de tamanho do arquivo de evidência. O Firestore aceita
# no máximo 1 MB por documento; 900 KB deixa folga pros outros campos.
# Arquivo maior que isso → usar o modo "Link" (Drive, OneDrive etc.).
LIMITE_ARQUIVO_BYTES = 900 * 1024

# [NOVO] Tempo máximo (segundos) de cada chamada ao Firestore. Sem isso, uma
# conexão travada deixa o request esperando PARA SEMPRE ("carregando
# infinito"); com isso, vira um erro claro no log depois de 20s.
TIMEOUT_FS = 20

_db_client: Optional[firestore.Client] = None
_db_pid: Optional[int] = None  # [NOVO] processo que criou a conexão


def _ler_secret_streamlit() -> Optional[dict]:
    """[NOVO] Lê a credencial do st.secrets (painel Secrets do Streamlit
    Cloud). Aceita as chaves FIRESTORE_KEY_JSON ou textkey, salvas como
    texto JSON ('''{...}''') OU como seção TOML ([textkey] com os campos)."""
    try:
        import streamlit as st
        for chave in ("FIRESTORE_KEY_JSON", "textkey"):
            if chave in st.secrets:
                valor = st.secrets[chave]
                if isinstance(valor, str):
                    # strict=False tolera quebras de linha reais dentro da
                    # private_key (acontece se o secret usou aspas duplas).
                    return json.loads(valor, strict=False)
                return dict(valor)
    except Exception:
        pass
    return None


def get_db() -> firestore.Client:
    """Conecta uma única vez por processo (equivalente ao 'uma vez por
    sessão' do Streamlit, só que aqui é 'uma vez por vida do servidor')."""
    global _db_client, _db_pid
    # [NOVO] Se o processo foi duplicado (fork) depois da conexão ser criada,
    # a conexão gRPC herdada fica morta e trava qualquer consulta. Comparar o
    # PID detecta isso e força uma conexão nova no processo atual.
    if _db_client is not None and _db_pid != os.getpid():
        _db_client = None
    if _db_client is None:
        key_dict = None

        chave_json = os.environ.get("FIRESTORE_KEY_JSON")
        if chave_json:
            key_dict = json.loads(chave_json, strict=False)

        if key_dict is None:
            key_dict = _ler_secret_streamlit()

        if key_dict is None:
            caminho_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firestore_key.json")
            if os.path.exists(caminho_local):
                with open(caminho_local, encoding="utf-8") as f:
                    key_dict = json.load(f)

        if key_dict is None:
            raise RuntimeError(
                "Credencial do Firestore não encontrada. Cadastre em Settings → Secrets "
                "do Streamlit Cloud a chave 'textkey' (ou FIRESTORE_KEY_JSON) com o JSON "
                "da service account, ou coloque um arquivo firestore_key.json nesta pasta."
            )

        creds = service_account.Credentials.from_service_account_info(key_dict)
        _db_client = firestore.Client(credentials=creds, project=PROJETO_FIRESTORE)
        _db_pid = os.getpid()
    return _db_client


def _col():
    return get_db().collection(COLECAO)


def _col_acoes():
    return get_db().collection(COLECAO_ACOES)


def _col_arquivos():
    return get_db().collection(COLECAO_ARQUIVOS)


def _doc_to_dict(doc) -> dict:
    d = doc.to_dict() or {}
    d["id"] = doc.id
    return d


def _now_iso() -> str:
    return datetime.datetime.now().isoformat()


# ──────────────────────────────────────────────────────────────────────────
# ATIVIDADES
# ──────────────────────────────────────────────────────────────────────────

def atividades_listar(excluido: bool = False) -> list[dict]:
    """Busca TODOS os documentos com aquele valor de `excluido` — sem
    order_by (mesmo motivo do database.py do Lila: um order_by exclui
    silenciosamente qualquer documento sem o campo preenchido). Ordena por
    `prazo` em Python, como o restante do sistema já faz em outras listas."""
    docs = _col().where(filter=FieldFilter("excluido", "==", excluido)).stream(timeout=TIMEOUT_FS)
    linhas = [_doc_to_dict(d) for d in docs]
    linhas.sort(key=lambda r: r.get("prazo") or "")

    # [NOVO v2.3] Mesmos dois campos que o SQLite calculava: quantas ações
    # cada atividade tem, e se alguma delas tem lembrete vencido/de hoje
    # ainda aberto (é o que acende o selo piscante na tela).
    hoje = datetime.date.today().isoformat()
    qtd: dict = {}
    pendentes: set = set()
    for a in _col_acoes().stream(timeout=TIMEOUT_FS):
        d = a.to_dict() or {}
        aid = d.get("atividade_id")
        qtd[aid] = qtd.get(aid, 0) + 1
        lem = d.get("lembrete")
        if lem and lem <= hoje and not d.get("lembrete_encerrado"):
            pendentes.add(aid)
    for linha in linhas:
        linha["qtd_acoes"] = qtd.get(linha["id"], 0)
        linha["tem_lembrete_pendente"] = linha["id"] in pendentes
    return linhas


def atividade_existe(atividade_id: str) -> bool:
    return _col().document(atividade_id).get(timeout=TIMEOUT_FS).exists


def atividades_inserir(dados: dict) -> str:
    dados = dict(dados)
    dados.setdefault("excluido", False)
    dados["_criado_em"] = _now_iso()
    _, ref = _col().add(dados, timeout=TIMEOUT_FS)
    return ref.id


def atividades_status_atualizar(atividade_id: str, status: str) -> bool:
    """Retorna False se o documento não existir ou já estiver excluído
    (mesma regra que já existia no main.py com SQLite)."""
    ref = _col().document(atividade_id)
    doc = ref.get(timeout=TIMEOUT_FS)
    if not doc.exists or doc.to_dict().get("excluido"):
        return False
    ref.update({"status": status}, timeout=TIMEOUT_FS)
    return True


def atividades_excluir(atividade_id: str) -> bool:
    """Exclusão LÓGICA — mesmo espírito do resto do sistema (nunca apagar
    de verdade, só marcar `excluido=True`, pra manter rastreabilidade)."""
    ref = _col().document(atividade_id)
    if not ref.get(timeout=TIMEOUT_FS).exists:
        return False
    ref.update({"excluido": True}, timeout=TIMEOUT_FS)
    return True


def init_dados_exemplo() -> None:
    """Popula a coleção com os 6 exemplos originais, SÓ se ela estiver
    vazia — mesmo comportamento que o init_db() do SQLite tinha."""
    if next(_col().limit(1).stream(timeout=TIMEOUT_FS), None) is not None:
        return  # já tem dado — não sobrescreve nada

    exemplos = [
        {"nome": "Reconciliar divergências de estoque", "area": "Logística", "resp": "Wendley", "prio": "Alta", "status": "Em Andamento", "prazo": "2026-10-06", "bloqueio": "—", "mes": "10"},
        {"nome": "Revisar contrato de fornecedor X", "area": "Jurídico", "resp": "Tiago", "prio": "Média", "status": "Aprovação", "prazo": "2026-10-03", "bloqueio": "Aprovação pendente", "mes": "10"},
        {"nome": "Homologar planilha de indicadores", "area": "PQI", "resp": "Guilherme", "prio": "Baixa", "status": "Planejamento", "prazo": "2026-10-12", "bloqueio": "—", "mes": "10"},
        {"nome": "Corrigir SLA de resposta ao cliente", "area": "Customer Experience", "resp": "Wendley", "prio": "Alta", "status": "Bloqueado", "prazo": "2026-09-29", "bloqueio": "Sistema/ferramenta indisponível", "mes": "09"},
        {"nome": "Auditoria de compliance trimestral", "area": "Compliance", "resp": "Tiago", "prio": "Média", "status": "Iniciar", "prazo": "2026-10-20", "bloqueio": "—", "mes": "10"},
        {"nome": "Levantamento de custo logístico", "area": "Compras", "resp": "Guilherme", "prio": "Baixa", "status": "Concluído", "prazo": "2026-09-25", "bloqueio": "—", "mes": "09"},
    ]
    for dados in exemplos:
        atividades_inserir(dados)


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v2.3] AÇÕES — sub-registros dentro de uma atividade (mesma regra
# que o SQLite tinha: ligadas por `atividade_id`, exclusão de verdade).
# ──────────────────────────────────────────────────────────────────────────

def acoes_listar(atividade_id: str) -> list[dict]:
    docs = _col_acoes().where(filter=FieldFilter("atividade_id", "==", atividade_id)).stream(timeout=TIMEOUT_FS)
    linhas = [_doc_to_dict(d) for d in docs]
    linhas.sort(key=lambda r: r.get("_criado_em") or "")
    return linhas


def acoes_inserir(atividade_id: str, descricao: str, lembrete: Optional[str]) -> str:
    _, ref = _col_acoes().add({
        "atividade_id": atividade_id,
        "descricao": descricao,
        "lembrete": lembrete or None,
        "lembrete_encerrado": False,
        "_criado_em": _now_iso(),
    }, timeout=TIMEOUT_FS)
    return ref.id


def acoes_excluir(acao_id: str) -> bool:
    ref = _col_acoes().document(acao_id)
    if not ref.get(timeout=TIMEOUT_FS).exists:
        return False
    ref.delete(timeout=TIMEOUT_FS)
    return True


def acoes_encerrar_lembrete(acao_id: str) -> bool:
    ref = _col_acoes().document(acao_id)
    if not ref.get(timeout=TIMEOUT_FS).exists:
        return False
    ref.update({"lembrete_encerrado": True}, timeout=TIMEOUT_FS)
    return True


def acoes_listar_todas() -> list[dict]:
    """Relatório de Ações: todas as ações, já com nome/responsável/status
    da atividade-mãe (o JOIN que o SQLite fazia, aqui feito em Python)."""
    atividades = {d.id: (d.to_dict() or {}) for d in _col().stream(timeout=TIMEOUT_FS)}
    linhas = []
    for a in _col_acoes().stream(timeout=TIMEOUT_FS):
        d = _doc_to_dict(a)
        mae = atividades.get(d.get("atividade_id"))
        if mae is None:
            continue  # mesma regra do JOIN: ação sem atividade não aparece
        d["atividade_nome"] = mae.get("nome")
        d["atividade_resp"] = mae.get("resp")
        d["atividade_status"] = mae.get("status")
        linhas.append(d)
    linhas.sort(key=lambda r: r.get("_criado_em") or "", reverse=True)
    return linhas


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v2.3] ARQUIVOS DE EVIDÊNCIA — guardados no próprio Firestore.
# O disco do Render Free é apagado a cada reinício, então salvar em pasta
# (como o SQLite fazia em ./uploads) perderia os arquivos.
# ──────────────────────────────────────────────────────────────────────────

def arquivos_salvar(nome: str, tipo: str, conteudo: bytes) -> str:
    _, ref = _col_arquivos().add({
        "nome": nome,
        "tipo": tipo or "application/octet-stream",
        "tamanho": len(conteudo),
        "conteudo": conteudo,
        "_criado_em": _now_iso(),
    }, timeout=TIMEOUT_FS)
    return ref.id


def arquivos_ler(arquivo_id: str) -> Optional[dict]:
    doc = _col_arquivos().document(arquivo_id).get(timeout=TIMEOUT_FS)
    return doc.to_dict() if doc.exists else None


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v2.4] DIAGNÓSTICO N2 — armazenamento GENÉRICO por seção.
#
# Mesmo formato do mod_diagnostico.py (Streamlit): coleção "diagnostico",
# um documento por seção (inventario, diario, matriz…), conteúdo inteiro
# no campo "dados". O servidor NÃO conhece os campos — quem decide o que
# vai dentro de "dados" é o diagnostico.html. Campo novo = só mexer no HTML.
# ──────────────────────────────────────────────────────────────────────────
COLECAO_DIAGNOSTICO = os.environ.get("DIAG_COLECAO", "diagnostico")


def _col_diag():
    return get_db().collection(COLECAO_DIAGNOSTICO)


def diag_ler_todos() -> dict:
    return {d.id: (d.to_dict() or {}).get("dados") for d in _col_diag().stream(timeout=TIMEOUT_FS)}


def diag_ler(secao: str):
    doc = _col_diag().document(secao).get(timeout=TIMEOUT_FS)
    return (doc.to_dict() or {}).get("dados") if doc.exists else None


def diag_salvar(secao: str, dados) -> None:
    brt = datetime.timezone(datetime.timedelta(hours=-3))
    _col_diag().document(secao).set(
        {"dados": dados, "atualizado_em": datetime.datetime.now(brt).isoformat()},
        timeout=TIMEOUT_FS,
    )


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.0] USUÁRIOS — coleção própria "pqi_usuarios" (não mistura com
# nada do Lila Closet). Documento = e-mail em minúsculas. A senha NUNCA é
# gravada: só o hash (ver seguranca.py).
# ──────────────────────────────────────────────────────────────────────────
COLECAO_USUARIOS = "pqi_usuarios"


def _col_usuarios():
    return get_db().collection(COLECAO_USUARIOS)


def usuario_ler(email: str) -> Optional[dict]:
    doc = _col_usuarios().document(email).get(timeout=TIMEOUT_FS)
    return doc.to_dict() if doc.exists else None


def usuarios_listar() -> list[dict]:
    linhas = [d.to_dict() or {} for d in _col_usuarios().stream(timeout=TIMEOUT_FS)]
    linhas.sort(key=lambda u: (u.get("nome") or u.get("email") or "").lower())
    return linhas


def usuario_criar(email: str, dados: dict) -> bool:
    """False se o e-mail já existir (create() falha se o documento existe)."""
    try:
        _col_usuarios().document(email).create(dados, timeout=TIMEOUT_FS)
        return True
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            return False
        raise


def usuario_atualizar(email: str, campos: dict) -> None:
    _col_usuarios().document(email).update(campos, timeout=TIMEOUT_FS)


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.1] MAPA DIGITAL KING STAR — um documento por PROCESSO na coleção
# "mapa_processos". Igual ao Diagnóstico, o servidor não conhece os campos:
# tudo vai dentro de "dados" e quem decide é o mapa.html. O servidor só
# cuida de: versão (pra duas pessoas não sobrescreverem uma à outra),
# quem alterou/quando, e exclusão lógica.
# ──────────────────────────────────────────────────────────────────────────
COLECAO_MAPA = "mapa_processos"


def _col_mapa():
    return get_db().collection(COLECAO_MAPA)


def _mapa_publico(doc_id: str, d: dict) -> dict:
    return {"id": doc_id, "dados": d.get("dados") or {}, "versao": int(d.get("versao", 1)),
            "atualizado_por": d.get("atualizado_por"), "atualizado_em": d.get("atualizado_em"),
            "criado_por": d.get("criado_por"), "criado_em": d.get("criado_em")}


def mapa_listar() -> list[dict]:
    saida = []
    for doc in _col_mapa().stream(timeout=TIMEOUT_FS):
        d = doc.to_dict() or {}
        if not d.get("excluido"):
            saida.append(_mapa_publico(doc.id, d))
    return saida


def mapa_criar(dados: dict, usuario: str) -> dict:
    agora = _now_iso()
    ref = _col_mapa().document()
    registro = {"dados": dados, "versao": 1, "excluido": False, "criado_por": usuario, "criado_em": agora,
                "atualizado_por": usuario, "atualizado_em": agora}
    ref.set(registro, timeout=TIMEOUT_FS)
    return _mapa_publico(ref.id, registro)


def mapa_salvar(processo_id: str, dados: dict, versao_esperada: Optional[int], usuario: str):
    """Grava com controle de versão, dentro de uma transação do Firestore.
    Retorna ("ok", registro) | ("conflito", registro_atual) | ("nao_existe", None).
    versao_esperada=None grava por cima (usado no "manter a minha versão")."""
    ref = _col_mapa().document(processo_id)
    transacao = get_db().transaction()

    @firestore.transactional
    def _tx(t):
        snap = ref.get(transaction=t)
        if not snap.exists or (snap.to_dict() or {}).get("excluido"):
            return "nao_existe", None
        atual = snap.to_dict() or {}
        versao_atual = int(atual.get("versao", 1))
        if versao_esperada is not None and versao_esperada != versao_atual:
            return "conflito", _mapa_publico(processo_id, atual)
        novo = {"dados": dados, "versao": versao_atual + 1, "atualizado_por": usuario, "atualizado_em": _now_iso()}
        t.update(ref, novo)
        return "ok", _mapa_publico(processo_id, {**atual, **novo})

    return _tx(transacao)


def mapa_excluir(processo_id: str, usuario: str) -> bool:
    ref = _col_mapa().document(processo_id)
    if not ref.get(timeout=TIMEOUT_FS).exists:
        return False
    ref.update({"excluido": True, "excluido_por": usuario, "excluido_em": _now_iso()}, timeout=TIMEOUT_FS)
    return True


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.2] DOCUMENTOS GENÉRICOS COM VERSÃO — mesma lógica do Mapa Digital,
# mas servindo qualquer coleção (usado pelo Organograma: um documento por
# setor). O servidor não conhece os campos: quem decide é o HTML.
# ──────────────────────────────────────────────────────────────────────────
def docs_listar(colecao: str) -> list[dict]:
    saida = []
    for doc in get_db().collection(colecao).stream(timeout=TIMEOUT_FS):
        d = doc.to_dict() or {}
        if not d.get("excluido"):
            saida.append(_mapa_publico(doc.id, d))
    return saida


def docs_criar(colecao: str, dados: dict, usuario: str) -> dict:
    agora = _now_iso()
    ref = get_db().collection(colecao).document()
    registro = {"dados": dados, "versao": 1, "excluido": False, "criado_por": usuario, "criado_em": agora,
                "atualizado_por": usuario, "atualizado_em": agora}
    ref.set(registro, timeout=TIMEOUT_FS)
    return _mapa_publico(ref.id, registro)


def docs_salvar(colecao: str, doc_id: str, dados: dict, versao_esperada: Optional[int], usuario: str):
    ref = get_db().collection(colecao).document(doc_id)
    transacao = get_db().transaction()

    @firestore.transactional
    def _tx(t):
        snap = ref.get(transaction=t)
        if not snap.exists or (snap.to_dict() or {}).get("excluido"):
            return "nao_existe", None
        atual = snap.to_dict() or {}
        versao_atual = int(atual.get("versao", 1))
        if versao_esperada is not None and versao_esperada != versao_atual:
            return "conflito", _mapa_publico(doc_id, atual)
        novo = {"dados": dados, "versao": versao_atual + 1, "atualizado_por": usuario, "atualizado_em": _now_iso()}
        t.update(ref, novo)
        return "ok", _mapa_publico(doc_id, {**atual, **novo})

    return _tx(transacao)


def docs_excluir(colecao: str, doc_id: str, usuario: str) -> bool:
    ref = get_db().collection(colecao).document(doc_id)
    if not ref.get(timeout=TIMEOUT_FS).exists:
        return False
    ref.update({"excluido": True, "excluido_por": usuario, "excluido_em": _now_iso()}, timeout=TIMEOUT_FS)
    return True
