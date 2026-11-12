"""
testes_agente.py — regras do Agente IA (v6.0) SEM Firestore e SEM IA externa.

    python testes_agente.py

Cobre: quem alimenta (Administrador e perfil Gerente; usuário comum recebe 403),
pergunta respondida pela base com a fonte, pergunta sem resposta virando pendência,
👎 virando pendência ligada ao artigo, artigo criado a partir da pendência (resolve),
nova versão + histórico + voltar versão, artigo desativado deixa de responder,
painel e a IA externa (simulada) usando só os artigos / dizendo NAO_SEI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("AGENTE_IA", None)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import agente as A  # noqa: E402

BANCO: dict = {}
SETORES = [{"id": "cx", "dados": {"nome": "CX", "pessoas": [
    {"id": "1", "nome": "Maria Paula", "cargo": "Supervisora", "nivel": "C", "email": "Maria.Paula@ks"},
    {"id": "2", "nome": "João Sem Login", "cargo": "Analista", "nivel": "D", "email": "joao@ks"},
    {"id": "3", "nome": "Sem E-mail", "cargo": "Analista", "nivel": "D"},
    {"id": "4", "nome": "", "cargo": "Analista", "vaga": True}]}},
           {"id": "niv", "dados": {"_tipo": "niveis"}}]
CONTAS = {"maria.paula@ks": {"nome": "Maria Paula", "ativo": True}}
USUARIO: dict = {}
PROCESSOS: list = []


def salvar_proc(pid, dados, versao, usuario):
    for i, r in enumerate(PROCESSOS):
        if r["id"] == pid:
            PROCESSOS[i] = {**r, "dados": dados, "versao": r["versao"] + 1}
            return "ok", PROCESSOS[i]
    return "nao_existe", None


def criar_proc(dados, usuario, email=None):
    reg = {"id": f"P{len(PROCESSOS) + 10}", "versao": 1, "dados": dados}
    PROCESSOS.append(reg)
    return reg
FALHAS = []


def ok(cond, txt):
    print(("  ✔ " if cond else "  ✘ ") + txt)
    if not cond:
        FALHAS.append(txt)


def ligar_memoria():
    BANCO.clear()
    A.P.update(
        listar=lambda col: [dict(v, id=k) for k, v in BANCO.get(col, {}).items()],
        ler=lambda col, i: (dict(BANCO[col][i], id=i) if i in BANCO.get(col, {}) else None),
        gravar=lambda col, i, d: BANCO.setdefault(col, {}).__setitem__(i, {k: v for k, v in d.items() if k != "id"}),
        desde=lambda col, campo, de: [dict(v, id=k) for k, v in BANCO.get(col, {}).items() if (v.get(campo) or "") >= de],
    )
    A.esquecer_cache()


def como(email, perfil_nome="Analista", admin=False):
    USUARIO.clear()
    USUARIO.update({"email": email, "nome": email.split("@")[0].title(), "_perfil_nome": perfil_nome, **({"papel": "admin"} if admin else {})})


def montar():
    app = FastAPI()
    A.ligar(app, usuario_atual=lambda request: dict(USUARIO),
            nome_perfil=lambda u: "Administrador" if u.get("papel") == "admin" else u.get("_perfil_nome"),
            processos=lambda: list(PROCESSOS), criar_processo=criar_proc, salvar_processo=salvar_proc,
            setores=lambda: SETORES, usuarios=lambda: CONTAS)
    return TestClient(app)


ARTIGO = {"titulo": "Como importar a carteira do dia", "setor": "Logística",
          "passos": ["Clique em Importar a carteira do dia.", "Selecione a planilha Pedidos em Aberto do BI.", "Confira a Foto do dia."],
          "palavras": "importar planilha bi pedidos aberto", "ativo": True}


def teste_permissoes():
    print("Quem alimenta")
    ligar_memoria(); t = montar()
    como("ana@ks")
    ok(t.get("/api/agente/me").json()["pode_alimentar"] is False, "usuário comum não alimenta")
    ok(t.post("/api/agente/artigos", json=ARTIGO).status_code == 403, "usuário comum recebe 403 ao criar artigo")
    ok(t.get("/api/agente/pendencias").status_code == 403, "usuário comum não vê pendências")
    como("ger@ks", "Gerente")
    ok(t.get("/api/agente/me").json()["pode_alimentar"] is True, "perfil Gerente alimenta")
    como("chefe@ks", "gerente ")
    ok(t.get("/api/agente/me").json()["pode_alimentar"] is True, "nome do perfil sem diferença de maiúscula/espaço")
    como("adm@ks", admin=True)
    ok(t.post("/api/agente/artigos", json=ARTIGO).status_code == 200, "administrador cria artigo")
    os.environ["AGENTE_PERFIS_ALIMENTAM"] = "Gerente, Gestor"
    como("g2@ks", "Gestor")
    ok(t.get("/api/agente/me").json()["pode_alimentar"] is True, "AGENTE_PERFIS_ALIMENTAM amplia a lista")
    os.environ.pop("AGENTE_PERFIS_ALIMENTAM")


def teste_perguntas():
    print("Perguntar, pendências e versões")
    ligar_memoria(); t = montar()
    como("adm@ks", admin=True)
    art = t.post("/api/agente/artigos", json=ARTIGO).json()
    ok(art["versao"] == 1 and art["id"], "artigo nasce na versão 1")

    como("ana@ks")
    r = t.post("/api/agente/perguntar", json={"pergunta": "como importar a planilha da carteira?"}).json()
    ok(r["tipo"] == "resposta" and r["modo"] == "base", "pergunta respondida pela base (sem IA externa)")
    ok(r["fonte"]["id"] == art["id"] and r["fonte"]["versao"] == 1, "resposta traz a fonte e a versão")
    ok(len(r["passos"]) == 3, "devolve o passo a passo do artigo")

    r2 = t.post("/api/agente/perguntar", json={"pergunta": "qual o horário do refeitório?"}).json()
    ok(r2["tipo"] == "nao_sei", "pergunta fora da base: não inventa")
    ok(t.post("/api/agente/perguntar", json={"pergunta": "   "}).status_code == 422, "pergunta vazia é recusada")

    ok(t.post("/api/agente/retorno", json={"pergunta_id": r["pergunta_id"], "util": False, "obs": "faltou o erro de coluna"}).json()["ok"], "👎 registrado")
    como("outro@ks")
    ok(t.post("/api/agente/retorno", json={"pergunta_id": r["pergunta_id"], "util": True}).status_code == 404, "ninguém avalia a pergunta de outra pessoa")

    como("ger@ks", "Gerente")
    me = t.get("/api/agente/me").json()
    ok(me["pendencias"] == 2, "duas pendências abertas (sem resposta + não resolveu)")
    pend = t.get("/api/agente/pendencias").json()["pendencias"]
    sem = next(p for p in pend if p["motivo"] == "sem_resposta")
    neg = next(p for p in pend if p["motivo"] == "nao_resolveu")
    ok(neg["artigo_id"] == art["id"] and "coluna" in neg["obs"], "👎 fica ligado ao artigo, com a observação")

    novo = t.post("/api/agente/artigos", json={"titulo": "Horário do refeitório", "passos": ["Das 11h às 14h."],
                                               "palavras": "refeitorio almoço", "pendencia_id": sem["id"]}).json()
    ok(novo["versao"] == 1, "pendência respondida vira artigo")
    ed = t.put(f"/api/agente/artigos/{art['id']}", json={**ARTIGO, "passos": ARTIGO["passos"] + ["Erro de coluna: avise o Gerente."],
                                                          "pendencia_id": neg["id"], "motivo": "incluí o erro de coluna"}).json()
    ok(ed["versao"] == 2 and len(ed["versoes"]) == 1, "edição gera versão 2 e guarda a anterior")
    ok(t.get("/api/agente/me").json()["pendencias"] == 0, "as duas pendências foram resolvidas")

    como("ana@ks")
    r3 = t.post("/api/agente/perguntar", json={"pergunta": "horário do refeitório"}).json()
    ok(r3["tipo"] == "resposta" and r3["fonte"]["id"] == novo["id"], "o agente já responde o que foi documentado")

    como("ger@ks", "Gerente")
    his = t.get("/api/agente/historico").json()["historico"]
    ok({h["acao"] for h in his} >= {"criou", "editou"} and any(h.get("detalhe") == "incluí o erro de coluna" for h in his), "histórico registra quem e o que mudou")
    rv = t.post(f"/api/agente/artigos/{art['id']}/restaurar", json={"versao": 1}).json()
    ok(rv["versao"] == 3 and len(rv["passos"]) == 3, "voltar versão restaura o conteúdo como versão 3")
    ok(t.post(f"/api/agente/artigos/{art['id']}/restaurar", json={"versao": 99}).status_code == 404, "versão inexistente é recusada")

    t.put(f"/api/agente/artigos/{art['id']}", json={**ARTIGO, "ativo": False})
    como("ana@ks")
    ok(t.post("/api/agente/perguntar", json={"pergunta": "como importar a planilha da carteira?"}).json()["tipo"] == "nao_sei",
       "artigo desativado deixa de responder")

    como("ger@ks", "Gerente")
    p = t.get("/api/agente/painel").json()
    ok(p["perguntas"] == 4 and p["respondidas"] == 2 and p["sem_resposta"] == 2 and p["nao_resolveu"] == 1, "painel conta perguntas e desfechos")
    ok(any(h["acao"] == "desativou" for h in t.get("/api/agente/historico").json()["historico"]), "desativar fica no histórico")


def teste_ia_simulada():
    print("IA externa (simulada)")
    ligar_memoria(); t = montar()
    como("adm@ks", admin=True)
    art = t.post("/api/agente/artigos", json=ARTIGO).json()
    os.environ.update(AGENTE_IA="groq", GROQ_API_KEY="teste")
    vistos = {}

    def falso(url, corpo, cab, timeout=40):
        vistos["prompt"] = corpo["messages"][-1]["content"]
        return {"choices": [{"message": {"content": RESPOSTA["v"]}}]}
    original = A._http_json
    A._http_json = falso
    RESPOSTA = {"v": "1. Clique em Importar.\n2. Escolha a planilha.\nFONTE: 1"}
    try:
        como("ana@ks")
        r = t.post("/api/agente/perguntar", json={"pergunta": "como importar a planilha?"}).json()
        ok(r["modo"] == "ia" and r["fonte"]["id"] == art["id"] and "FONTE" not in r["texto"], "IA responde e a fonte sai do texto")
        ok("Como importar a carteira do dia" in vistos["prompt"], "a IA recebe só os artigos encontrados")
        RESPOSTA["v"] = "NAO_SEI"
        ok(t.post("/api/agente/perguntar", json={"pergunta": "importar planilha de férias"}).json()["tipo"] == "nao_sei", "IA diz NAO_SEI → vira pendência")

        def quebra(*a, **k):
            raise A.urllib.error.URLError("fora do ar")
        A._http_json = quebra
        r = t.post("/api/agente/perguntar", json={"pergunta": "Como importar a PLANILHA"}).json()
        ok(r.get("mesma_resposta") and r["texto"].startswith("1. Clique em Importar"), "[v7.3] mesma pergunta: a mesma resposta (nem chama a IA)")
        r = t.post("/api/agente/perguntar", json={"pergunta": "qual o passo para importar a planilha?"}).json()
        ok(r["tipo"] == "resposta" and r["modo"] == "base", "IA fora do ar: responde pela base")
    finally:
        A._http_json = original
        os.environ.pop("AGENTE_IA", None); os.environ.pop("GROQ_API_KEY", None)


def teste_atribuir():
    print("Atribuir a uma pessoa do Organograma")
    ligar_memoria(); t = montar()
    como("ana@ks")
    t.post("/api/agente/perguntar", json={"pergunta": "como faço a baixa de pasta da transportadora?"})
    como("ger@ks", "Gerente")
    pend = t.get("/api/agente/pendencias").json()["pendencias"][0]
    ok(pend["fase"] == "sem_responsavel", "pendência nasce sem responsável")
    ps = t.get("/api/agente/responsaveis").json()["pessoas"]
    ok(len(ps) == 3 and not any(p["nome"] == "" for p in ps), "lista pessoas do Organograma (sem vagas e sem a config de níveis)")
    ok(next(p for p in ps if p["nome"] == "Maria Paula")["tem_login"] is True, "liga ao login do PQI pelo e-mail (sem diferença de maiúscula)")
    ok(t.post(f"/api/agente/pendencias/{pend['id']}/atribuir", json={"email": "joao@ks"}).status_code == 400, "não atribui a quem não tem login no PQI")
    ok(t.post(f"/api/agente/pendencias/{pend['id']}/atribuir", json={"email": "fora@ks"}).status_code == 400, "não atribui a quem não está no Organograma")
    como("ana@ks")
    ok(t.post(f"/api/agente/pendencias/{pend['id']}/atribuir", json={"email": "maria.paula@ks"}).status_code == 403, "usuário comum não atribui")
    como("ger@ks", "Gerente")
    r = t.post(f"/api/agente/pendencias/{pend['id']}/atribuir", json={"email": "maria.paula@ks", "nota": "você conhece o processo"}).json()
    ok(r["fase"] == "com_responsavel" and r["atribuida"]["setor"] == "CX", "atribuída com setor e recado")

    como("ana@ks")
    ok(t.post(f"/api/agente/pendencias/{pend['id']}/proposta", json={"titulo": "x", "passos": ["y"]}).status_code == 403, "só o responsável escreve a resposta")
    como("maria.paula@ks")
    me = t.get("/api/agente/me").json()
    ok(me["comigo"] == 1 and me["pode_alimentar"] is False, "responsável vê 1 pergunta com ela (sem ser quem alimenta)")
    prop = {"titulo": "Baixa de pasta da transportadora", "passos": ["Abra o protocolo.", "Anexe o comprovante."], "palavras": "baixa pasta protocolo"}
    ok(t.post(f"/api/agente/pendencias/{pend['id']}/proposta", json=prop).json()["fase"] == "aguardando_aprovacao", "resposta enviada para aprovação")
    ok(t.get("/api/agente/me").json()["comigo"] == 0, "sai da lista dela depois de enviar")
    como("ana@ks")
    ok(t.post("/api/agente/perguntar", json={"pergunta": "baixa de pasta da transportadora"}).json()["tipo"] == "nao_sei",
       "antes da aprovação o agente ainda não responde")

    como("ger@ks", "Gerente")
    ok(t.post(f"/api/agente/pendencias/{pend['id']}/devolver", json={"motivo": "faltou o prazo"}).json()["fase"] == "com_responsavel", "devolver volta para o responsável")
    como("maria.paula@ks")
    c = t.get("/api/agente/comigo").json()["pendencias"][0]
    ok(c["proposta"]["motivo_devolucao"] == "faltou o prazo", "responsável vê o motivo da devolução")
    prop["passos"].append("Prazo: até 2 dias úteis.")
    t.post(f"/api/agente/pendencias/{pend['id']}/proposta", json=prop)
    como("ger@ks", "Gerente")
    art = t.post("/api/agente/artigos", json={**prop, "setor": "CX", "pendencia_id": pend["id"]}).json()
    ok(all(p["id"] != pend["id"] for p in t.get("/api/agente/pendencias").json()["pendencias"]), "publicar resolve a pendência")
    his = [h for h in t.get("/api/agente/historico").json()["historico"] if h["artigo_id"] == art["id"]][0]
    ok("Maria" in his["detalhe"] and "aprovada" in his["detalhe"], "histórico credita quem escreveu a resposta")
    como("ana@ks")
    r = t.post("/api/agente/perguntar", json={"pergunta": "baixa de pasta da transportadora"}).json()
    ok(r["tipo"] == "resposta" and r["fonte"]["id"] == art["id"] and len(r["passos"]) == 3, "depois de publicada, o agente responde")


def proc_carteira(passos, regras="1. Item com saldo negativo trava o pedido inteiro.", parecer=""):
    return {"id": "P1", "versao": 5, "dados": {"codigo": "PROC-001", "nome": "Carteira de Pedidos", "area": "CX", "macroprocesso": "Back Office",
            "passos_real": [{"id": "r1", "descricao": "Extrair as planilhas", "quem": "Gilberto"}], "passos_it": [],
            "validacao_tobe": {"parecer": parecer},
            "tobe": {"codigo": "IT CARTEIRA", "versao": "01", "regras": regras, "fluxo": "Sem sucesso volta no próximo dia útil.",
                     "passos": [{"id": f"s{i}", "descricao": d, "quem": q, "sistema": "Protheus", "regra": ""} for i, (d, q) in enumerate(passos, 1)]},
            "fluxo": {"tobe": {"raias": [{"id": "ra", "nome": "CX · Back Office"}, {"id": "rb", "nome": "Compras"}],
                               "nos": [{"id": "n0", "tipo": "inicio", "texto": "Início", "raia": "ra", "x": 190},
                                       {"id": "n1", "tipo": "processo", "texto": "Ajustar saldo negativo", "raia": "ra", "x": 400, "passo": "s1"},
                                       {"id": "n2", "tipo": "decisao", "texto": "Fornecedor atrasou?", "raia": "rb", "x": 600},
                                       {"id": "n3", "tipo": "processo", "texto": "Cobrar fornecedor", "raia": "rb", "x": 800, "passo": "s2"}],
                               "ligacoes": [{"de": "n0", "para": "n1"}, {"de": "n1", "para": "n2"}, {"de": "n2", "para": "n3", "rotulo": "sim"}]}}}}


def teste_fonte_unica_mapa():
    print("[v6.1] Mapa Digital é a fonte única do agente (IT + regras + fluxograma)")
    ligar_memoria()
    t = montar()
    PROCESSOS[:] = [proc_carteira([("Ajustar o saldo negativo pela tela de Documento de Saída", "CX · Back Office"),
                                   ("Cobrar o fornecedor e passar o prazo correto", "Compras")])]
    A.sincronizar_processo(PROCESSOS[0])
    art = A.P["ler"](A.COL_ART, "mapa-P1")
    ok(art and art["ativo"] and art["origem"]["codigo"] == "PROC-001", "gravar no Mapa cria o artigo do processo")
    txt = " ".join(art["passos"])
    ok("IT CARTEIRA" in art["titulo"] and "em validação" in art["passos"][0], "título com a IT; cabeçalho diz a fonte e a situação")
    ok("1. Ajustar o saldo negativo" in txt and "Quem faz: CX · Back Office" in txt and "Onde: Protheus" in txt, "passos da IT com quem faz e onde")
    ok("Regras gerais:" in txt and "saldo negativo trava" in txt, "regras gerais entram na resposta")
    ok("Fluxograma (como será)" in txt and "Fornecedor atrasou?" in txt and "sim: Cobrar fornecedor" in txt, "fluxograma lido na ordem das setas, com os rótulos")
    como("ana@ks")
    r = t.post("/api/agente/perguntar", json={"pergunta": "como ajustar saldo negativo na carteira"}).json()
    ok(r["tipo"] == "resposta" and r["fonte"]["id"] == "mapa-P1", "o agente responde pela IT do Mapa")

    print("[v6.1] mudou no Mapa → o agente muda junto")
    PROCESSOS[0] = proc_carteira([("Ajustar o saldo negativo pela tela de Documento de Saída", "CX · Back Office"),
                                  ("Cobrar o fornecedor e passar o prazo correto", "Compras"),
                                  ("Jogar em carteira na tela de Agenda os pedidos antigos", "CX")], parecer="Aprovado — passa a ser regra")
    A.sincronizar_processo(PROCESSOS[0])
    art2 = A.P["ler"](A.COL_ART, "mapa-P1")
    ok(art2["versao"] == 2 and "Agenda" in " ".join(art2["passos"]) and "aprovada" in art2["passos"][0], "nova versão do artigo com o passo novo e a IT aprovada")
    ok(art2["versoes"][0]["versao"] == 1, "a versão anterior fica guardada")
    A.sincronizar_processo(PROCESSOS[0])
    ok(A.P["ler"](A.COL_ART, "mapa-P1")["versao"] == 2, "gravar sem mudar nada não cria versão")

    print("[v6.1] o artigo do Mapa não é editado no agente")
    como("ger@ks", "Gerente")
    r = t.put("/api/agente/artigos/mapa-P1", json={"titulo": "outra coisa", "passos": ["texto solto"]})
    ok(r.status_code == 409 and "Processos" in r.json()["detail"], "editar no agente é bloqueado: manda editar em Processos")
    ok(t.post("/api/agente/artigos/mapa-P1/restaurar", json={"versao": 1}).status_code == 409, "voltar versão também só pelo Mapa")
    r = t.post("/api/agente/sincronizar-mapa").json()
    ok(len(r["artigos"]) == 1 and r["artigos"][0]["id"] == "mapa-P1", "ressincronizar tudo pelo Mapa")
    PROCESSOS.clear()
    t.post("/api/agente/sincronizar-mapa")
    ok(A.P["ler"](A.COL_ART, "mapa-P1")["ativo"] is False, "processo excluído do Mapa: o agente deixa de usar")


def teste_falta_virar_it():
    print("[v6.2] Falta virar IT: o que o agente sabe ou foi perguntado e não tem IT no Mapa")
    ligar_memoria()
    PROCESSOS.clear()
    t = montar()
    como("ger@ks", "Gerente")
    man = t.post("/api/agente/artigos", json={"titulo": "Como pedir vistoria de produtos", "setor": "Qualidade",
                                              "passos": ["Abra o chamado de vistoria", "Anexe as fotos da peça"], "palavras": "vistoria peça foto"}).json()
    como("ana@ks")
    t.post("/api/agente/perguntar", json={"pergunta": "como faço reembolso de frete?"})
    t.post("/api/agente/perguntar", json={"pergunta": "Como faço o reembolso do frete"})
    t.post("/api/agente/perguntar", json={"pergunta": "como ajustar saldo negativo no protheus"})
    como("ana@ks")
    ok(t.get("/api/agente/falta-it").status_code == 403, "só quem tem a ⚙️ vê a aba")
    como("ger@ks", "Gerente")
    f = t.get("/api/agente/falta-it").json()
    ok([a["titulo"] for a in f["artigos"]] == ["Como pedir vistoria de produtos"], "artigo manual aparece como 'falta virar IT'")
    reemb = [g for g in f["perguntas"] if "reembolso" in g["pergunta"].lower()]
    ok(len(f["perguntas"]) == 2 and reemb and reemb[0]["vezes"] == 2, "perguntas sem resposta agrupadas por assunto, com quantas vezes")
    ok(f["perguntas"][0]["vezes"] == 2, "a mais perguntada vem primeiro")
    ok(t.get("/api/agente/me").json()["falta_it"] == 3, "contador da aba no /me")

    print("[v6.2] virou IT no Mapa → a pendência fecha sozinha")
    reg = {"id": "PX", "versao": 1, "dados": {"codigo": "PROC-001", "nome": "Carteira de Pedidos", "area": "CX",
           "tobe": {"codigo": "IT CARTEIRA", "passos": [{"id": "a", "descricao": "Ajustar o saldo negativo no Protheus pela tela de Documento de Saída", "quem": "CX"}]}}}
    PROCESSOS.append(reg)
    A.sincronizar_processo(reg)
    f = t.get("/api/agente/falta-it").json()
    ok(not any("saldo" in g["pergunta"] for g in f["perguntas"]), "pergunta que a IT agora responde saiu da lista")
    pen = [p for p in A.P["listar"](A.COL_PEN) if "saldo" in p["pergunta"]][0]
    ok(pen["status"] == "resolvida" and "IT do Mapa" in pen["nota"], "pendência fechada com a nota 'respondida pela IT do Mapa'")

    print("[v6.2] levar para o Mapa")
    r = t.post("/api/agente/falta-it/criar-processo", json={"nome": "Vistoria de produtos", "area": "Qualidade", "artigo_id": man["id"]}).json()
    novo = PROCESSOS[-1]["dados"]
    ok(r["processo"]["codigo"] == "PROC-002" and len(novo["tobe"]["passos"]) == 2, "artigo vira processo novo no Mapa com os passos na nova IT")
    ok(novo["amarracao"]["tobe"]["ativo"] and novo["amarracao"]["asis"]["ativo"], "processo já nasce com IT e fluxograma amarrados")
    ok(A.P["ler"](A.COL_ART, man["id"])["ativo"] is False, "o artigo manual sai de cena: o agente responde pela IT")
    como("ana@ks")
    resp = t.post("/api/agente/perguntar", json={"pergunta": "como pedir vistoria de produtos"}).json()
    ok(resp["fonte"]["id"].startswith("mapa-") and "PROC-002" in resp["fonte"]["titulo"], "a resposta agora vem da IT do Mapa")
    como("ger@ks", "Gerente")
    g = [g for g in t.get("/api/agente/falta-it").json()["perguntas"] if "reembolso" in g["pergunta"].lower()][0]
    t.post("/api/agente/falta-it/criar-processo", json={"nome": "Reembolso de frete", "area": "CX", "pendencias": g["ids"]})
    f = t.get("/api/agente/falta-it").json()
    ok(f["perguntas"][0]["processo"]["codigo"] == "PROC-003" and not f["artigos"], "pergunta virou processo: fica na lista como 'falta escrever a IT'")

    print("[v6.2] ligar a um processo que já existe (sem duplicar)")
    PROCESSOS.append({"id": "PV", "versao": 3, "dados": {"codigo": "PROC-009", "nome": "Vistoria de Peças", "area": "Logística", "tobe": {"passos": []}}})
    man2 = t.post("/api/agente/artigos", json={"titulo": "Como registrar a vistoria da peça", "passos": ["Abra a Vistoria", "Fotografe a etiqueta"],
                                               "palavras": "vistoria etiqueta"}).json()
    f = t.get("/api/agente/falta-it").json()
    ok(any(x["codigo"] == "PROC-009" and not x["tem_it"] for x in f["processos"]), "a aba lista os processos do Mapa (e quem ainda não tem IT)")
    r = t.post("/api/agente/falta-it/criar-processo", json={"artigo_id": man2["id"], "processo_id": "PV"}).json()
    pv = [x for x in PROCESSOS if x["id"] == "PV"][0]
    ok(r["processo"]["codigo"] == "PROC-009" and r["passos"] == 2 and len(pv["dados"]["tobe"]["passos"]) == 2, "IT vazia: os passos do artigo viram a IT do processo existente")
    ok(pv["dados"]["amarracao"]["tobe"]["ativo"] and len(PROCESSOS) == 4, "não cria processo duplicado; IT já amarrada")
    ok(A.P["ler"](A.COL_ART, man2["id"])["ativo"] is False, "o artigo manual sai de cena")

    print("[v6.2] ver a resposta do agente e importar no fim de uma IT que já existe")
    man3 = t.post("/api/agente/artigos", json={"titulo": "Como conferir a etiqueta da vistoria", "passos": ["Leia a etiqueta", "Confira o lote"],
                                               "palavras": "etiqueta lote vistoria conferir"}).json()
    f = t.get("/api/agente/falta-it").json()
    a3 = [a for a in f["artigos"] if a["id"] == man3["id"]][0]
    ok(a3["resposta"] == ["Leia a etiqueta", "Confira o lote"], "a aba mostra a resposta do agente para conferir antes de importar")
    r = t.post("/api/agente/falta-it/criar-processo", json={"artigo_id": man3["id"], "processo_id": "PV"}).json()
    pv = [x for x in PROCESSOS if x["id"] == "PV"][0]
    descr = [x["descricao"] for x in pv["dados"]["tobe"]["passos"]]
    ok(r["acrescentou"] and descr == ["Abra a Vistoria", "Fotografe a etiqueta", "Leia a etiqueta", "Confira o lote"], "IT já existia: os passos entram no fim, sem apagar nada")
    ok(A.P["ler"](A.COL_ART, man3["id"])["ativo"] is False, "importado: o artigo sai de cena")


def teste_busca_por_radical():
    print("[v6.3.2] Variações da mesma palavra (libero × liberação) acham a IT")
    ligar_memoria()
    t = montar()
    como("ger@ks", "Gerente")
    t.post("/api/agente/artigos", json={"titulo": "Carteira de Pedidos — IT CARTEIRA (PROC-001)", "palavras": "carteira pedidos",
                                        "passos": ["Tratar os pedidos que NÃO LIBERARAM: a liberação é automática; conferir o empenho no Documento de Saída"]})
    como("ana@ks")
    r = t.post("/api/agente/perguntar", json={"pergunta": "como libero um pedido"}).json()
    ok(r["tipo"] == "resposta" and "CARTEIRA" in r["fonte"]["titulo"], "'como libero um pedido' acha a IT (libero × liberação/liberaram)")
    r = t.post("/api/agente/perguntar", json={"pergunta": "qual a cor do uniforme da empresa"}).json()
    ok(r["tipo"] == "nao_sei", "pergunta sem relação continua 'não sei'")


def teste_humano_memoria_exemplos_ajustar():
    print("[v6.4] Conversa lembrada, exemplos aprovados (👍) e IA mais humana")
    ligar_memoria()
    PROCESSOS.clear()
    t = montar()
    reg = {"id": "PC", "versao": 1, "dados": {"codigo": "PROC-001", "nome": "Carteira de Pedidos", "area": "CX",
           "tobe": {"codigo": "IT CARTEIRA", "regras": "1. Item com saldo negativo trava o pedido inteiro.",
                    "passos": [{"id": "s8", "descricao": "Na tela de Agenda, colocar o pedido em carteira com a data que o cliente quer receber.", "quem": "CX"},
                               {"id": "s9", "descricao": "Compras cobra o fornecedor e passa o prazo correto.", "quem": "Compras"}]}}}
    PROCESSOS.append(reg)
    A.sincronizar_processo(reg)
    como("ana@ks")
    r = t.post("/api/agente/perguntar", json={"pergunta": "e se o cliente não atender?",
                                              "historico": [{"p": "como coloco um pedido em carteira na agenda", "r": "Na tela de Agenda..."}]}).json()
    ok(r["tipo"] == "resposta" and r["fonte"]["id"] == "mapa-PC", "pergunta de continuação usa o assunto da conversa para achar a IT")
    enviado = {}
    A.provedor = lambda: "groq"
    os.environ["GROQ_API_KEY"] = "gsk_teste"
    def falsa(url, corpo, cab, timeout=40):
        enviado["user"] = corpo["messages"][1]["content"]; enviado["system"] = corpo["messages"][0]["content"]; enviado["temp"] = corpo["temperature"]
        return {"choices": [{"message": {"content": "Você abre a Agenda e coloca o pedido em carteira.\nFONTE: 1"}}]}
    A._http_json = falsa
    r1 = t.post("/api/agente/perguntar", json={"pergunta": "como coloco o pedido em carteira na agenda?"}).json()
    ok(r1["modo"] == "ia" and "próprias palavras".upper() in enviado["system"].upper() or "SUAS palavras" in enviado["system"], "instrução: colega humano, com as próprias palavras")
    ok("NUNCA invente nome de tela, botão, campo" in enviado["system"] and enviado["temp"] == 0.4, "pode concluir, mas não inventa tela/botão/campo")
    t.post("/api/agente/retorno", json={"pergunta_id": r1["pergunta_id"], "util": True})
    ok(len(A.P["listar"](A.COL_EX)) == 1, "👍 numa resposta da IA vira exemplo aprovado")
    t.post("/api/agente/perguntar", json={"pergunta": "como colocar pedido em carteira pela agenda",
                                          "historico": [{"p": "oi", "r": "Olá!"}]})
    ok("EXEMPLOS APROVADOS" in enviado["user"] and "Você abre a Agenda" in enviado["user"], "pergunta parecida leva o exemplo aprovado para a IA")
    ok("CONVERSA ANTERIOR" in enviado["user"], "a conversa anterior vai junto para a IA")

    print("[v6.4] ✏️ Ajustar DE → PARA")
    ok(t.post("/api/agente/ajustar", json={"artigo_id": "mapa-PC", "de": "x", "para": "y"}).status_code == 403, "só quem tem a ⚙️ ajusta")
    como("ger@ks", "Gerente")
    r = t.post("/api/agente/ajustar", json={"artigo_id": "mapa-PC", "de": "colocar o pedido em carteira com a data que o cliente quer receber",
                                            "para": "selecione o pedido, escreva nas observações internas a data prevista (ou CARTEIRA), marque NÃO no agendamento e salve",
                                            "pergunta_id": r1["pergunta_id"]}).json()
    d = [x for x in PROCESSOS if x["id"] == "PC"][0]["dados"]
    ok(r["modo"] == "texto" and "passo 1" in r["onde"] and "observações internas" in d["tobe"]["passos"][0]["descricao"], "trecho está na IT: troca feita no próprio passo do Mapa")
    ok(A.ultimo_artigo_txt("mapa-PC").count("observações internas") >= 1 and A.P["ler"](A.COL_EX, r1["pergunta_id"]) is not None, "o agente já responde com o texto novo")
    ok(not A.exemplos_para("como coloco o pedido em carteira na agenda", [A.P["ler"](A.COL_ART, "mapa-PC")]), "exemplo antigo deixa de valer quando a IT muda")
    r = t.post("/api/agente/ajustar", json={"artigo_id": "mapa-PC", "de": "escolha a opção Colocar em Carteira",
                                            "para": "não existe essa opção: siga as observações internas e o agendamento NÃO"}).json()
    d = [x for x in PROCESSOS if x["id"] == "PC"][0]["dados"]
    ok(r["modo"] == "correcao" and d["ajustes_agente"][0]["de"] == "escolha a opção Colocar em Carteira", "invenção da IA (não está na IT) vira correção oficial no Mapa")
    ok("CORREÇÕES (valem mais" in A.ultimo_artigo_txt("mapa-PC"), "a correção entra no topo do que o agente lê")
    man = t.post("/api/agente/artigos", json={"titulo": "Como pedir vistoria", "passos": ["Abra o chamado X"], "palavras": "vistoria"}).json()
    r = t.post("/api/agente/ajustar", json={"artigo_id": man["id"], "de": "chamado X", "para": "chamado de Vistoria no Concierge"}).json()
    art = A.P["ler"](A.COL_ART, man["id"])
    ok(r["modo"] == "texto" and art["passos"] == ["Abra o chamado de Vistoria no Concierge"] and art["versao"] == 2, "artigo manual: troca no passo, com versão nova")


def teste_fonte_da_verdade_fluxo_interno():
    print("[v7.1] Fonte da verdade = Fluxo interno: o que VALE hoje vem primeiro")
    base = {"codigo": "PROC-050", "nome": "Triagem", "area": "Qualidade", "dono": "Rita", "origem": {"dep": "q", "subsetor": "Retornos"},
            "passos_real": [{"descricao": "Olhar a peça"}], "passos_it": [{"descricao": "Conferir lacre"}], "it": {"codigo": "IT-50"},
            "tobe": {"passos": [{"descricao": "Escanear a etiqueta"}]}, "validacao_tobe": {"parecer": ""}}
    ok(A.it_vigente(base)[0] == "it", "IT oficial vale mais que a nova IT ainda em validação")
    ok(A.it_vigente({**base, "validacao_tobe": {"parecer": "Aprovado"}})[0] == "tobe", "nova IT aprovada passa a valer")
    ok(A.it_vigente({**base, "passos_it": []})[4] == "nova IT ainda em validação", "sem IT oficial, a nova IT entra avisando que está em validação")
    ok("ainda sem IT" in A.it_vigente({**base, "passos_it": [], "tobe": {}})[4], "só o como é feito hoje: avisa que não há IT")
    art = A.artigo_do_processo({"id": "PX", "dados": base})
    ok("Fluxo interno" in art["passos"][0] and "Setor: Qualidade · Subsetor: Retornos · Responsável: Rita." in art["passos"], "artigo diz a fonte, o setor, o subsetor e o responsável")


def teste_tudo_vira_base():
    print("[v7.3] TUDO o que está escrito vira base: Organograma, Atividades, Processos, Melhoria")
    ligar_memoria()
    PROCESSOS.clear()
    org = [{"id": "log", "dados": {"nome": "Logística", "descricao": "Entrega e expedição", "pessoas": [
        {"id": "g", "nome": "Carlos Souza", "cargo": "Gerente de Logística", "nivel": "B", "email": "carlos@ks", "ramal": "2201", "x": 10},
        {"id": "s", "nome": "Bia Lima", "cargo": "Supervisora", "nivel": "C", "subsetor": "Expedição", "gestor_id": "g"},
        {"id": "c", "nome": "Davi Rocha", "cargo": "Conferente", "nivel": "D", "subsetor": "Expedição", "gestor_id": "s", "apelido": "Davizão"},
        {"id": "v", "nome": "", "cargo": "Conferente", "vaga": True, "subsetor": "Expedição", "gestor_id": "s"}]}},
           {"id": "niv", "dados": {"_tipo": "niveis"}}]
    diag = {"log": {"inventario": [{"atividade": "Agrupamento", "subsetor": "Expedição", "executores": "Davi, Bia Lima",
                                    "modo": "Individual", "categoria": "Operacional",
                                    "descricao": "Juntar os pedidos da mesma rota no mesmo palete antes do carregamento."}],
                    "gemba": {"observacoes": [{"onde": "doca 3", "vi": "palete sem etiqueta"}]}}}
    PROCESSOS.append({"id": "P1", "versao": 1, "dados": {"codigo": "PROC-007", "nome": "Agrupamento", "area": "Logística", "dono": "Bia Lima",
                      "objetivo": "Reduzir viagens", "origem": {"dep": "log", "atividade": "Agrupamento"},
                      "oportunidades": [{"texto": "usar leitor de código de barras"}],
                      "passos_real": [{"descricao": "Separar por rota"}]}})
    cards = [{"id": "m1", "dados": {"codigo": "MLH-001", "titulo": "Etiqueta no palete", "status": "Em andamento", "indicador_id": "i1",
                                    "problema": "palete sem etiqueta na doca"}}]
    inds = [{"id": "i1", "dados": {"nome": "Paletes sem etiqueta por dia", "unidade": "un", "lancamentos": [{"data": "2026-10-01", "valor": 12}]}}]
    FONTES = {"setores": org, "diag": diag, "processos": PROCESSOS, "melhoria_cards": cards, "melhoria_indicadores": inds}
    app = FastAPI()
    A.ligar(app, usuario_atual=lambda request: dict(USUARIO),
            nome_perfil=lambda u: "Administrador" if u.get("papel") == "admin" else u.get("_perfil_nome"),
            processos=lambda: list(PROCESSOS), criar_processo=criar_proc, salvar_processo=salvar_proc,
            setores=lambda: org, usuarios=lambda: CONTAS, fontes=lambda: FONTES)
    t = TestClient(app)
    A.esquecer_cache()
    ids = {a["id"]: a for a in A.artigos()}
    setor = ids.get("org-log")
    ok(setor and any("GESTOR" in p and "Carlos Souza" in p for p in setor["passos"]), "setor: o topo do organograma é o gestor")
    ok(any("2201" in p for p in setor["passos"]) and any("Davizão" in p for p in setor["passos"]), "todos os campos da pessoa (até campo novo) viram base")
    ok(not any("10" == p for p in setor["passos"]) and not any("x: 10" in p for p in setor["passos"]), "posição na tela não é conteúdo")
    ok(any("Responsável pelo subsetor Expedição: Bia Lima" in p for p in setor["passos"]), "responsável de cada subsetor")
    ok(A.buscar("quem é o gestor da logistica")[0]["id"] == "org-log", "“quem é o gestor da logística” acha o organograma do setor")
    ativ = A.buscar("o que é agrupamento")
    ok(any(a["id"].startswith("ativ-log") for a in ativ[:2]), "“o que é agrupamento” acha a atividade")
    a_at = ids.get("ativ-log-agrupamento")
    ok(a_at and any("Juntar os pedidos" in p for p in a_at["passos"]) and any("Davi, Bia Lima" in p for p in a_at["passos"])
       and any("PROC-007" in p for p in a_at["passos"]), "atividade: o que é, quem executa e o processo ligado")
    davi = ids.get("pes-log-c")
    ok(davi and any("Agrupamento" in p for p in davi["passos"]) and any("Expedição" in p for p in davi["passos"]), "relação cruzada: a pessoa e as atividades dela")
    bia = ids.get("pes-log-s")
    ok(bia and any("Respondem a Bia Lima: Davi Rocha" in p for p in bia["passos"]) and any("PROC-007" in p for p in bia["passos"]),
       "relação cruzada: quem responde a ela e os processos de que é responsável")
    ok(any("palete sem etiqueta" in p for p in ids["diag-log-gemba"]["passos"]), "outras seções do diagnóstico, campo a campo")
    ok(any("leitor de código de barras" in p for p in ids["proc-P1"]["passos"]), "ficha completa do processo (oportunidades…)")
    ok(any("Paletes sem etiqueta" in p for p in ids["mlh-m1"]["passos"]) and "ind-i1" in ids, "Melhoria Contínua: melhorias e indicadores")
    ok(not any(a.get("gerado") for a in A.artigos_salvos()), "artigos gerados não são gravados (sempre refeitos da fonte)")

    print("[v7.3] mudou a fonte → muda a resposta")
    v1 = ids["org-log"]["versao"]
    org[0]["dados"]["pessoas"][0]["nome"] = "Carla Dias"
    A.esquecer_cache()
    novo = next(a for a in A.artigos() if a["id"] == "org-log")
    ok(novo["versao"] != v1 and any("Carla Dias" in p for p in novo["passos"]), "trocou o gestor no Organograma: o agente sabe na hora")

    print("[v7.3] mesma pergunta = mesma resposta; 👎 → pendência; ajuste vira a verdade para todos")
    os.environ.update(AGENTE_IA="groq", GROQ_API_KEY="teste")
    chamadas = {"n": 0}
    def falsa(url, corpo, cab, timeout=40):
        chamadas["n"] += 1
        chamadas["user"] = corpo["messages"][1]["content"]
        return {"choices": [{"message": {"content": "O gestor da Logística é a Carla Pires.\nFONTE: 1"}}]}
    orig = A._http_json
    A._http_json = falsa
    try:
        como("ana@ks")
        r1 = t.post("/api/agente/perguntar", json={"pergunta": "Quem é o gestor da Logística?"}).json()
        ok(r1["modo"] == "ia" and r1["fonte"]["id"] == "org-log" and "Carla Dias" in chamadas["user"], "IA recebe o organograma")
        como("rui@ks")
        r2 = t.post("/api/agente/perguntar", json={"pergunta": "quem e o gestor da logistica"}).json()
        ok(r2.get("mesma_resposta") and r2["texto"] == r1["texto"] and chamadas["n"] == 1, "outra pessoa, mesma pergunta: mesma resposta")
        t.post("/api/agente/retorno", json={"pergunta_id": r2["pergunta_id"], "util": False, "obs": "o nome está errado"})
        pend = [p for p in A._abertas() if p.get("pergunta_id") == r2["pergunta_id"]]
        ok(pend and pend[0]["motivo"] == "nao_resolveu", "👎 vai para as pendências")
        ok(A.P["ler"](A.COL_RESP, A.chave_pergunta("quem e o gestor da logistica"))["ruim"], "👎: a resposta deixa de valer para todos")
        como("ger@ks", "Gerente")
        r = t.post("/api/agente/ajustar", json={"artigo_id": "org-log", "de": "Carla Pires", "para": "Carla Dias, Gerente de Logística",
                                                "pergunta_id": r2["pergunta_id"]}).json()
        ok(r["modo"] == "correcao" and "Organograma" in r["onde"], "ajuste de artigo gerado vira correção oficial")
        org_c = next(a for a in A.artigos() if a["id"] == "org-log")
        ok("CORREÇÕES" in org_c["passos"][0] and "Carla Dias, Gerente de Logística" in org_c["passos"][0], "a correção entra no topo do artigo")
        como("lia@ks")
        r3 = t.post("/api/agente/perguntar", json={"pergunta": "quem é o gestor da logística"}).json()
        ok(r3.get("mesma_resposta") and "Carla Dias, Gerente de Logística" in r3["texto"] and chamadas["n"] == 1,
           "a resposta ajustada vale para quem perguntar a mesma coisa")
        ok(not [p for p in A._abertas() if p.get("pergunta_id") == r2["pergunta_id"]], "a pendência fecha com o ajuste")
        org[0]["dados"]["descricao"] = "Entrega, expedição e frota"
        A.esquecer_cache()
        t.post("/api/agente/perguntar", json={"pergunta": "quem é o gestor da logística"})
        ok(chamadas["n"] == 2, "mudou a fonte: a resposta é refeita")
    finally:
        A._http_json = orig
        os.environ.pop("AGENTE_IA", None); os.environ.pop("GROQ_API_KEY", None)


if __name__ == "__main__":
    teste_tudo_vira_base()
    teste_fonte_da_verdade_fluxo_interno()
    teste_permissoes()
    teste_perguntas()
    teste_ia_simulada()
    teste_atribuir()
    teste_fonte_unica_mapa()
    teste_falta_virar_it()
    teste_busca_por_radical()
    teste_humano_memoria_exemplos_ajustar()
    print()
    print("TUDO CERTO ✔" if not FALHAS else f"{len(FALHAS)} FALHA(S): " + "; ".join(FALHAS))
    sys.exit(1 if FALHAS else 0)
