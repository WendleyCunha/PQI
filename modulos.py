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
        "nome": "Carteira de Pedidos",
        "descricao": "Carteira do dia (BI Pedidos em Aberto + PV Abertos): prazos, bloqueados, itens e pendências por setor (Compras, CX, Logística…).",
        "url": "/carteira", "icone": "📦", "tipo": "nuvem",
        "paginas": ["/carteira"],
        "apis": ["/api/carteira"],
        # [v4.16] abas do sistema — cada uma pode ter nível próprio por perfil e por pessoa.
        # "min": nível do SISTEMA a partir do qual a aba aparece quando segue o sistema (ex.: Importar só p/ Editar).
        "abas": [
            # [v4.28] Triagem, Status do pedido e Pendências por setor foram para o Painel; Itens virou visão dentro da Carteira
            {"id": "painel", "nome": "Painel"}, {"id": "carteira", "nome": "Carteira"},
            {"id": "cx", "nome": "CX"}, {"id": "compras", "nome": "Compras"}, {"id": "logistica", "nome": "Logística"}, {"id": "solucoes", "nome": "Soluções"},
            {"id": "historico", "nome": "Histórico"}, {"id": "importar", "nome": "Importar", "min": 3}, {"id": "config", "nome": "Configurar", "min": 3},
        ],
    },
    {
        # [v4.1] Veio do Streamlit (cartas.py) para o Painel.
        "id": "cartas",
        "nome": "Cartas de Débito",
        "descricao": "Carta de apuração para desconto salarial: gera o Word, recebe a carta assinada, fecha o lote e guarda o histórico.",
        "url": "/cartas", "icone": "📑", "tipo": "nuvem",
        "paginas": ["/cartas"],
        "apis": ["/api/cartas"],
        "abas": [   # [v4.16]
            {"id": "nova", "nome": "Nova carta", "min": 2}, {"id": "painel", "nome": "Painel"}, {"id": "fechamento", "nome": "Fechamento"},
            {"id": "historico", "nome": "Histórico"}, {"id": "config", "nome": "Configurar"},
        ],
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
        # [v6.4] MAPEAMENTO — a sequência do mapeamento num módulo só, em 4 passos:
        # 1 Organograma · 2 Atividades (Diagnóstico) · 3 Áreas · 4 Processos.
        # Usa as telas e os dados dos três sistemas (que continuam no Painel). Acesso: ver JUNTA abaixo.
        "id": "mapeamento",
        "nome": "Mapeamento",
        "descricao": "Organograma → Atividades → Áreas → Processos: o mapeamento da empresa em sequência, num lugar só.",
        "url": "/mapeamento", "icone": "🧭", "tipo": "nuvem",
        "paginas": ["/mapeamento"],
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
    # [v6.1] Removidos do Painel os externos Parque Aliança, Wendley Site e Wendley Cunha (Streamlit).
    {
        # [v3.1] Substitui o antigo card "Concierge — King Star".
        "id": "mapa",
        "nome": "Processos",
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
    {
        # [v5.0] QUALIDADE — o Mapa de Entregas sem papel: PCL importa o PDF e trata ajustes/retornos,
        # montagem do palete e conferência na doca no celular, retorno com destino da peça e GAP.
        # Ninguém ganha acesso sozinho: o administrador libera em Segurança (sistema e abas).
        "id": "qualidade",
        "nome": "Qualidade — Cargas e Retorno",
        "descricao": "Operação: PCL, montagem do palete, conferência na doca e retorno. Administrativo: painel, GAP e cadastros.",
        "url": "/qualidade", "icone": "✅", "tipo": "nuvem",
        "paginas": ["/qualidade"],
        "apis": ["/api/qualidade"],
        "abas": [   # [v5.9] Operação: PCL, montagem, conferência, retorno · Administrativo: painel, GAP, cadastros
            {"id": "pcl", "nome": "PCL (ajustes, retornos, importar)"},
            {"id": "montagem", "nome": "Montagem do palete", "min": 2},
            {"id": "conferencia", "nome": "Conferência na doca", "min": 2},
            {"id": "retorno", "nome": "Retorno", "min": 2},
            {"id": "painel", "nome": "Painel (administrativo)"},
            {"id": "gap", "nome": "GAP (administrativo)"},
            {"id": "cadastros", "nome": "Cadastros (administrativo)", "min": 3},
        ],
    },
    {
        # [v5.9] ROUTERKING — as entregas na rua: Ao vivo, Histórico de entregas e o app do motorista.
        # Nasceu das abas "Ao vivo" e "Motoristas e app" da Qualidade (mesma base de cargas).
        # Quem tinha essas abas continua com o mesmo acesso até o admin configurar o RouterKing (ver LEGADO).
        "id": "routerking",
        "nome": "RouterKing — Entregas",
        "descricao": "Entregas na rua em tempo real: app do motorista, chat com a frota, palavra-chave, GPS e o histórico de cada cliente (PDF).",
        "url": "/routerking", "icone": "🚚", "tipo": "nuvem",
        "paginas": ["/routerking"],
        "apis": ["/api/routerking"],
        "abas": [
            {"id": "aovivo", "nome": "Ao vivo (entregas na rua)"},
            {"id": "historico", "nome": "Histórico de entregas"},
            {"id": "motoristas", "nome": "Motoristas e app", "min": 3},
        ],
    },
    {
        # [v5.1] PONTO — registro de ponto por digital. Pessoas vêm do Organograma (só leitura).
        # Ninguém ganha acesso sozinho: o administrador libera em Segurança (pensado para o perfil Gerente).
        # Para alguém de alçada menor editar SÓ os cadastros: sistema "Visualizar" + aba Cadastros "Excluir"
        # (e as outras abas "Sem acesso") no acesso por pessoa.
        "id": "ponto",
        "nome": "Ponto — Registro por Digital",
        "descricao": "Bater ponto com a digital, quem está presente agora, espelho, tratamento de ocorrências e cadastros.",
        "url": "/ponto", "icone": "⏱️", "tipo": "nuvem",
        "paginas": ["/ponto"],
        "apis": ["/api/ponto"],
        "abas": [
            {"id": "hoje", "nome": "Hoje (tempo real)"},
            {"id": "marcar", "nome": "Bater ponto (terminal)", "min": 2},
            {"id": "espelho", "nome": "Espelho"},
            {"id": "tratamento", "nome": "Tratamento"},
            {"id": "cadastros", "nome": "Cadastros", "min": 3},
        ],
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
    for mid in LEGADO:   # [v5.9] sistema novo que veio de abas de outro: herda o acesso antigo até ser configurado
        if not _configurado(usuario, perfil, mid):
            saida[mid] = min(3, max(_legado_abas(usuario, perfil, saida, mid).values() or [0]))
    for mid, origens in JUNTA.items():   # [v6.4] Mapeamento: enquanto não configurado, vale o maior dos três
        if not _configurado(usuario, perfil, mid):
            saida[mid] = max(int(saida.get(o, 0)) for o in origens)
    return saida


# [v6.4] O Mapeamento mostra as telas do Organograma, do Diagnóstico e de Processos. Enquanto o admin não
# configurar o Mapeamento para a pessoa (ou o perfil), ela o vê com o MAIOR nível que tem nos três.
# Cada passo continua respeitando o acesso do sistema de origem (quem não tem Processos não vê o passo 4).
JUNTA = {"mapeamento": ("organograma", "diagnostico", "mapa")}


# [v5.9] RouterKing nasceu das abas "aovivo" e "motoristas" da Qualidade. Enquanto o admin não configurar
# o RouterKing para a pessoa (ou para o perfil dela), vale o que ela tinha nessas abas — ninguém perde
# nem ganha acesso no dia da mudança. {sistema novo: (sistema antigo, {aba nova: (aba antiga, mínimo)})}
LEGADO = {"routerking": ("qualidade", {"aovivo": ("aovivo", 1), "historico": ("aovivo", 1), "motoristas": ("motoristas", 3)})}


def _configurado(usuario: dict, perfil, mid: str) -> bool:
    return (mid in (usuario.get("acessos") or {}) or mid in ((perfil or {}).get("niveis") or {})
            or mid in (usuario.get("abas") or {}) or mid in ((perfil or {}).get("abas") or {}))


def _legado_abas(usuario: dict, perfil, niveis: dict, mid: str) -> dict:
    antigo, mapa = LEGADO[mid]
    ns = int(niveis.get(antigo, 0))
    du = (usuario.get("abas") or {}).get(antigo) or {}
    dp = ((perfil or {}).get("abas") or {}).get(antigo) or {}
    out = {}
    for nova, (velha, minimo) in mapa.items():
        v = _n_aba(du.get(velha))
        if v is None:
            v = _n_aba(dp.get(velha))
        out[nova] = nivel_aba_herdado({"min": minimo}, ns) if v is None else v
    return out


# ──────────────────────────────────────────────────────────────────────────
# [NOVO v4.16] PERMISSÃO POR ABA (dentro de cada sistema)
# Níveis da aba: 0 Sem acesso (a aba some) · 1 Visualizar · 2 Adicionar ·
#                3 Editar (sem excluir) · 4 Excluir (tudo)
# Onde fica gravado (só as EXCEÇÕES):
#   perfil["abas"]  = {"carteira": {"cx": 2, "importar": 0}}
#   usuario["abas"] = {"carteira": {"compras": 3}}        ← vale mais que o perfil
# Aba sem exceção SEGUE O SISTEMA: Editar no sistema (3, que sempre incluiu
# excluir) vira 4 na aba; os outros níveis ficam iguais. Assim, no dia em que
# isto entrou, ninguém perdeu nem ganhou acesso.
# ──────────────────────────────────────────────────────────────────────────
NIVEIS_ABA = {0: "Sem acesso", 1: "Visualizar", 2: "Adicionar", 3: "Editar", 4: "Excluir"}


# [v4.22] abas criadas pela própria configuração do sistema (ex.: um SETOR novo na Carteira vira aba)
ABAS_EXTRA: dict = {}


def _abas_mod(m: dict) -> list:
    base = list(m.get("abas") or [])
    ids = {a["id"] for a in base}
    return base + [a for a in ABAS_EXTRA.get(m.get("id"), []) if a.get("id") not in ids]


def abas_de(modulo_id: str) -> list:
    m = POR_ID.get(modulo_id) or {}
    return _abas_mod(m)


def nivel_aba_herdado(aba: dict, nivel_sistema: int) -> int:
    n = int(nivel_sistema or 0)
    if n < int(aba.get("min", 1)):
        return 0
    return 4 if n >= 3 else n


def _n_aba(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return max(0, min(4, int(v)))
    except (TypeError, ValueError):
        return None


def abas_do_usuario(usuario: dict, perfil, niveis: dict) -> dict:
    """{sistema: {aba: nível 0–4}} — já resolvido (pessoa > perfil > herda do sistema)."""
    saida = {}
    admin = usuario.get("papel") == "admin"
    for m in MODULOS:
        abas = _abas_mod(m)
        if not abas:
            continue
        ns = int(niveis.get(m["id"], 0))
        dp = ((perfil or {}).get("abas") or {}).get(m["id"]) or {}
        du = (usuario.get("abas") or {}).get(m["id"]) or {}
        if not admin and m["id"] in LEGADO and not _configurado(usuario, perfil, m["id"]):
            saida[m["id"]] = _legado_abas(usuario, perfil, niveis, m["id"]) if ns > 0 else {a["id"]: 0 for a in abas}
            continue
        r = {}
        for a in abas:
            if admin:
                r[a["id"]] = 4
                continue
            if ns <= 0:   # sem acesso ao sistema = nenhuma aba
                r[a["id"]] = 0
                continue
            v = _n_aba(du.get(a["id"]))
            if v is None:
                v = _n_aba(dp.get(a["id"]))
            r[a["id"]] = nivel_aba_herdado(a, ns) if v is None else v
        saida[m["id"]] = r
    return saida


def nivel_efetivo_sistema(nivel_sistema: int, abas: dict) -> int:
    """Para o porteiro: se uma aba tem nível MAIOR que o sistema (ex.: sistema Visualizar,
    aba CX Adicionar), a pessoa precisa conseguir gravar — a rota confere a aba."""
    if not abas:
        return nivel_sistema
    return max(int(nivel_sistema or 0), max((min(int(v), 3) for v in abas.values()), default=0))


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
    return [{**{k: m[k] for k in ("id", "nome", "icone", "tipo", "descricao")}, "so_acesso": so_acesso(m),
             "abas": _abas_mod(m)} for m in MODULOS]
