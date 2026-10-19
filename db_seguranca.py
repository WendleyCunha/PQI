"""
db_seguranca.py — [NOVO v4.2] gavetas do Firestore da Segurança.

Arquivo separado de propósito: o db_diario.py (que guarda tudo dos outros
sistemas) continua IGUAL. Este arquivo só reaproveita a conexão de lá.

Coleções (todas novas, nenhuma mexe no que já existe):
  pqi_config          → documento "seguranca": a aba Configurações
  pqi_sessoes         → uma sessão por login (aba Sessões ativas)
  pqi_pedidos_senha   → pedidos de "Esqueci minha senha" (aba Liberações)
                        o id do documento é o HASH do código que ficou no
                        navegador de quem pediu — o código em si não é guardado.

Mesma regra do resto do sistema: nada de .order_by() no Firestore; quando
precisa ordenar, busca e ordena em Python.
"""
import datetime
from typing import Optional

from google.cloud.firestore_v1.base_query import FieldFilter

from db_diario import get_db, TIMEOUT_FS

COLECAO_CONFIG = "pqi_config"
COLECAO_SESSOES = "pqi_sessoes"
COLECAO_PEDIDOS_SENHA = "pqi_pedidos_senha"

CONFIG_PADRAO = {
    "esqueci_ativo": True,        # mostra "Esqueci minha senha" na tela de login
    "liberacao_minutos": 15,      # depois de liberado, quanto tempo a pessoa tem para criar a senha
    "pedido_validade_horas": 24,  # pedido não atendido some sozinho depois disso
    "tentativas_email": 5, "bloqueio_email_min": 5,
    "tentativas_ip": 20, "bloqueio_ip_min": 15,
    "sessao_horas": 12,
    "lembrar_ativo": True, "lembrar_dias": 30,
    "senha_minimo": 8, "senha_numero": False, "senha_letra": False,
    "avisar_bloqueio": True,      # conta bloqueada por senha errada vira aviso para o ADM
}


def _agora() -> str:
    return datetime.datetime.now().isoformat()


# ─── Configurações ────────────────────────────────────────────────────────
def config_ler() -> dict:
    snap = get_db().collection(COLECAO_CONFIG).document("seguranca").get(timeout=TIMEOUT_FS)
    return {**CONFIG_PADRAO, **((snap.to_dict() or {}) if snap.exists else {})}


def config_salvar(campos: dict) -> None:
    get_db().collection(COLECAO_CONFIG).document("seguranca").set(campos, merge=True, timeout=TIMEOUT_FS)


# ─── Sessões ──────────────────────────────────────────────────────────────
def sessao_criar(sid: str, dados: dict) -> None:
    get_db().collection(COLECAO_SESSOES).document(sid).set({**dados, "revogada": False}, timeout=TIMEOUT_FS)


def sessao_ler(sid: str) -> Optional[dict]:
    snap = get_db().collection(COLECAO_SESSOES).document(sid).get(timeout=TIMEOUT_FS)
    return {**(snap.to_dict() or {}), "sid": sid} if snap.exists else None


def sessao_atualizar(sid: str, campos: dict) -> None:
    get_db().collection(COLECAO_SESSOES).document(sid).set(campos, merge=True, timeout=TIMEOUT_FS)


def sessoes_ativas() -> list:
    """Sessões não derrubadas e não vencidas. As vencidas são apagadas aqui (limpeza)."""
    agora = _agora()
    saida = []
    col = get_db().collection(COLECAO_SESSOES)
    for d in col.where(filter=FieldFilter("revogada", "==", False)).stream(timeout=TIMEOUT_FS):
        x = {**(d.to_dict() or {}), "sid": d.id}
        if str(x.get("expira_em") or "") < agora:
            try:
                d.reference.delete()
            except Exception:
                pass
            continue
        saida.append(x)
    saida.sort(key=lambda s: s.get("ultimo_uso") or s.get("criado_em") or "", reverse=True)
    return saida


def sessoes_revogar_email(email: str, por: str) -> int:
    n = 0
    for d in get_db().collection(COLECAO_SESSOES).where(filter=FieldFilter("email", "==", email)).stream(timeout=TIMEOUT_FS):
        if not (d.to_dict() or {}).get("revogada"):
            d.reference.set({"revogada": True, "revogada_por": por, "revogada_em": _agora()}, merge=True)
            n += 1
    return n


# ─── Pedidos de "Esqueci minha senha" ─────────────────────────────────────
def pedido_criar(pid: str, dados: dict) -> None:
    get_db().collection(COLECAO_PEDIDOS_SENHA).document(pid).set(dados, timeout=TIMEOUT_FS)


def pedido_ler(pid: str) -> Optional[dict]:
    snap = get_db().collection(COLECAO_PEDIDOS_SENHA).document(pid).get(timeout=TIMEOUT_FS)
    return {**(snap.to_dict() or {}), "id": pid} if snap.exists else None


def pedido_atualizar(pid: str, campos: dict) -> None:
    get_db().collection(COLECAO_PEDIDOS_SENHA).document(pid).set(campos, merge=True, timeout=TIMEOUT_FS)


def pedidos_abertos() -> list:
    """Pendentes e liberados (ainda não usados) — é o que o sino do ADM conta."""
    col = get_db().collection(COLECAO_PEDIDOS_SENHA)
    saida = [{**(d.to_dict() or {}), "id": d.id}
             for d in col.where(filter=FieldFilter("status", "in", ["pendente", "liberado"])).stream(timeout=TIMEOUT_FS)]
    saida.sort(key=lambda p: p.get("criado_em") or "", reverse=True)
    return saida


def pedidos_do_email(email: str) -> list:
    col = get_db().collection(COLECAO_PEDIDOS_SENHA)
    return [{**(d.to_dict() or {}), "id": d.id}
            for d in col.where(filter=FieldFilter("email", "==", email)).stream(timeout=TIMEOUT_FS)]


def pedidos_recentes(limite: int = 60) -> list:
    saida = [{**(d.to_dict() or {}), "id": d.id} for d in get_db().collection(COLECAO_PEDIDOS_SENHA).stream(timeout=TIMEOUT_FS)]
    saida.sort(key=lambda p: p.get("criado_em") or "", reverse=True)
    return saida[:limite]
