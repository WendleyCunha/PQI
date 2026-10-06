"""
permissoes.py — níveis de acesso e a regra do nível "Adicionar".

NÍVEIS (por sistema, definidos no PERFIL de cada pessoa):
  0 Sem acesso  → o card não aparece e o endereço é bloqueado
  1 Visualizar  → abre e consulta; qualquer gravação é recusada
  2 Adicionar   → inclui registros novos e completa campos vazios; o que a
                  própria pessoa registrou continua editável; NÃO altera nem
                  apaga o que outra pessoa registrou
  3 Editar      → tudo, inclusive excluir

COMO O "ADICIONAR" É GARANTIDO NO SERVIDOR
Cada registro novo gravado por alguém com nível 2 recebe a marca "_por"
(e-mail do autor). A cada gravação, o servidor compara o que está no banco
com o que chegou:
  • itens de lista de OUTRA pessoa precisam continuar lá, sem mudança;
  • itens com "_por" = a própria pessoa podem mudar ou sair;
  • campos que estavam vazios podem ser preenchidos — e quem preencheu continua
    podendo ajustar o que escreveu (o servidor guarda isso em "_por_campo");
    campos preenchidos por outra pessoa não podem ser trocados nem apagados;
  • chaves e itens novos são aceitos (e recebem a marca do autor).
Funciona igual para Diagnóstico, Mapa e Organograma, sem o servidor
precisar conhecer os campos de cada sistema.
"""
import json
from collections import Counter

NIVEIS = {0: "Sem acesso", 1: "Visualizar", 2: "Adicionar", 3: "Editar"}
SEM_ACESSO, VISUALIZAR, ADICIONAR, EDITAR = 0, 1, 2, 3

MENSAGEM_ADICIONAR = ("Seu perfil neste sistema é “Adicionar”: você pode incluir registros novos e completar "
                      "campos vazios, mas não alterar nem apagar o que outra pessoa registrou.")


class Recusado(Exception):
    """A gravação mexeu em algo que o nível "Adicionar" não permite."""


def _vazio(x) -> bool:
    return x is None or x == "" or x is False or x == [] or x == {}


def _limpo(x):
    """Versão para comparar: sem campos vazios (assim, quando a tela só
    acrescenta campos padrão vazios num registro antigo, não conta como
    alteração) e sem a marca de autor."""
    if isinstance(x, dict):
        return {k: _limpo(v) for k, v in x.items() if k not in ("_por", "_por_campo") and not _vazio(v)}
    if isinstance(x, list):
        return [_limpo(v) for v in x]
    return x


def _chave(x) -> str:
    return json.dumps(_limpo(x), sort_keys=True, ensure_ascii=False, default=str)


def _do_autor(x, email: str) -> bool:
    return isinstance(x, dict) and x.get("_por") == email


def _carimbar(x, email: str):
    """Marca o autor em registros novos (dicionário, ou cada dicionário de uma lista)."""
    if isinstance(x, dict):
        y = dict(x)
        y["_por"] = email
        return y
    if isinstance(x, list):
        return [_carimbar(v, email) if isinstance(v, dict) else v for v in x]
    return x


def so_acrescimos(antigo, novo, email: str, caminho: str = "dados", raiz: bool = True):
    """Devolve `novo` (com a marca de autor nos itens novos) se ele só ACRESCENTA
    em relação a `antigo`; senão levanta Recusado com o lugar do problema."""
    if _vazio(antigo):
        if not (raiz and isinstance(novo, dict)):
            return _carimbar(novo, email) if (not raiz or isinstance(novo, list)) else novo
        antigo = {}                                  # seção nova: registra quem preencheu cada campo
    if _do_autor(antigo, email):
        return _carimbar(novo, email) if isinstance(novo, dict) else novo

    if isinstance(antigo, list):
        if not isinstance(novo, list):
            raise Recusado(f"{caminho} foi substituído")
        disponiveis = Counter(_chave(n) for n in novo)
        intactos = Counter()
        for o in antigo:
            if _do_autor(o, email):
                continue
            k = _chave(o)
            if disponiveis[k] > 0:
                disponiveis[k] -= 1
                intactos[k] += 1
            else:
                raise Recusado(f"{caminho}: um registro de outra pessoa foi alterado ou removido")
        saida = []
        for n in novo:
            k = _chave(n)
            if intactos[k] > 0:
                intactos[k] -= 1
                saida.append(n)
            else:
                saida.append(_carimbar(n, email))
        return saida

    if isinstance(antigo, dict):
        if not isinstance(novo, dict):
            raise Recusado(f"{caminho} foi substituído")
        saida = dict(novo)
        saida.pop("_por_campo", None)                # quem preencheu cada campo: vale só o que o servidor guardou
        por_campo = dict(antigo.get("_por_campo") or {})
        for k, v in antigo.items():
            if k in ("_por", "_por_campo"):
                if k == "_por":
                    saida["_por"] = v                # ninguém troca o autor de um registro
                continue
            meu_campo = por_campo.get(k) == email
            if k not in novo or _vazio(novo.get(k)):
                if _vazio(v) or meu_campo:
                    continue
                raise Recusado(f"{caminho}.{k} foi apagado")
            if meu_campo and not isinstance(v, (dict, list)):
                saida[k] = novo[k]                   # ajustando o que a própria pessoa escreveu
                continue
            if _vazio(v) and not isinstance(novo[k], (dict, list)):
                por_campo[k] = email                 # preencheu um campo vazio
            saida[k] = so_acrescimos(v, novo[k], email, f"{caminho}.{k}", raiz=False)
        for k, v in novo.items():
            if k not in antigo and k not in ("_por", "_por_campo"):
                if isinstance(v, (dict, list)):
                    saida[k] = _carimbar(v, email)
                else:
                    saida[k] = v
                    if not _vazio(v):
                        por_campo[k] = email
        if por_campo:
            saida["_por_campo"] = por_campo
        return saida

    if _chave(antigo) == _chave(novo):
        return novo
    raise Recusado(f"{caminho} foi alterado")
