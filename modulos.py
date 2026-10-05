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
        "descricao": "Cadastro, acompanhamento e indicadores de atividades — com ações dentro de cada atividade.",
        "url": "/diario", "icone": "📔", "tipo": "nuvem",
        "paginas": ["/diario"],
        "apis": ["/api/atividades", "/api/acoes", "/api/kpis", "/api/upload", "/api/arquivos"],
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
        "nome": "Diagnóstico N2",
        "descricao": "Mapeamento de atividades do Backoffice: inventário, organograma, RACI, diário de bordo, Gemba, matriz, jornada e relatório.",
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
    "nome": "Usuários e Permissões",
    "descricao": "Criar usuários, definir senha inicial, papel e quais módulos cada um enxerga.",
    "url": "/admin", "icone": "🔐", "tipo": "admin",
}

PAPEIS = {
    "admin": "Administrador — vê e gerencia tudo",
    "supervisor": "Supervisor — vê os módulos liberados (e, nos módulos de demandas, o compilado da equipe)",
    "usuario": "Usuário — vê só os módulos liberados",
}

IDS_VALIDOS = {m["id"] for m in MODULOS}


def modulo_da_rota(caminho: str):
    """Qual módulo protege este endereço? None = endereço não pertence a módulo nenhum."""
    for m in MODULOS:
        if caminho in m["paginas"]:
            return m
        for prefixo in m["apis"]:
            if caminho == prefixo or caminho.startswith(prefixo + "/"):
                return m
    return None


def tem_acesso(usuario: dict, modulo_id: str) -> bool:
    return usuario.get("papel") == "admin" or modulo_id in (usuario.get("modulos") or [])


def cards_do_usuario(usuario: dict) -> list:
    """O que o Painel mostra pra esta pessoa (sem as regras internas de rota)."""
    publico = lambda m: {k: m[k] for k in ("id", "nome", "descricao", "url", "icone", "tipo")}
    cards = [publico(m) for m in MODULOS if tem_acesso(usuario, m["id"])]
    if usuario.get("papel") == "admin":
        cards.append(publico(MODULO_ADMIN))
    return cards


def catalogo_publico() -> list:
    return [{k: m[k] for k in ("id", "nome", "icone", "tipo")} for m in MODULOS]
