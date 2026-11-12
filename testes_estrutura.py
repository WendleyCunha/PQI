"""
testes_estrutura.py — mudança de estrutura no Organograma (v6.5), sem Firestore.

    python testes_estrutura.py

Cobre: subsetor vira setor (pessoas, responsável, vínculos, Ponto, atividades e processos),
subsetor vai para outro setor (junta subsetor), uma pessoa vai para outro setor, vínculos cortados
nos dois sentidos, "não levar atividades", e o Ponto continuando com o mesmo código da pessoa.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import estrutura as E  # noqa: E402

OK = 0


def ok(cond, txt):
    global OK
    assert cond, txt
    OK += 1
    print("  ✓", txt)


def frota():
    return {"nome": "Frota", "cor": "#2E4A7A", "responsavel": "Carlos", "subsetores": ["Motoristas", "Qualidade"], "pos_caixas": {"q1|x": {"x": 3}},
            "pessoas": [
                {"id": "c", "nome": "Carlos", "cargo": "Gerente", "nivel": "B", "subsetor": ""},
                {"id": "q1", "nome": "Rita", "cargo": "Coordenadora", "nivel": "C", "subsetor": "Qualidade", "gestor_id": "c", "ox": 10},
                {"id": "q2", "nome": "Léo", "cargo": "Inspetor", "nivel": "D", "subsetor": "Qualidade", "gestor_id": "q1", "extras": ["c"]},
                {"id": "m1", "nome": "Zé", "cargo": "Motorista", "nivel": "D", "subsetor": "Motoristas", "gestor_id": "q1"},
            ]}


INV = [{"atividade": "Inspecionar carga", "subsetor": "Qualidade"}, {"atividade": "Dirigir", "subsetor": "Motoristas"}]
PROCS = [{"id": "P1", "versao": 2, "dados": {"codigo": "PROC-001", "nome": "Inspecionar carga", "area": "Frota",
                                              "origem": {"dep": "frota", "atividade": "Inspecionar carga", "subsetor": "Qualidade"}}},
         {"id": "P2", "versao": 1, "dados": {"codigo": "PROC-002", "nome": "Dirigir", "area": "Frota"}},
         {"id": "CFG", "versao": 1, "dados": {"_tipo": "arquitetura"}}]

print("Subsetor vira setor")
r = E.planejar("frota", frota(), "setor", subsetor="Qualidade", novo={"nome": "Qualidade", "cor": "#2F7D4F"},
               inventario_origem=INV, processos=PROCS, quem="Wendley")
o, d, R = r["origem"], r["destino"], r["resumo"]
ok([p["nome"] for p in d["pessoas"]] == ["Rita", "Léo"] and d["nome"] == "Qualidade", "Rita e Léo vão para o setor novo")
ok([p["nome"] for p in o["pessoas"]] == ["Carlos", "Zé"], "Carlos e Zé ficam em Frota")
ok(d["responsavel"] == "Rita", "quem chefiava o subsetor vira responsável")
ok(all(p["subsetor"] == "" for p in d["pessoas"]), "no setor novo ninguém fica 'subsetor Qualidade'")
ok(d["pessoas"][1]["gestor_id"] == "q1" and d["pessoas"][1]["extras"] == [], "Léo continua respondendo a Rita (vínculo com quem ficou sai)")
ok(d["pessoas"][0]["gestor_id"] == "" and o["pessoas"][1]["gestor_id"] == "", "vínculos cortados nos dois sentidos (Rita↔Carlos, Zé→Rita)")
ok(len(R["vinculos_cortados"]) == 2 and any("Zé" in x for x in R["vinculos_cortados"]), "resumo avisa quem ficou sem gestor")
ok(d["pessoas"][0]["ponto_pid"] == "frota.q1", "Ponto: guarda o código antigo da pessoa")
ok("ox" not in d["pessoas"][0] and o["pos_caixas"] == {}, "posições à mão do desenho antigo saem")
ok(o["subsetores"] == ["Motoristas"], "subsetor sai da lista da Frota")
ok([i["atividade"] for i in r["inventario_origem"]] == ["Dirigir"] and r["inventario_destino"][0]["atividade"] == "Inspecionar carga"
   and r["inventario_destino"][0]["subsetor"] == "", "atividade do subsetor vai para o Diagnóstico do setor novo")
ok([p["id"] for p in r["processos"]] == ["P1"] and r["processos"][0]["dados"]["area"] == "Qualidade"
   and r["processos"][0]["dados"]["origem"]["dep"] == "__novo__", "processo ligado vai junto (o id do setor novo entra na gravação)")
ok(o["historico_estrutura"][0]["texto"].startswith("O subsetor Qualidade virou") and d["historico_estrutura"][0]["por"] == "Wendley", "histórico nos dois setores")

print("Subsetor vai para outro setor")
cx = {"nome": "CX", "subsetores": ["SAC"], "pessoas": [{"id": "s1", "nome": "Ana", "subsetor": "SAC"}]}
r = E.planejar("frota", frota(), "mover", subsetor="Qualidade", destino_id="cx", destino=cx, inventario_origem=INV, inventario_destino=[], processos=PROCS)
ok([p["nome"] for p in r["destino"]["pessoas"]] == ["Ana", "Rita", "Léo"] and r["destino"]["subsetores"] == ["SAC", "Qualidade"], "entra como subsetor Qualidade em CX")
ok(all(p["subsetor"] == "Qualidade" for p in r["destino"]["pessoas"][1:]), "mantém o subsetor")
ok(r["processos"][0]["dados"]["origem"]["dep"] == "cx" and r["processos"][0]["dados"]["area"] == "CX", "processo passa para CX")
r = E.planejar("frota", frota(), "mover", subsetor="Qualidade", destino_id="cx", destino=cx, inventario_origem=INV, processos=PROCS, levar_atividades=False)
ok(not r["processos"] and len(r["inventario_origem"]) == 2, "sem 'levar junto': atividades e processos ficam")

print("Uma pessoa vai para outro setor")
r = E.planejar("frota", frota(), "pessoa", pessoa_id="m1", destino_id="cx", destino=cx, subsetor_destino="SAC")
ok([p["nome"] for p in r["destino"]["pessoas"]] == ["Ana", "Zé"] and r["destino"]["pessoas"][1]["subsetor"] == "SAC", "Zé vai para CX · SAC")
ok(r["destino"]["pessoas"][1]["ponto_pid"] == "frota.m1", "Ponto continua com o código dele")
ok(len(r["origem"]["pessoas"]) == 3 and r["origem"]["subsetores"] == ["Motoristas", "Qualidade"], "Frota fica com os outros")
f = frota(); f["pessoas"][3]["ponto_pid"] = "antigo.m1"
r = E.planejar("frota", f, "pessoa", pessoa_id="m1", destino_id="cx", destino=cx)
ok(r["destino"]["pessoas"][1]["ponto_pid"] == "antigo.m1", "quem já mudou antes mantém o primeiro código do Ponto")

print("Subsetor junta-se a outro subsetor · setor inteiro vira subsetor")
r = E.planejar("frota", frota(), "mover", subsetor="Qualidade", destino_id="cx", destino=cx, subsetor_destino="SAC", inventario_origem=INV, processos=PROCS)
ok(all(p["subsetor"] == "SAC" for p in r["destino"]["pessoas"]) and r["destino"]["subsetores"] == ["SAC"], "Qualidade junta-se ao SAC em CX (não cria subsetor novo)")
ok(r["inventario_destino"][0]["subsetor"] == "SAC" and r["processos"][0]["dados"]["origem"]["subsetor"] == "SAC", "atividade e processo (com a IT) vão para o SAC")
r = E.planejar("frota", frota(), "setor_vira_sub", destino_id="cx", destino=cx, inventario_origem=INV, inventario_destino=[], processos=PROCS)
R = r["resumo"]
ok(len(r["destino"]["pessoas"]) == 5 and all(p["subsetor"] == "Frota" for p in r["destino"]["pessoas"][1:]), "Frota inteira entra em CX como subsetor Frota")
ok(r["destino"]["pessoas"][2].get("subsetor_anterior") == "Qualidade", "o subsetor antigo fica anotado na pessoa")
ok(r["destino"]["pessoas"][3]["gestor_id"] == "q1" and not R["vinculos_cortados"], "chefias internas continuam (ninguém fica sem gestor)")
ok(sorted(p["id"] for p in r["processos"]) == ["P1", "P2"] and all(p["dados"]["area"] == "CX" for p in r["processos"]), "todos os processos da Frota vão para CX (inclusive o sem origem)")
ok(len(r["inventario_destino"]) == 2 and not r["inventario_origem"], "todas as atividades vão")
ok(R["origem_excluida"] and "virou o subsetor Frota de CX" in R["texto"], "resumo avisa que o card da Frota sai da lista")
r = E.planejar("frota", frota(), "setor_vira_sub", destino_id="cx", destino=cx, subsetor_destino="Logística")
ok(all(p["subsetor"] == "Logística" for p in r["destino"]["pessoas"][1:]), "dá para escolher o nome do subsetor no destino")

print("Recusas")
for args, txt in ((dict(modo="setor", subsetor="Qualidade", novo={"nome": ""}), "setor novo sem nome"),
                  (dict(modo="mover", subsetor="Nada", destino_id="cx", destino=cx), "subsetor vazio"),
                  (dict(modo="mover", subsetor="Qualidade", destino_id="frota", destino=frota()), "destino = o próprio setor"),
                  (dict(modo="pessoa", pessoa_id="xx", destino_id="cx", destino=cx), "pessoa inexistente")):
    try:
        E.planejar("frota", frota(), **args)
        ok(False, txt)
    except E.Recusado:
        ok(True, f"recusa: {txt}")

raiz = os.path.dirname(os.path.abspath(__file__))
ok('p.get("ponto_pid")' in open(os.path.join(raiz, "ponto.py"), encoding="utf-8").read(), "ponto.py usa o código guardado")
m = open(os.path.join(raiz, "main.py"), encoding="utf-8").read()
ok("/api/organograma/setores/{setor_id}/estrutura" in m and "perm.EDITAR" in m, "rota ligada no main.py (só Editar)")
print(f"\n{OK} verificações ok.")
