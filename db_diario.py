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
     problema de "rerun". Por isso as funções abaixo não têm TTL nenhum;
     se no futuro o volume de leituras crescer muito, dá pra adicionar
     cache com `functools.lru_cache` ou uma lib tipo `cachetools`, mas por
     enquanto (5 pessoas testando) não precisa.

  2. SEM `st.secrets["textkey"]` — isso é um mecanismo só do Streamlit
     Cloud. Aqui a credencial vem de uma VARIÁVEL DE AMBIENTE
     (`FIRESTORE_KEY_JSON`, com o MESMO conteúdo JSON que você já usa no
     secret do Lila Closet — é a mesma service account, então é
     literalmente copiar e colar o mesmo texto) — ou, em desenvolvimento
     local, de um arquivo `firestore_key.json` na mesma pasta (que NUNCA
     deve ir pro Git — ver `.gitignore`).

  3. SEM `st.session_state` — isso também é exclusivo do Streamlit (é por
     sessão de navegador). Aqui o equivalente de "conectar uma vez só" é um
     singleton de módulo (uma variável global comum, `_db_client`) — o
     processo do FastAPI fica de pé o tempo todo (não como o Streamlit, que
     reinicia o script a cada interação), então basta conectar uma vez
     quando o servidor sobe e reaproveitar essa conexão em todo request.

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
from google.oauth2 import service_account

PROJETO_FIRESTORE = "wendleydesenvolvimento"
COLECAO = "diario_atividades"

_db_client: Optional[firestore.Client] = None


def get_db() -> firestore.Client:
    """Conecta uma única vez por processo (equivalente ao 'uma vez por
    sessão' do Streamlit, só que aqui é 'uma vez por vida do servidor')."""
    global _db_client
    if _db_client is None:
        chave_json = os.environ.get("FIRESTORE_KEY_JSON")
        if chave_json:
            key_dict = json.loads(chave_json)
        else:
            caminho_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firestore_key.json")
            if not os.path.exists(caminho_local):
                raise RuntimeError(
                    "Credencial do Firestore não encontrada. Defina a variável de "
                    "ambiente FIRESTORE_KEY_JSON (mesmo conteúdo do secret 'textkey' "
                    "do Lila Closet) ou coloque um arquivo firestore_key.json nesta pasta."
                )
            with open(caminho_local, encoding="utf-8") as f:
                key_dict = json.load(f)

        creds = service_account.Credentials.from_service_account_info(key_dict)
        _db_client = firestore.Client(credentials=creds, project=PROJETO_FIRESTORE)
    return _db_client


def _col():
    return get_db().collection(COLECAO)


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
    docs = _col().where("excluido", "==", excluido).stream()
    linhas = [_doc_to_dict(d) for d in docs]
    linhas.sort(key=lambda r: r.get("prazo") or "")
    return linhas


def atividades_inserir(dados: dict) -> str:
    dados = dict(dados)
    dados.setdefault("excluido", False)
    dados["_criado_em"] = _now_iso()
    _, ref = _col().add(dados)
    return ref.id


def atividades_status_atualizar(atividade_id: str, status: str) -> bool:
    """Retorna False se o documento não existir ou já estiver excluído
    (mesma regra que já existia no main.py com SQLite)."""
    ref = _col().document(atividade_id)
    doc = ref.get()
    if not doc.exists or doc.to_dict().get("excluido"):
        return False
    ref.update({"status": status})
    return True


def atividades_excluir(atividade_id: str) -> bool:
    """Exclusão LÓGICA — mesmo espírito do resto do sistema (nunca apagar
    de verdade, só marcar `excluido=True`, pra manter rastreabilidade)."""
    ref = _col().document(atividade_id)
    if not ref.get().exists:
        return False
    ref.update({"excluido": True})
    return True


def init_dados_exemplo() -> None:
    """Popula a coleção com os 6 exemplos originais, SÓ se ela estiver
    vazia — mesmo comportamento que o init_db() do SQLite tinha."""
    if next(_col().limit(1).stream(), None) is not None:
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
