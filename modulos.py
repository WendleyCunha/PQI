"""
modulos.py — CATÁLOGO DE MÓDULOS (o único lugar pra cadastrar um módulo novo)

Cada módulo aqui vira:
  1) um card no Painel (só pra quem tem permissão), e
  2) uma regra de segurança: as "paginas" e "apis" listadas só abrem pra
     quem tem esse módulo liberado (o admin sempre tem tudo).

"id" é o que fica gravado na permissão de cada usuário — NÃO renomeie um id
depois de liberar pra alguém (a permissão antiga deixaria de valer).

Módulos EXTERNOS (Streamlit, portal King Star): aqui só dá pra esconder o
card. Quem souber o endereço direto ainda consegue abrir, porque eles não
rodam neste servidor — a proteção de verdade deles é o login próprio de cada um.
"""

MODULOS = [
    {
        "id": "diario",
        "nome": "Diário de Bordo",
        "descricao": "Diário de gestão: registro do dia, cobranças, feedbacks, ATAs e prioridades P1–P4 por pilar.",
        "url": "/diario", "icone": "📔", "tipo": "nuvem",
        "paginas": ["/diario", "/ata"],   # [v3.7] + Gerador de ATA
        "apis": ["/api/atividades", "/api/acoes", "/api/kpis", "/api/upload", "/api/arquivos", "/api/diario"],
    },
    {
        "id": "carteira",
        "nome": "Carteira de Encomendas",
        "descricao": "Carteira do dia (BI Pedidos em Aberto + PV Abertos): prazos, bloqueados, itens e pendências por setor (Compras, CX, Logística…).",
        "url": "/carteira", "icone": "📦", "tipo": "nuvem",
        "paginas": ["/carteira"],
        "apis": ["/api/carteira"],
    },
    {
        "id": "rg",
        "nome": "RG do Pedido — Acompanhamento",
        "descricao": "Rastreabilidade do item do pedido origem ao reenvio: cronograma, cenários de teste e GAPs.",
        "url": "/rg-pedido-acompanhamento.html", "icone": "🧾", "tipo": "arquivo",
        "paginas": ["/rg-pedido-acompanhamento.html"],
        "apis": [],
    },
    {
        "id": "diagnostico",
        "nome": "Diagnóstico",
        "descricao": "Mapeamento de atividades por departamento: inventário, organograma real, RACI, Diário de Bordo, Gemba, matriz, jornada e relatório.",
        "url": "/diagnostico", "icone": "🗺️", "tipo": "nuvem",
        "paginas": ["/diagnostico"],
        "apis": ["/api/diagnostico", "/api/diagnostico-ia"],
    },
    {
        "id": "parque",
        "nome": "Parque Aliança — Gestão",
        "descricao": "Relatórios, anúncios, passagens e manutenção da congregação.",
        "url": "https://painelparquealianca.streamlit.app/?embedded=true", "icone": "🕊️", "tipo": "externo",
        "paginas": [], "apis": [],
    },
    {
        "id": "wendleysite",
        "nome": "Wendley Site",
        "descricao": "Tickets, ChecKing e Home — hub principal de módulos.",
        "url": "https://wendleysite.streamlit.app/?embedded=true", "icone": "🗂️", "tipo": "externo",
        "paginas": [], "apis": [],
    },
    {
        "id": "wendleycunha",
        "nome": "Wendley Cunha",
        "descricao": "App pessoal no Streamlit Cloud.",
        "url": "https://wendleycunha.streamlit.app/?embedded=true", "icone": "👤", "tipo": "externo",
        "paginas": [], "apis": [],
    },
    {
        # [v3.1] Substitui o antigo card "Concierge — King Star".
        "id": "mapa",
        "nome": "Mapa Digital King Star",
        "descricao": "Mapeamento de processos: como é feito hoje × Instrução de Trabalho — gaps, aderência e oportunidades.",
        "url": "/mapa", "icone": "🧩", "tipo": "nuvem",
        "paginas": ["/mapa"],
        "apis": ["/api/mapa"],
    },
    {
        "id": "organograma",
        "nome": "Organogramas",
        "descricao": "Estrutura de cada setor: quem é quem, a quem responde, nível, escala e horário.",
        "url": "/organograma", "icone": "🏛️", "tipo": "nuvem",
        "paginas": ["/organograma"],
        "apis": ["/api/organograma"],
    },
]

# Só aparece pra quem é admin (não precisa liberar — vem junto com o papel).
MODULO_ADMIN = {
    "id": "admin",
    "nome": "Segurança — Usuários e Permissões",
    "descricao": "Usuários, perfis de acesso (Visualizar · Adicionar · Editar), departamentos e histórico de alterações.",
    "url": "/admin", "icone": "🔐", "tipo": "admin",
}

# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.3] PERFIS DE ACESSO — cada perfil define um nível por sistema:
#   0 Sem acesso · 1 Visualizar · 2 Adicionar · 3 Editar   (ver permissoes.py)
# Sistemas externos e o RG (que guarda no próprio navegador) só têm
# "acessa / não acessa": qualquer nível ≥ 1 vira 1.
# Estes perfis são criados uma vez (se ainda não existir nenhum) e depois
# o administrador ajusta e cria outros na aba Permissões.
# ──────────────────────────────────────────────────────────────────────────
PERFIS_PADRAO = [
    {"id": "gestor", "nome": "Gestor", "descricao": "Edita tudo nos sistemas da área.",
     "niveis": {"diario": 3, "rg": 1, "diagnostico": 3, "mapa": 3, "organograma": 3, "carteira": 3}},
    {"id": "analista", "nome": "Analista", "descricao": "Edita o Diário; adiciona no Diagnóstico e no Mapa; consulta o Organograma.",
     "niveis": {"diario": 3, "rg": 1, "diagnostico": 2, "mapa": 2, "organograma": 1, "carteira": 2}},
    {"id": "colaborador", "nome": "Colaborador", "descricao": "Adiciona demandas e registros, sem alterar os existentes.",
     "niveis": {"diario": 2, "diagnostico": 2, "mapa": 1, "organograma": 1, "carteira": 2}},
    {"id": "leitura", "nome": "Somente leitura", "descricao": "Consulta os sistemas da área, sem gravar nada.",
     "niveis": {"diario": 1, "rg": 1, "diagnostico": 1, "mapa": 1, "organograma": 1, "carteira": 1}},
]

PAPEIS = {
    "admin": "Administrador — vê e gerencia tudo",
    "supervisor": "Supervisor — vê os módulos liberados (e, nos módulos de demandas, o compilado da equipe)",
    "usuario": "Usuário — vê só os módulos liberados",
}

IDS_VALIDOS = {m["id"] for m in MODULOS}
POR_ID = {m["id"]: m for m in MODULOS}


def so_acesso(modulo: dict) -> bool:
    """Sistemas onde só existe "acessa ou não" (externos e arquivos locais)."""
    return modulo.get("tipo") in ("externo", "arquivo")


IDS_SO_ACESSO = {m["id"] for m in MODULOS if so_acesso(m)}


def _ajustar(m: dict, n) -> int:
    try:
        n = max(0, min(3, int(n or 0)))
    except (TypeError, ValueError):
        n = 0
    return min(n, 1) if so_acesso(m) else n


def niveis_do_perfil(usuario: dict, perfil) -> dict:
    """Nível que vem SÓ do perfil (sem as exceções individuais).
    admin → tudo 3 · com perfil → o do perfil · sem perfil (cadastro antigo) →
    3 nos módulos que estavam marcados, como era antes dos perfis."""
    if usuario.get("papel") == "admin":
        return {m["id"]: (1 if so_acesso(m) else 3) for m in MODULOS}
    saida = {}
    for m in MODULOS:
        if perfil is not None:
            n = (perfil.get("niveis") or {}).get(m["id"], 0)
        else:
            n = 3 if m["id"] in (usuario.get("modulos") or []) else 0
        saida[m["id"]] = _ajustar(m, n)
    return saida


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v3.9] ACESSO INDIVIDUAL POR SISTEMA (aba Permissões → clicar no sistema)
# No cadastro do usuário, "acessos" guarda SÓ as exceções:
#   {"carteira": 3}               → nível fixo neste sistema (0–3)
#   {"mapa": "perfil:gestor"}     → neste sistema, vale como o perfil Gestor
#                                    (acompanha se o perfil Gestor mudar)
# Sistema sem exceção → segue o perfil da pessoa. Admin ignora exceções.
# ──────────────────────────────────────────────────────────────────────────
def resolver_excecao(m: dict, valor, perfis: dict):
    """Nível (0–3) de uma exceção, ou None se ela não vale (perfil apagado, valor inválido)."""
    if isinstance(valor, str) and valor.startswith("perfil:"):
        p = (perfis or {}).get(valor[7:])
        if not p:
            return None
        return _ajustar(m, (p.get("niveis") or {}).get(m["id"], 0))
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.strip().isdigit()):
        return _ajustar(m, valor)
    return None


def niveis_do_usuario(usuario: dict, perfil, perfis: dict = None) -> dict:
    """Nível (0–3) de cada sistema para esta pessoa: o do perfil, trocado pela
    exceção individual quando houver (ver acima)."""
    saida = niveis_do_perfil(usuario, perfil)
    if usuario.get("papel") == "admin":
        return saida
    for mid, valor in (usuario.get("acessos") or {}).items():
        m = POR_ID.get(mid)
        if not m:
            continue
        n = resolver_excecao(m, valor, perfis)
        if n is not None:
            saida[mid] = n
    return saida


def modulo_da_rota(caminho: str):
    """Qual módulo protege este endereço? None = endereço não pertence a módulo nenhum."""
    for m in MODULOS:
        if caminho in m["paginas"]:
            return m
        for prefixo in m["apis"]:
            if caminho == prefixo or caminho.startswith(prefixo + "/"):
                return m
    return None


def cards_do_usuario(usuario: dict, niveis: dict) -> list:
    """O que o Painel mostra pra esta pessoa (sem as regras internas de rota)."""
    publico = lambda m: {k: m[k] for k in ("id", "nome", "descricao", "url", "icone", "tipo")}
    cards = [publico(m) for m in MODULOS if niveis.get(m["id"], 0) >= 1]
    if usuario.get("papel") == "admin":
        cards.append(publico(MODULO_ADMIN))
    return cards


def catalogo_publico() -> list:
    return [{**{k: m[k] for k in ("id", "nome", "icone", "tipo", "descricao")}, "so_acesso": so_acesso(m)} for m in MODULOS]
