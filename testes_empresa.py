"""
testes_empresa.py — Empresa · Fase 0 (v7.0) SEM Firestore e SEM IA externa.

    python testes_empresa.py

Cobre: o setor criado no Organograma aparece mesmo sem atividade; atividades do
Diagnóstico aparecem para confirmar; criar atividade (vira processo do Mapa, com
origem no setor); pessoas da atividade; IT escrita/ajustada (com histórico e ids
mantidos); IT pela IA (simulada) sem gravar; "lista completa"; % da Fase 0;
processos fora do Organograma não somem; e o acesso de Empresa (herda o maior dos
três sistemas até ser configurado; depois libera os três editores).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import empresa as E  # noqa: E402
import modulos as mods  # noqa: E402

SETORES: list = []
PROCESSOS: list = []
INVENTARIO: dict = {}
USUARIO: dict = {}
NIVEL = {"empresa": 3}
IA = {"texto": None, "ligada": True}
OK = 0


def ok(cond, txt):
    global OK
    assert cond, txt
    OK += 1
    print("  ✓", txt)


def criar(dados, usuario, email=None):
    reg = {"id": f"P{len(PROCESSOS) + 1}", "versao": 1, "dados": dados, "criado_por": usuario}
    PROCESSOS.append(reg)
    return reg


def salvar(pid, dados, versao, usuario):
    for i, r in enumerate(PROCESSOS):
        if r["id"] == pid:
            if versao is not None and versao != r["versao"]:
                return "conflito", r
            PROCESSOS[i] = {**r, "dados": dados, "versao": r["versao"] + 1, "atualizado_por": usuario}
            return "ok", PROCESSOS[i]
    return "nao_existe", None


def montar():
    app = FastAPI()
    E.H.clear()
    E.ligar(app, usuario_atual=lambda r: USUARIO, niveis=lambda u: NIVEL,
            setores_do=lambda u: [s for s in SETORES if not u.get("deps") or s["id"] in u["deps"]],
            processos=lambda: [dict(p) for p in PROCESSOS], criar_processo=criar, salvar_processo=salvar,
            inventario=lambda dep: INVENTARIO.get(dep, []), nome=lambda r: USUARIO.get("nome", ""),
            sincronizar=lambda reg: None, ia=lambda s, u: IA["texto"], ia_ligada=lambda: IA["ligada"],
            ve_tudo=lambda u: not u.get("deps"))
    return app


def dados_exemplo():
    SETORES[:] = [
        {"id": "cx", "dados": {"nome": "CX", "cor": "#3b82f6", "subsetores": ["SAC", "Pós-venda"], "pessoas": [
            {"id": "1", "nome": "Maria Paula", "cargo": "Supervisora", "subsetor": "SAC"},
            {"id": "2", "nome": "João", "cargo": "Analista", "subsetor": "SAC"},
            {"id": "3", "nome": "", "cargo": "Analista", "vaga": True}]}},
        {"id": "qual", "dados": {"nome": "Qualidade", "pessoas": []}},   # setor novo, sem nada
    ]
    PROCESSOS[:] = [
        {"id": "P0", "versao": 1, "dados": {"codigo": "PROC-007", "nome": "Abrir chamado", "area": "CX", "dono": "Maria Paula",
                                             "passos_it": [{"id": "a", "descricao": "Abrir o ERP"}, {"id": "b", "descricao": "Registrar"}],
                                             "it": {"codigo": "IT-07", "versao": "3"},
                                             "fluxo": {"asis": {"nos": [{"id": "n1"}, {"id": "n2"}]}}}},
        {"id": "PX", "versao": 1, "dados": {"codigo": "PROC-002", "nome": "Conferir doca", "area": "Expedição antiga"}},
        {"id": "CFG", "versao": 1, "dados": {"_tipo": "arquitetura", "areas": []}},
    ]
    INVENTARIO.clear()
    INVENTARIO["cx"] = [{"atividade": "Abrir chamado", "subsetor": "SAC"},
                        {"atividade": "Responder WhatsApp", "subsetor": "SAC", "executores": "João, Maria Paula"}]


def teste_visao():
    print("Visão da Fase 0")
    dados_exemplo()
    USUARIO.clear(); USUARIO.update(email="w@ks", nome="Wendley")
    t = TestClient(montar())
    v = t.get("/api/empresa/visao").json()
    nomes = [s["nome"] for s in v["setores"]]
    ok(nomes == ["CX", "Qualidade"], "o setor Qualidade (sem atividade nenhuma) aparece")
    cx = v["setores"][0]
    ok([a["nome"] for a in cx["atividades"]] == ["Abrir chamado"], "processo do Mapa é atividade do setor")
    ok([x["nome"] for x in cx["levantadas"]] == ["Responder WhatsApp"], "atividade do Diagnóstico aparece para confirmar (sem repetir a que já existe)")
    a = cx["atividades"][0]
    ok(a["it"]["tem"] and a["it"]["passos"] == 2 and a["fluxo"]["simbolos"] == 2, "IT e fluxograma reconhecidos")
    ok(len(cx["pessoas"]) == 2 and cx["vagas"] == 1, "pessoas do organograma (vaga contada à parte)")
    ok(v["fora"] and v["fora"][0]["area"] == "Expedição antiga", "processo com área fora do Organograma não some")
    ok(v["setores"][1]["pct"] == 0 and 0 < cx["pct"] < 100, "% por setor")


def teste_atividades_e_it():
    print("Atividades, pessoas e IT")
    dados_exemplo()
    USUARIO.clear(); USUARIO.update(email="w@ks", nome="Wendley")
    t = TestClient(montar())
    r = t.post("/api/empresa/atividades", json={"setor_id": "cx", "nome": "Responder WhatsApp", "pessoas": ["João"], "subsetor": "SAC"})
    ok(r.status_code == 200, "confirma a atividade levantada")
    novo = PROCESSOS[-1]["dados"]
    ok(novo["codigo"] == "PROC-008" and novo["area"] == "CX" and novo["origem"]["dep"] == "cx" and novo["dono"] == "João",
       "vira processo do Mapa (código seguinte, área e origem no setor)")
    ok(t.post("/api/empresa/atividades", json={"setor_id": "cx", "nome": "responder whatsapp"}).status_code == 409, "não duplica")
    v = t.get("/api/empresa/visao").json()["setores"][0]
    ok(not v["levantadas"] and len(v["atividades"]) == 2, "saiu de 'levantadas'")
    pid = r.json()["atividade"]["id"]
    ok(t.put(f"/api/empresa/atividades/{pid}/pessoas", json={"pessoas": ["João", "Maria Paula"]}).json()["atividade"]["pessoas"] == ["João", "Maria Paula"],
       "pessoas da atividade")
    r = t.put(f"/api/empresa/atividades/{pid}/it", json={"passos": [{"descricao": "Ler a mensagem"}, {"descricao": "Responder", "quem": "João"}]})
    it = r.json()["atividade"]["it"]
    ok(it["tem"] and it["passos"] == 2 and it["versao"] == "1" and it["codigo"] == "IT-008", "IT nova: versão 1, código IT-008")
    # ajustar a IT existente (P0): ids na mesma posição continuam, versão sobe, histórico guarda a anterior
    r = t.put("/api/empresa/atividades/P0/it", json={"passos": [{"descricao": "Abrir o ERP (tela X)"}, {"descricao": "Registrar"}, {"descricao": "Avisar o cliente"}],
                                                        "origem": "ia"})
    d = next(p for p in PROCESSOS if p["id"] == "P0")["dados"]
    ok([x["id"] for x in d["passos_it"]][:2] == ["a", "b"] and len(d["passos_it"]) == 3, "ajuste mantém os ids (o 'como é feito hoje' continua ligado)")
    ok(d["it"]["versao"] == "4" and d["it_historico"][0]["passos"][0]["descricao"] == "Abrir o ERP", "versão sobe e a anterior fica no histórico")
    ok(d["it"].get("gerada_com_ia"), "marca que veio da IA")
    ok(t.put("/api/empresa/atividades/P0/it", json={"passos": []}).status_code == 400, "IT vazia recusada")


def teste_ia():
    print("IT pela IA (simulada)")
    dados_exemplo()
    USUARIO.clear(); USUARIO.update(email="w@ks", nome="Wendley")
    t = TestClient(montar())
    IA["texto"] = 'Claro! {"titulo": "Abrir chamado", "passos": [{"descricao": "Abrir o ERP", "quem": "Analista"}, {"descricao": "Registrar o chamado"}]} fim'
    r = t.post("/api/empresa/it-ia", json={"processo_id": "P0", "pedido": "incluir o aviso ao cliente"})
    ok(r.status_code == 200 and len(r.json()["proposta"]["passos"]) == 2 and r.json()["ajuste"], "proposta lida mesmo com texto em volta")
    ok(next(p for p in PROCESSOS if p["id"] == "P0")["versao"] == 1, "a proposta não grava nada")
    IA["texto"] = "não sei"
    ok(t.post("/api/empresa/it-ia", json={"processo_id": "P0"}).status_code == 502, "resposta inválida → erro claro")
    IA["ligada"] = False
    ok("GROQ_API_KEY" in t.post("/api/empresa/it-ia", json={"processo_id": "P0"}).json()["detail"], "IA desligada explica como ligar (Groq gratuito)")
    IA["ligada"] = True


def teste_lista_completa_e_perfis():
    print("Lista completa, perfil Adicionar e setores restritos")
    dados_exemplo()
    USUARIO.clear(); USUARIO.update(email="w@ks", nome="Wendley")
    t = TestClient(montar())
    ok(t.post("/api/empresa/setores/cx/lista-completa", json={"completa": True}).status_code == 200, "marca a lista como completa")
    cfg = next(p for p in PROCESSOS if p["id"] == "CFG")["dados"]
    ok(cfg["areas"][0]["nome"] == "CX" and cfg["areas"][0]["mapeado"], "grava no mesmo ✓ 100% mapeado de Processos")
    v = t.get("/api/empresa/visao").json()
    ok(v["setores"][0]["lista_completa"], "visão mostra a lista completa")
    NIVEL["empresa"] = 2
    ok(t.put("/api/empresa/atividades/P0/pessoas", json={"pessoas": []}).status_code == 403, "Adicionar não tira pessoa")
    ok(t.put("/api/empresa/atividades/P0/pessoas", json={"pessoas": ["Maria Paula", "João"]}).status_code == 200, "Adicionar inclui pessoa")
    ok(t.put("/api/empresa/atividades/P0/it", json={"passos": [{"descricao": "x"}]}).status_code == 403, "Adicionar não troca IT existente")
    NIVEL["empresa"] = 3
    USUARIO.update(deps=["qual"])
    v = t.get("/api/empresa/visao").json()
    ok([s["nome"] for s in v["setores"]] == ["Qualidade"] and not v["fora"], "restrito a um setor vê só ele")
    ok(t.put("/api/empresa/atividades/P0/pessoas", json={"pessoas": ["X"]}).status_code == 403, "não mexe em atividade de outro setor")


def teste_permissoes():
    print("Acesso ao card Empresa")
    perfil = {"niveis": {"organograma": 1, "mapa": 3, "diagnostico": 0}}
    n = mods.niveis_do_usuario({"email": "a@ks"}, perfil, {})
    ok(n["empresa"] == 3 and n["organograma"] == 1, "sem configurar: Empresa = maior dos três (e os três ficam como estavam)")
    perfil = {"niveis": {"organograma": 0, "mapa": 0, "diagnostico": 0, "empresa": 2}}
    n = mods.niveis_do_usuario({"email": "a@ks"}, perfil, {})
    ok(n["organograma"] == n["mapa"] == n["diagnostico"] == 2, "configurada: libera os editores de dentro")
    cards = [c["id"] for c in mods.cards_do_usuario({"email": "a@ks"}, n)]
    ok("empresa" in cards and not {"organograma", "mapa", "diagnostico"} & set(cards), "Painel mostra só o card Empresa")
    ok(any(m["id"] == "mapa" and m.get("oculto") for m in mods.catalogo_publico()), "Segurança ainda lista os três (marcados 'dentro de Empresa')")
    ok(mods.modulo_da_rota("/api/empresa/visao")["id"] == "empresa", "rotas /api/empresa protegidas pelo módulo")
    raiz = os.path.dirname(os.path.abspath(__file__))
    main = open(os.path.join(raiz, "main.py"), encoding="utf-8").read()
    ok('"/empresa": "empresa.html"' in main and "emp.ligar(" in main, "página e rotas ligadas no main.py")
    ok(os.path.exists(os.path.join(raiz, "empresa.html")), "empresa.html existe")


if __name__ == "__main__":
    teste_visao()
    teste_atividades_e_it()
    teste_ia()
    teste_lista_completa_e_perfis()
    teste_permissoes()
    print(f"\n{OK} verificações ok.")
