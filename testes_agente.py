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
USUARIO: dict = {}
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
            nome_perfil=lambda u: "Administrador" if u.get("papel") == "admin" else u.get("_perfil_nome"))
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
        r = t.post("/api/agente/perguntar", json={"pergunta": "como importar a planilha?"}).json()
        ok(r["tipo"] == "resposta" and r["modo"] == "base", "IA fora do ar: responde pela base")
    finally:
        A._http_json = original
        os.environ.pop("AGENTE_IA", None); os.environ.pop("GROQ_API_KEY", None)


if __name__ == "__main__":
    teste_permissoes()
    teste_perguntas()
    teste_ia_simulada()
    print()
    print("TUDO CERTO ✔" if not FALHAS else f"{len(FALHAS)} FALHA(S): " + "; ".join(FALHAS))
    sys.exit(1 if FALHAS else 0)
