"""
seguranca.py — senhas, sessões e proteção contra tentativa-e-erro.

Só usa a biblioteca padrão do Python (nada novo no requirements.txt).

SENHAS: nunca são guardadas. O banco guarda só um "hash" PBKDF2-SHA256
(com sal aleatório e 260 mil rodadas) — nem você, olhando o Firestore,
consegue descobrir a senha de alguém. Pra conferir, o servidor refaz o
hash da senha digitada e compara.

SESSÃO: depois do login, o navegador recebe um cookie "pqi_sessao"
assinado com a chave SESSION_SECRET (variável de ambiente no Render).
O cookie é HttpOnly (JavaScript nenhum consegue ler), Secure (só viaja
por https) e SameSite=Lax (outro site não consegue usar ele por você).
Sem a SESSION_SECRET ninguém consegue fabricar um cookie válido.

[v4.2] O que mudou (tudo compatível com os cookies que já existem):
  • criar_token aceita a duração (para o "Lembrar de mim"), o id da sessão
    (para a aba Sessões ativas poder derrubar uma sessão) e a marca de
    administrador de fábrica;
  • limites de tentativas e regras de senha passam a vir da aba
    Configurações da Segurança (função configurar) — sem configuração,
    valem os mesmos números de antes;
  • bloqueios_ativos() lista quem está bloqueado agora (aba Liberações).
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
from typing import Optional

NOME_COOKIE = "pqi_sessao"
DURACAO_SESSAO_SEG = 12 * 3600  # 12 horas logado; depois pede login de novo (ajustável em Configurações)

_SEGREDO_ENV = os.environ.get("SESSION_SECRET", "")
SEGREDO_TEMPORARIO = not _SEGREDO_ENV
# Sem SESSION_SECRET o sistema ainda funciona, mas gera uma chave nova a
# cada reinício do servidor — e todo mundo é deslogado quando o Render
# "acorda". Por isso: configure SESSION_SECRET no Render.
_SEGREDO = (_SEGREDO_ENV or secrets.token_hex(32)).encode("utf-8")

_ITERACOES = 260_000

# [v4.2] regras de senha (ajustáveis em Configurações)
REGRAS_SENHA = {"minimo": 8, "exigir_numero": False, "exigir_letra": False}


# ─── Senhas ───────────────────────────────────────────────────────────────
def gerar_hash(senha: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), sal, _ITERACOES)
    return f"pbkdf2_sha256${_ITERACOES}${sal.hex()}${h.hex()}"


_HASH_FALSO = gerar_hash(secrets.token_hex(8))  # usado pra igualar o tempo quando o e-mail não existe


def conferir_senha(senha: str, armazenado: Optional[str]) -> bool:
    alvo = armazenado or _HASH_FALSO
    try:
        _alg, it, sal_hex, h_hex = alvo.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", (senha or "").encode("utf-8"), bytes.fromhex(sal_hex), int(it))
        ok = hmac.compare_digest(calc.hex(), h_hex)
    except Exception:
        return False
    return ok and armazenado is not None


def senha_aceitavel(senha: str) -> Optional[str]:
    """Devolve a mensagem de erro, ou None se a senha serve."""
    minimo = int(REGRAS_SENHA.get("minimo") or 8)
    if len(senha or "") < minimo:
        return f"A senha precisa ter pelo menos {minimo} caracteres."
    if (senha or "").strip() != senha:
        return "A senha não pode começar nem terminar com espaço."
    if REGRAS_SENHA.get("exigir_numero") and not re.search(r"\d", senha):
        return "A senha precisa ter pelo menos um número."
    if REGRAS_SENHA.get("exigir_letra") and not re.search(r"[A-Za-zÀ-ÿ]", senha):
        return "A senha precisa ter pelo menos uma letra."
    return None


# ─── Token de sessão (cookie assinado) ────────────────────────────────────
def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def criar_token(email: str, versao_sessao: int, duracao_seg: Optional[int] = None,
                sid: Optional[str] = None, fabrica: bool = False) -> str:
    """[v4.2] duracao_seg (Lembrar de mim), sid (sessão listada em Sessões ativas)
    e fabrica (administrador de fábrica) são opcionais — sem eles, igual a antes."""
    carga_d = {"e": email, "v": versao_sessao, "x": int(time.time()) + int(duracao_seg or DURACAO_SESSAO_SEG)}
    if sid:
        carga_d["s"] = sid
    if fabrica:
        carga_d["f"] = 1
    carga = _b64(json.dumps(carga_d, separators=(",", ":")).encode("utf-8"))
    assinatura = hmac.new(_SEGREDO, carga.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{carga}.{assinatura}"


def ler_token(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    carga, assinatura = token.rsplit(".", 1)
    esperado = hmac.new(_SEGREDO, carga.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, assinatura):
        return None
    try:
        dados = json.loads(_unb64(carga))
    except Exception:
        return None
    if int(dados.get("x", 0)) < time.time():
        return None
    return dados


def novo_id(bytes_: int = 18) -> str:
    """[v4.2] Id aleatório (sessão, pedido de senha)."""
    return secrets.token_urlsafe(bytes_)


def resumo(texto: str) -> str:
    """[v4.2] Hash SHA-256 de um código secreto — o banco guarda só o hash."""
    return hashlib.sha256((texto or "").encode("utf-8")).hexdigest()


# ─── Limite de tentativas de login (anti tentativa-e-erro) ────────────────
_falhas: dict = {}
_trava = threading.Lock()
LIMITES = {"email": (5, 5 * 60), "ip": (20, 15 * 60)}  # (tentativas, segundos de bloqueio)


def configurar(cfg: dict) -> None:
    """[v4.2] Aplica a aba Configurações (limites de tentativas, sessão, regras de senha)."""
    global DURACAO_SESSAO_SEG
    try:
        LIMITES["email"] = (max(1, int(cfg.get("tentativas_email", 5))), max(1, int(cfg.get("bloqueio_email_min", 5))) * 60)
        LIMITES["ip"] = (max(1, int(cfg.get("tentativas_ip", 20))), max(1, int(cfg.get("bloqueio_ip_min", 15))) * 60)
        DURACAO_SESSAO_SEG = max(1, int(cfg.get("sessao_horas", 12))) * 3600
        REGRAS_SENHA.update(minimo=max(6, int(cfg.get("senha_minimo", 8))),
                            exigir_numero=bool(cfg.get("senha_numero", False)),
                            exigir_letra=bool(cfg.get("senha_letra", False)))
    except (TypeError, ValueError):
        pass


def bloqueado_ate(chave: str) -> float:
    with _trava:
        _qtd, ate = _falhas.get(chave, (0, 0.0))
        return ate if ate > time.time() else 0.0


def registrar_falha(chave: str, tipo: str) -> bool:
    """Conta uma tentativa errada. [v4.2] Devolve True quando ESTA tentativa bloqueou."""
    maximo, segundos = LIMITES[tipo]
    with _trava:
        qtd, ate = _falhas.get(chave, (0, 0.0))
        if ate and ate <= time.time():
            qtd = 0
        qtd += 1
        bloqueou = qtd >= maximo
        _falhas[chave] = (qtd, time.time() + segundos if bloqueou else 0.0)
        return bloqueou


def limpar_falhas(chave: str) -> None:
    with _trava:
        _falhas.pop(chave, None)


def bloqueios_ativos() -> list:
    """[v4.2] [{chave, tipo, ate}] de quem está bloqueado agora."""
    agora = time.time()
    with _trava:
        return [{"chave": k, "tipo": k.split(":", 1)[0], "valor": k.split(":", 1)[-1], "ate": ate}
                for k, (_q, ate) in _falhas.items() if ate and ate > agora]


def senha_aleatoria(tamanho: int = 12) -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(tamanho))
