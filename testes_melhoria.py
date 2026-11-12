"""
testes_melhoria.py — Melhoria Contínua (v7.2) SEM Firestore e SEM IA externa.

    python testes_melhoria.py

Cobre: prova antes × depois (comprovada, variação normal, piorou, poucos dados, sentido "menor"),
ganho em R$ e em tempo, card ligado ao Fluxo interno (código, etapa, histórico), indicador
solicitado → delegado → preenchido → ativo, lançamentos (colar, data repetida, inválidos),
pendências (pedido, lançamento atrasado, ação), sugestões do Fluxo interno e acesso para todos.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import melhoria as M  # noqa: E402
import modulos as mods  # noqa: E402

OK = 0
BANCO: dict = {}
USUARIO = {"email": "ana@ks", "nome": "Ana"}
NIVEL = {"melhoria": 2}
AGORA = {"v": "2026-11-12T12:00:00+00:00"}


def ok(cond, txt):
    global OK
    assert cond, txt
    OK += 1
    print("  ✓", txt)


def col(c):
    return BANCO.setdefault(c, {})


def pub(i, r):
    return {"id": i, "dados": r["dados"], "versao": r["versao"]}


def criar(c, d, u, e=None):
    i = f"{c[:3]}{len(col(c)) + 1}"
    col(c)[i] = {"dados": d, "versao": 1}
    return pub(i, col(c)[i])


def salvar(c, i, d, v, u):
    r = col(c).get(i)
    if not r:
        return "nao_existe", None
    if v is not None and v != r["versao"]:
        return "conflito", pub(i, r)
    r.update(dados=d, versao=r["versao"] + 1)
    return "ok", pub(i, r)


PROCS = [{"id": "P1", "dados": {"codigo": "PROC-006", "nome": "Triagem de retornos", "area": "Qualidade", "dono": "Rita",
                                 "origem": {"dep": "qual", "subsetor": "Retornos"},
                                 "passos_it": [{"id": "i1", "descricao": "Conferir lacre"}, {"id": "i2", "descricao": "Registrar"}],
                                 "passos_real": [{"descricao": "Olhar a peça", "it_ref": "i1", "aderencia": "Divergente", "tempo_min": "30", "vezes_mes": "40"},
                                                 {"descricao": "Anotar no caderno", "tempo_min": "5", "vezes_mes": "40"}]}},
         {"id": "CFG", "dados": {"_tipo": "arquitetura"}}]
SETORES = [{"id": "qual", "dados": {"nome": "Qualidade", "pessoas": [{"nome": "Rita", "email": "rita@ks", "cargo": "Coordenadora", "nivel": "C"},
                                                                     {"nome": "Léo", "email": "leo@ks"}]}}]


SINC: list = []


def salvar_proc(pid, dados, versao, usuario):
    for i, r in enumerate(PROCS):
        if r["id"] == pid:
            v = r.get("versao", 1)
            if versao is not None and versao != v:
                return "conflito", r
            PROCS[i] = {**r, "dados": dados, "versao": v + 1}
            return "ok", PROCS[i]
    return "nao_existe", None


def montar():
    app = FastAPI()
    M.H.clear()
    M.ligar(app, usuario_atual=lambda r: USUARIO, niveis=lambda u: NIVEL, nome=lambda r: USUARIO["nome"], setores_do=lambda u: SETORES,
            processos=lambda: PROCS, docs_listar=lambda c: [pub(i, r) for i, r in col(c).items()], docs_criar=criar, docs_salvar=salvar,
            docs_ler=lambda c, i: pub(i, col(c)[i]) if i in col(c) else None, docs_excluir=lambda c, i, u: bool(col(c).pop(i, None)),
            ia=lambda s, u: None, ia_ligada=lambda: False, agora=lambda: AGORA["v"],
            salvar_processo=salvar_proc, sincronizar=lambda r: SINC.append(r["id"]))
    return TestClient(app)


def como(email, nome):
    USUARIO.clear(); USUARIO.update(email=email, nome=nome)


def meds(lista):
    return [{"data": d, "valor": v} for d, v in lista]


print("Prova antes × depois")
antes = meds([("2026-10-01", 30), ("2026-10-08", 32), ("2026-10-15", 31), ("2026-10-22", 29)])
r = M.comparar(antes + meds([("2026-11-01", 20), ("2026-11-08", 19), ("2026-11-15", 21), ("2026-11-22", 20)]), "2026-11-01", "menor")
ok(r["situacao"] == "comprovada" and round(r["variacao_pct"]) == -34, "tempo caiu de 30,5 para 20 min: comprovada (−34%)")
r = M.comparar(antes + meds([("2026-11-01", 29), ("2026-11-08", 30), ("2026-11-15", 31), ("2026-11-22", 29)]), "2026-11-01", "menor")
ok(r["situacao"] == "variacao_normal", "melhorou pouco: pode ser variação normal")
r = M.comparar(antes + meds([("2026-11-01", 40), ("2026-11-08", 41), ("2026-11-15", 39), ("2026-11-22", 40)]), "2026-11-01", "menor")
ok(r["situacao"] == "piorou", "subiu num indicador 'menor é melhor': piorou")
r = M.comparar(antes + meds([("2026-11-01", 20)]), "2026-11-01", "menor")
ok(r["situacao"] == "poucos_dados" and "3 valor(es) DEPOIS" in r["texto"], "poucos dados: diz quantos faltam")
r = M.comparar(antes, "", "menor")
ok(r["situacao"] == "sem_mudanca" and r["antes"]["n"] == 4, "sem data da mudança: tudo é linha de base")
r = M.comparar(meds([("01/10/2026", "80%"), ("08/10/2026", "80"), ("15/10/2026", "80"), ("22/10/2026", "80"),
                     ("01/11/2026", "90"), ("08/11/2026", "91"), ("15/11/2026", "92"), ("22/11/2026", "90")]), "01/11/2026", "maior")
ok(r["situacao"] == "comprovada", "datas BR, '%' e antes sem variação: maior é melhor, comprovada")

print("Ganho")
g = M.ganhos({"custo": {"antes": "1,00", "depois": "0,50", "qtd_mes": "1000"}, "tempo": {"min_antes": 30, "min_depois": 20, "vezes_mes": 120, "custo_hora": 40}})
ok(g["custo"]["reducao_pct"] == 50 and g["custo"]["mes"] == 500 and g["custo"]["ano"] == 6000, "R$ 1,00 → 0,50 em 1.000/mês: 50%, R$ 500/mês, R$ 6.000/ano")
ok(g["tempo"]["horas_mes"] == 20 and g["tempo"]["reais_mes"] == 800, "30 → 20 min, 120 vezes: 20 h/mês = R$ 800")

print("Card ligado ao Fluxo interno")
BANCO.clear()
t = montar()
b = t.get("/api/melhoria/base").json()
ok(b["processos"][0]["gaps"] == {"sem_it": 1, "divergente": 1, "nao_feito": 1} and b["processos"][0]["horas_mes"] == 23.3, "lê gaps e horas do Fluxo interno")
ok(t.post("/api/melhoria/cards", json={"dados": {"titulo": "x"}}).status_code == 400, "sem processo: recusa")
c = t.post("/api/melhoria/cards", json={"dados": {"titulo": "Triagem mais rápida", "processo_id": "P1"}}).json()
ok(c["dados"]["codigo"] == "MC-001" and c["dados"]["etapa"] == "planejar" and c["dados"]["setor_nome"] == "Qualidade"
   and c["dados"]["subsetor"] == "Retornos", "nasce MC-001, em Planejar, com setor e subsetor do processo")
d = {**c["dados"], "etapa": "fazer", "data_mudanca": "2026-11-01", "sem_indicador": True}
c2 = t.put(f"/api/melhoria/cards/{c['id']}", json={"dados": d, "versao": c["versao"]}).json()
txts = [h["texto"] for h in c2["dados"]["historico"]]
ok("Etapa: Planejar → Fazer" in txts and any("SEM indicador" in x for x in txts) and any("Data da mudança" in x for x in txts), "histórico registra etapa, data da mudança e 'sem indicador'")
ok(t.put(f"/api/melhoria/cards/{c['id']}", json={"dados": d, "versao": 1}).status_code == 409, "versão velha: conflito")
ok(t.delete(f"/api/melhoria/cards/{c['id']}").status_code == 403, "Adicionar não exclui")

print("Indicador: pedido ao gestor → delegado → preenchido")
i = t.post("/api/melhoria/indicadores", json={"dados": {"processo_id": "P1", "processo_nome": "Triagem de retornos"}}).json()
ok(i["dados"]["status"] == "solicitado", "indicador sem nome nasce 'solicitado'")
t.post(f"/api/melhoria/indicadores/{i['id']}/pedido", json={"para_nome": "Rita", "para_email": "rita@ks", "mensagem": "precisamos medir a triagem"})
como("rita@ks", "Rita")
p = t.get("/api/melhoria/pendencias").json()["pendencias"]
ok(p and p[0]["tipo"] == "pedido_indicador" and "precisamos medir" in p[0]["detalhe"], "gestora vê o pedido nas pendências dela")
ok(t.post(f"/api/melhoria/indicadores/{i['id']}/medicoes", json={"valores": [{"data": "01/10", "valor": "3"}]}).status_code == 400, "não lança antes de preencher")
t.post(f"/api/melhoria/indicadores/{i['id']}/delegar", json={"para_nome": "Léo", "para_email": "leo@ks"})
ok(not t.get("/api/melhoria/pendencias").json()["pendencias"], "depois de delegar, sai das pendências dela")
como("leo@ks", "Léo")
ok(t.get("/api/melhoria/pendencias").json()["pendencias"][0]["tipo"] == "pedido_indicador", "e aparece para quem recebeu")
x = BANCO["melhoria_indicadores"][i["id"]]
dados = {**x["dados"], "nome": "Tempo médio de triagem", "unidade": "min", "sentido": "menor", "frequencia": "semanal", "quem_mede": "Léo", "quem_mede_email": "leo@ks"}
r = t.put(f"/api/melhoria/indicadores/{i['id']}", json={"dados": dados, "versao": x["versao"]}).json()
ok(r["dados"]["status"] == "ativo" and r["dados"]["pedido"]["delegado"]["nome"] == "Léo", "preenchido: fica ATIVO (o pedido fica no histórico)")

print("Lançamentos")
r = t.post(f"/api/melhoria/indicadores/{i['id']}/medicoes", json={"valores": [{"data": "01/10/2026", "valor": "30"}, {"data": "08/10/2026", "valor": "32,5"},
                                                                             {"data": "xx", "valor": "1"}, {"data": "01/10/2026", "valor": "31"}]}).json()
ms = r["indicador"]["dados"]["medicoes"]
ok(r["lancados"] == 2 and len(r["erros"]) == 1 and len(ms) == 2 and ms[0]["valor"] == 31, "cola várias linhas; inválida é avisada; mesma data: vale o último")
AGORA["v"] = "2026-11-30T12:00:00+00:00"
p = t.get("/api/melhoria/pendencias").json()["pendencias"]
ok(any(x["tipo"] == "lancar" for x in p), "lançamento atrasado vira pendência de quem mede")

print("Pendência de ação, sugestões e acesso")
c = t.post("/api/melhoria/cards", json={"dados": {"titulo": "Caderno → ERP", "processo_id": "P1", "acoes": [{"o_que": "Criar campo no ERP", "quem": "Léo", "quando": "2026-11-20"}]}}).json()
p = [x for x in t.get("/api/melhoria/pendencias").json()["pendencias"] if x["tipo"] == "acao"]
ok(p and p[0]["detalhe"].startswith("ATRASADA"), "ação minha atrasada aparece")
s = t.get("/api/melhoria/sugestoes").json()["sugestoes"]
ok({x["tipo"] for x in s} >= {"gap", "tempo"} and all(x["ja_tem_melhoria"] for x in s), "sugestões: gaps e tempo do Fluxo interno (marcando o que já tem melhoria)")
ok(len(t.post("/api/melhoria/ia/indicador", json={"processo_id": "P1", "ganhos": ["tempo", "dinheiro"]}).json()["sugestoes"]) == 2, "sem IA: sugere indicadores-padrão pelo ganho")
n = mods.niveis_do_usuario({"email": "z@ks"}, {"niveis": {}}, {})
ok(n["melhoria"] == 2, "Melhoria é para todos: sem configuração, entra com Adicionar")
n = mods.niveis_do_usuario({"email": "z@ks"}, {"niveis": {"melhoria": 0}}, {})
ok(n["melhoria"] == 0, "o admin ainda pode tirar o acesso")
print("Como vai ficar (fases 4–7) editado de dentro da melhoria")
t = montar()
proc = t.get("/api/melhoria/processos").json()[0]
d = {**proc["dados"], "nome": "NOME TROCADO", "passos_real": [], "tobe": {"passos": [{"id": "t1", "descricao": "Escanear a etiqueta"}]},
     "validacao_tobe": {"parecer": "Aprovado"}, "fases": {"f2": {"concluido": False}, "f5": {"concluido": True}}, "fluxo": {"asis": {"nos": []}, "tobe": {"nos": [{"id": "x"}]}}}
r = t.put("/api/melhoria/processos/P1", json={"dados": d, "versao": proc.get("versao", 1)})
nd = r.json()["dados"]
ok(r.status_code == 200 and nd["nome"] == "Triagem de retornos" and len(nd["passos_real"]) == 2, "da melhoria não se mexe no como é feito hoje (nome e passos reais ficam)")
ok(nd["tobe"]["passos"][0]["descricao"] == "Escanear a etiqueta" and nd["fases"]["f5"]["concluido"] and "f2" not in nd["fases"], "grava só TO BE, validação e fases 4–7")
ok(nd["fluxo"]["tobe"]["nos"] and "asis" not in nd["fluxo"], "fluxograma: só o TO BE")
ok(SINC == ["P1"], "TO BE aprovado sincroniza o Agente")
ok(t.put("/api/melhoria/processos/P1", json={"dados": d, "versao": 1}).status_code == 409, "versão velha: conflito")
tb = t.get("/api/melhoria/processos/P1/tobe").json()
ok(tb["passos"] == 1 and tb["aprovado"] and tb["simbolos_tobe"] == 1, "resumo do TO BE para o card")

raiz = os.path.dirname(os.path.abspath(__file__))
main = open(os.path.join(raiz, "main.py"), encoding="utf-8").read()
mp = open(os.path.join(raiz, "mapa.html"), encoding="utf-8").read()
ok("MODO_MELHORIA" in mp and '"/melhoria-solucao": "mapa.html"' in main, "a tela das fases 4–7 é servida pela Melhoria")
ok(mods.modulo_da_rota("/melhoria-solucao")["id"] == "melhoria", "protegida pelo acesso da Melhoria (para todos)")
ok('"/melhoria": "melhoria.html"' in main and "mlh.ligar(" in main and os.path.exists(os.path.join(raiz, "melhoria.html")), "página e rotas ligadas")
print(f"\n{OK} verificações ok.")
