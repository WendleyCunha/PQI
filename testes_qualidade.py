"""
testes_qualidade.py — regras da Qualidade (v5.4) SEM Firestore.

    python testes_qualidade.py

Cobre: cada etapa da carga por uma pessoa diferente (PCL, montagem, doca, retorno;
o ADMIN faz todas), confirmar peça com código SEM LEITOR (com motivo registrado) na
montagem, na doca e no retorno, entrada avulsa no Retorno (fora do PDF) até o PCL
apontar no ERP, e os arquivos do app do PQI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import testes_motorista as T  # noqa: E402  (banco na memória, login falso e importação de teste)
from fastapi.testclient import TestClient  # noqa: E402

Q, ok = T.Q, T.ok
EAN = "7891234567895"


def como(email, abas, admin=False):
    T.USUARIO.clear()
    T.USUARIO.update({"email": email, "nome": email.split("@")[0], "abas": abas, **({"papel": "admin"} if admin else {})})


def com_sku(numero):
    """O teste não tem a Carteira: põe SKU nos itens (o 1º com EAN cadastrado, o resto sem)."""
    c = Q.carga(numero)
    for p in c["pedidos"]:
        for i, it in enumerate(p["entregar"]):
            it["sku"] = "SKU-EAN" if i == 0 else f"SKU-{p['pv']}-{i}"
    for u in c["unidades"]:
        u["sku"] = "SKU-EAN" if u["i"] == 0 else f"SKU-{u['pv']}-{u['i']}"
    Q.gravar(c)


def importar(numero, pvs):
    r = Q.importar_mapa({"data_mapa": Q.datetime.now().strftime("%d/%m/%Y"), "arquivo": "teste.pdf",
                         "cargas": [{"numero": numero, "placa": "ABC-1D23", "transportadora": "KYODAI",
                                     "pedidos": [{"seq": str(k + 1), "tipo": "VENDA", "pv": pv, "nf": f"9{k}", "cliente": f"C{k}", "nome": f"Cliente {k}",
                                                  "endereco": "Rua A", "bairro": "Centro", "cidade": "COTIA", "obs": [],
                                                  "entregar": [{"desc": "CMP COM EAN 138", "qtd": 1}, {"desc": "PROTETOR 140", "qtd": 1}]}
                                                 for k, pv in enumerate(pvs)]}]},
                        {"por": "Pcl", "email": "pcl@ks", "admin": False})
    com_sku(numero)
    return r


def teste_etapas():
    print("Cada etapa por uma pessoa (o ADMIN faz todas)")
    T.ligar_memoria()
    Q.listas()["exigir_conferencia"] = True
    Q.produtos()["SKU-EAN"] = {"desc": "CMP COM EAN 138", "eans": [EAN], "qr": False}
    importar("070001", ["739464", "739465"])
    t = TestClient(T.montar())
    U = lambda c: c["unidades"]
    c = Q.carga("070001")
    ean_un = [u for u in U(c) if u["sku"] == "SKU-EAN"]
    sem_un = [u for u in U(c) if u["sku"] != "SKU-EAN"]

    como("pcl@ks", {"pcl": 3, "montagem": 3, "conferencia": 3, "retorno": 3})
    r = t.post("/api/qualidade/cargas/070001/montagem/leitura", json={"unidade": sem_un[0]["id"]})
    ok(r.status_code == 403 and "PCL" in r.json()["detail"], "quem importou (PCL) não monta o palete")
    ok(t.get("/api/qualidade/cargas/070001").json()["bloqueios"]["montagem"] != "", "a tela já avisa o bloqueio")

    como("mont@ks", {"montagem": 3})
    for u in sem_un:
        ok(t.post("/api/qualidade/cargas/070001/montagem/leitura", json={"unidade": u["id"]}).json()["resultado"] == "ok", f"montador confirma {u['desc']} tocando")
    r = t.post("/api/qualidade/cargas/070001/montagem/leitura", json={"unidade": ean_un[0]["id"]}).json()
    ok(r["resultado"] == "exige_codigo", "peça com EAN: tocar pede a leitura")
    ok(t.post("/api/qualidade/cargas/070001/montagem/leitura", json={"unidade": ean_un[0]["id"], "sem_leitor": True}).status_code == 400, "sem leitor exige o motivo")
    r = t.post("/api/qualidade/cargas/070001/montagem/leitura", json={"unidade": ean_un[0]["id"], "sem_leitor": True, "motivo": "leitor quebrado"}).json()
    ok(r["resultado"] == "ok" and r["modo"] == "sem_leitor", "sem leitor + motivo: a peça conta como montada")
    ok(t.post("/api/qualidade/cargas/070001/montagem/leitura", json={"codigo": EAN}).json()["resultado"] == "ok", "a outra peça com EAN entra pela leitura")
    ok(t.post("/api/qualidade/cargas/070001/montagem/finalizar", json={}).json()["resultado"] == "ok", "palete OK sem divergência")
    lid = Q.carga("070001")["montagem"]["leituras"][ean_un[0]["id"]]
    ok(lid["motivo"] == "leitor quebrado" and lid["por"] == "mont", "motivo e pessoa ficam registrados")

    como("mont@ks", {"montagem": 3, "conferencia": 3})
    ok(t.post("/api/qualidade/cargas/070001/doca/leitura", json={"unidade": sem_un[0]["id"]}).status_code == 403, "montador não confere a doca")

    como("conf@ks", {"conferencia": 3})
    for u in U(Q.carga("070001")):
        corpo = {"unidade": u["id"]} if u["sku"] != "SKU-EAN" else {"unidade": u["id"], "sem_leitor": True, "motivo": "sem câmera"}
        ok(t.post("/api/qualidade/cargas/070001/doca/leitura", json=corpo).json()["resultado"] == "ok", "conferente confere (com e sem leitor)")
    ok(t.post("/api/qualidade/cargas/070001/doca/finalizar", json={}).json()["status"] == "validada", "doca OK → validada")

    como("conf@ks", {"conferencia": 3, "retorno": 3})
    ok(t.post("/api/qualidade/cargas/070001/retorno/lote", json={"pv": "739464", "linhas": [{"i": 1, "qtd": 1}], "motivo": "Cliente ausente",
                                                                   "estado": "ok", "destino": "CD — volta ao estoque"}).status_code == 403, "conferente não faz o retorno")

    como("ret@ks", {"retorno": 3, "pcl": 3})
    corpo = {"pv": "739464", "linhas": [{"i": 0, "qtd": 1}, {"i": 1, "qtd": 1}], "motivo": "Cliente ausente", "estado": "ok", "destino": "CD — volta ao estoque"}
    ok(t.post("/api/qualidade/cargas/070001/retorno/lote", json=corpo).status_code == 409, "retorno em lote com EAN pede leitura ou motivo")
    r = t.post("/api/qualidade/cargas/070001/retorno/lote", json={**corpo, "sem_leitor": True, "motivo_sem_leitor": "leitor sem bateria"})
    ok(r.status_code == 200 and {i["modo"] for i in r.json()["itens"]} == {"sem_leitor", "lote"}, "com motivo: registra e marca a peça com EAN como sem leitor")
    r = t.post("/api/qualidade/cargas/070001/retorno", json={"item": "739465|0", "origem": "nao_entregue"}).json()
    ok(r["resultado"] == "exige_codigo", "retorno peça a peça com EAN pede a leitura")
    r = t.post("/api/qualidade/cargas/070001/retorno", json={"item": "739465|0", "origem": "nao_entregue", "sem_codigo_ok": True, "motivo_sem_leitor": "sem leitor",
                                                            "motivo": "Cliente recusou", "estado": "ok", "destino": "CD — volta ao estoque"}).json()
    ok(r["resultado"] == "ok" and r["item"]["modo"] == "sem_leitor", "retorno peça a peça sem leitor, com motivo")
    ok(t.post("/api/qualidade/cargas/070001/retorno/concluir", json={}).json()["resultado"] == "ok", "retorno concluído")
    ok(t.post("/api/qualidade/cargas/070001/apontar", json={"texto": ""}).status_code == 403, "quem fez o retorno não aponta no ERP (PCL)")

    como("pcl@ks", {"pcl": 3})
    ok(t.post("/api/qualidade/cargas/070001/apontar", json={"texto": "ok"}).json()["status"] == "baixada", "o PCL (outra pessoa) aponta e baixa")
    ep = Q.carga("070001")["etapas_pessoas"]
    ok(ep["pcl"] == ["pcl@ks"] and ep["montagem"] == ["mont@ks"] and ep["doca"] == ["conf@ks"] and ep["retorno"] == ["ret@ks"], "quem fez cada etapa fica na carga")

    print("ADMIN faz todas as etapas (testes)")
    importar("070002", ["741000"])
    como("adm@ks", {"pcl": 4, "montagem": 4, "conferencia": 4, "retorno": 4}, admin=True)
    for et in ("montagem", "doca"):
        for u in U(Q.carga("070002")):
            corpo = {"unidade": u["id"]} if u["sku"] != "SKU-EAN" else {"unidade": u["id"], "sem_leitor": True, "motivo": "teste"}
            t.post(f"/api/qualidade/cargas/070002/{et}/leitura", json=corpo)
        ok(t.post(f"/api/qualidade/cargas/070002/{et}/finalizar", json={}).json()["resultado"] == "ok", f"admin fecha a {et}")
    ok(t.post("/api/qualidade/cargas/070002/retorno/concluir", json={}).json()["resultado"] == "ok", "admin conclui o retorno")
    ok(t.post("/api/qualidade/cargas/070002/apontar", json={"texto": ""}).json()["status"] == "baixada", "admin aponta no ERP")


def teste_avulso():
    print("Entrada avulsa no Retorno (fora do PDF)")
    T.ligar_memoria()
    Q.produtos()["SKU-EAN"] = {"desc": "CMP COM EAN 138", "eans": [EAN], "qr": False}
    t = TestClient(T.montar())
    como("ret@ks", {"retorno": 2})
    base = {"origem": "Loja", "referencia": "Loja Barueri", "pv": "", "nf": "540999", "motivo": "Retorno de loja", "estado": "avariada",
            "avaria": "Rasgado", "destino": "AS — assistência (recuperar)", "obs": "chegou sem aviso"}
    ok(t.post("/api/qualidade/retorno/avulso", json={**base, "itens": []}).status_code == 400, "sem item não registra")
    como("ret@ks", {"montagem": 3})
    ok(t.post("/api/qualidade/retorno/avulso", json={**base, "itens": [{"desc": "X", "qtd": 1}]}).status_code == 403, "precisa da aba Retorno")
    como("ret@ks", {"retorno": 2})
    r = t.post("/api/qualidade/retorno/avulso", json={**base, "itens": [{"sku": "SKU-EAN", "desc": "", "qtd": 2}, {"desc": "Travesseiro", "qtd": 1}]})
    ok(r.status_code == 200 and r.json()["numero"].startswith("AV") and r.json()["itens"] == 3, "cria a carga AV… com 3 peças registradas")
    num = r.json()["numero"]
    c = Q.carga(num)
    ok(c["avulsa"] and c["status"] == "em_retorno" and c["pedidos"][0]["nome"] == "Loja: Loja Barueri", "fica Em retorno, com a origem")
    ok(all(i["estado"] == "avariada" and i["avaria"] == "Rasgado" for i in c["retorno"]["itens"]), "estado e avaria em todas as peças")
    ok(c["retorno"]["itens"][0]["desc"] == "CMP COM EAN 138", "SKU do cadastro traz a descrição")
    r2 = t.post("/api/qualidade/retorno/avulso", json={**base, "itens": [{"desc": "Outro", "qtd": 1}]}).json()
    ok(r2["numero"] != num, "a segunda entrada do dia ganha outro número")
    como("ret@ks", {"retorno": 2, "aovivo": 1})
    ok(all(g["numero"] != num for g in t.get("/api/routerking/ao-vivo").json()["cargas"]), "avulsa não aparece no Ao vivo")
    ok(any(x["numero"] == num and x["avulsa"] for x in t.get("/api/qualidade/cargas").json()), "aparece na lista de cargas marcada como avulsa")
    ok(t.post(f"/api/qualidade/cargas/{num}/retorno/concluir", json={}).json()["resultado"] == "ok", "conclui o retorno avulso")
    como("pcl@ks", {"pcl": 3, "gap": 3})
    ok(any(x["carga"] == num for x in t.get("/api/qualidade/gap").json()["retornos"]), "as peças entram no GAP para tabular")
    ok(t.post(f"/api/qualidade/cargas/{num}/apontar", json={"texto": ""}).json()["status"] == "baixada", "PCL aponta no ERP e baixa")


def teste_ajuste_carga():
    print("Ajuste na carga pelo PCL (retira / agrupamento) com trava")
    T.ligar_memoria()
    Q.listas()["exigir_conferencia"] = True
    importar("070101", ["800001", "800002"])
    importar("070102", ["800009"])
    t = TestClient(T.montar())
    como("mont@ks", {"montagem": 3})
    u1 = [u for u in Q.carga("070101")["unidades"] if u["pv"] == "800001" and u["sku"] != "SKU-EAN"][0]
    ok(t.post("/api/qualidade/cargas/070101/montagem/leitura", json={"unidade": u1["id"]}).json()["resultado"] == "ok", "montagem começou")
    como("pcl2@ks", {"pcl": 3})
    ok(t.post("/api/qualidade/cargas/070101/ajuste-carga", json={"tipo": "retira", "pv": "800001", "motivo": ""}).status_code == 400, "ajuste pede motivo")
    r = t.post("/api/qualidade/cargas/070101/ajuste-carga", json={"tipo": "retira", "pv": "800001", "motivo": "cliente adiou"}).json()
    ok(r["ajuste"]["tipo"] == "retira" and r["ajuste"]["ja_no_palete"] == 1 and r["ajuste"]["etapa"] == "montagem", "PCL retira o PV 800001 (1 unidade já estava no palete)")
    c = Q.carga("070101")
    ok(all(p["pv"] != "800001" for p in c["pedidos"]) and all(u["pv"] != "800001" for u in c["unidades"]), "o pedido sai da carga e das unidades esperadas")
    ok(c["resumo"]["ajustes_pendentes"] == 1, "carga sinalizada com ajuste pendente")
    como("mont@ks", {"montagem": 3})
    u2 = [u for u in c["unidades"] if u["sku"] != "SKU-EAN"][0]
    r = t.post("/api/qualidade/cargas/070101/montagem/leitura", json={"unidade": u2["id"]})
    ok(r.status_code == 423 and "RETIRA" in r.json()["detail"], "montagem TRAVADA: não lê nem fecha até validar")
    ok(t.post("/api/qualidade/cargas/070101/montagem/finalizar", json={}).status_code == 423, "não fecha o palete com o ajuste pendente")
    como("pcl2@ks", {"pcl": 3, "montagem": 3})
    aid = c["ajustes_carga"][0]["id"]
    ok(t.post(f"/api/qualidade/cargas/070101/ajuste-carga/{aid}/validar").status_code == 403, "o PCL não valida o próprio ajuste (é a etapa da montagem)")
    como("mont@ks", {"montagem": 3})
    r = t.post(f"/api/qualidade/cargas/070101/ajuste-carga/{aid}/validar").json()
    ok(r["ajuste"]["status"] == "validado" and r["pendentes"] == 0, "montador valida que tirou do palete")
    ok(t.post("/api/qualidade/cargas/070101/montagem/leitura", json={"unidade": u2["id"]}).json()["resultado"] == "ok", "montagem destravada")

    print("Agrupamento: o PV muda de carga sozinho")
    como("pcl2@ks", {"pcl": 3})
    r = t.post("/api/qualidade/cargas/070101/ajuste-carga", json={"tipo": "agrupa", "pv": "800009", "motivo": "mesmo bairro"}).json()
    ok(r["ajuste"]["ref"] == "070102", "PV 800009 estava na carga 070102: muda para a 070101")
    c1, c2 = Q.carga("070101"), Q.carga("070102")
    ok(any(p["pv"] == "800009" for p in c1["pedidos"]) and not any(p["pv"] == "800009" for p in c2["pedidos"]), "sai de uma carga e entra na outra")
    ok(sum(1 for u in c1["unidades"] if u["pv"] == "800009") == 2 and len({u["id"] for u in c1["unidades"]}) == len(c1["unidades"]), "unidades novas sem repetir id")
    ok(c1["resumo"]["ajustes_pendentes"] == 1 and c2["resumo"]["ajustes_pendentes"] == 1, "as DUAS cargas ficam travadas até validar")
    ok(t.post("/api/qualidade/cargas/070101/ajuste-carga", json={"tipo": "agrupa", "pv": "999999", "motivo": "x"}).status_code == 400,
       "PV fora das cargas pede cliente e itens")
    r = t.post("/api/qualidade/cargas/070101/ajuste-carga", json={"tipo": "agrupa", "pv": "999999", "motivo": "pedido urgente", "nome": "Cliente Novo",
                                                                  "itens": [{"qtd": 1, "desc": "TRAVESSEIRO"}]}).json()
    ok(r["ajuste"]["tipo"] == "agrupa" and any(p["pv"] == "999999" for p in Q.carga("070101")["pedidos"]), "agrupa PV novo digitado pelo PCL")
    como("mont@ks", {"montagem": 3})
    for a in [x for x in Q.carga("070101")["ajustes_carga"] if x["status"] == "pendente"]:
        t.post(f"/api/qualidade/cargas/070101/ajuste-carga/{a['id']}/validar")
    for u in Q.carga("070101")["unidades"]:
        corpo = {"unidade": u["id"]} if u.get("sku") != "SKU-EAN" else {"unidade": u["id"], "sem_leitor": True, "motivo": "teste"}
        t.post("/api/qualidade/cargas/070101/montagem/leitura", json=corpo)
    ok(t.post("/api/qualidade/cargas/070101/montagem/finalizar", json={}).json()["resultado"] == "ok", "validados os ajustes, o palete fecha com os pedidos novos")

    print("Agrupamento depois da montagem: a doca confere direto")
    como("pcl2@ks", {"pcl": 3})
    r = t.post("/api/qualidade/cargas/070101/ajuste-carga", json={"tipo": "agrupa", "pv": "888888", "motivo": "encaixe", "nome": "Cliente Doca",
                                                                  "itens": [{"qtd": 1, "desc": "PROTETOR 090"}]}).json()
    ok(r["ajuste"]["etapa"] == "doca", "ajuste depois da montagem trava a conferência na doca")
    como("conf@ks", {"conferencia": 3})
    a = [x for x in Q.carga("070101")["ajustes_carga"] if x["status"] == "pendente"][0]
    ok(t.post(f"/api/qualidade/cargas/070101/ajuste-carga/{a['id']}/validar").json()["pendentes"] == 0, "conferente valida")
    for u in Q.carga("070101")["unidades"]:
        corpo = {"unidade": u["id"]} if u.get("sku") != "SKU-EAN" else {"unidade": u["id"], "sem_leitor": True, "motivo": "teste"}
        t.post("/api/qualidade/cargas/070101/doca/leitura", json=corpo)
    ok(t.post("/api/qualidade/cargas/070101/doca/finalizar", json={}).json()["status"] == "validada", "doca confere o pedido agrupado e valida a carga")
    como("pcl2@ks", {"pcl": 3})
    ok(t.post("/api/qualidade/cargas/070101/ajuste-carga", json={"tipo": "retira", "pv": "888888", "motivo": "x"}).status_code == 409,
       "depois de validada (com o motorista), não ajusta mais")


def teste_retorno_do_app():
    print("Retorno alimentado pelo app")
    T.ligar_memoria()
    Q.placas()["ABC1D23"] = {"motorista": "João Ferreira"}
    T.importar("070201", "ABC1D23", [("810001", "Ana", 2), ("810002", "Beto", 1)])
    app = T.montar()
    mot, t = TestClient(app), TestClient(app)
    T.entrar(mot, "ABC1D23", "João Ferreira")
    base = "/api/motorista/cargas/070201/pedido/810001"
    mot.post(base + "/chat", json={"motivo": "Ausente"})
    car = mot.get("/api/motorista/cargas").json()["cargas"][0]
    ok(car["motivos"] == ["Ausente", "Avaria", "Escadas"] and car["pedidos"][0]["motivo_chat"] == "Ausente", "retorno usa os motivos do chat e já traz o do chat")
    r = mot.post(base, json={"desfecho": "retorno", "foto": T.FOTO, "cid": "r1"}).json()
    ok(r["resultado"] == "ok" and r["carga"]["pedidos"][0]["motivo"] == "Ausente", "retorno sem escolher motivo: vale o motivo do chat")
    c = Q.carga("070201")
    its = [x for x in c["retorno"]["itens"] if x["pv"] == "810001"]
    ok(len(its) == 2 and all(x["fonte"] == "app" and not x["recebido"] for x in its) and c["status"] == "em_retorno", "as 2 peças já aparecem no Retorno, aguardando chegar")
    como("cd@ks", {"retorno": 2})
    ok(t.post("/api/qualidade/cargas/070201/retorno/concluir", json={}).status_code == 409, "não conclui com peça do app ainda não recebida")
    r = t.post("/api/qualidade/cargas/070201/retorno", json={"item": "810001|0", "origem": "nao_entregue"}).json()
    ok(r["resultado"] == "precisa_dados" and r["receber"] and "motivo" not in r["falta"], "tocar na peça confirma a do app (pede só estado e destino)")
    r = t.post("/api/qualidade/cargas/070201/retorno", json={"receber": r["receber"], "estado": "ok", "destino": "CD — volta ao estoque"}).json()
    ok(r["resultado"] == "ok" and r["item"]["recebido"] and r["item"]["motivo"] == "Ausente", "peça recebida no CD, com o motivo do motorista")
    outra = [x for x in Q.carga("070201")["retorno"]["itens"] if not x.get("recebido")][0]
    ok(t.post("/api/qualidade/cargas/070201/retorno", json={"receber": outra["id"], "estado": "ok", "destino": "CD — volta ao estoque"}).json()["resultado"] == "ok", "segunda peça recebida")
    ok(len([x for x in Q.carga("070201")["retorno"]["itens"] if x["pv"] == "810001"]) == 2, "não duplica: as peças do app viram as peças do Retorno")
    como("pcl@ks", {"aovivo": 2, "pcl": 2})
    r = t.put("/api/routerking/cargas/070201/liberacao/810002", json={"acao": "liberar"})
    ok(r.status_code == 200 and r.json()["liberacao"]["status"] == "liberada", "PCL libera sem palavra ANTES do motorista pedir")

    print("Retorno: ENTREGUE no app mas voltou → pendência no PCL")
    ok(mot.post("/api/motorista/cargas/070201/pedido/810002", json={"desfecho": "entregue", "cid": "e1"}).json()["resultado"] == "ok", "motorista deu ENTREGUE")
    ok(mot.post("/api/motorista/cargas/070201/chegada", json={}).status_code == 200, "motorista avisou que chegou no CD")
    como("cd@ks", {"retorno": 2})
    lst = t.get("/api/qualidade/cargas").json()
    x = next(c for c in lst if c["numero"] == "070201")
    ok(x["rua"]["chegada"] and x["rua"]["app_pendentes"] == 0, "lista do Retorno sabe que a carga chegou no CD (destaque)")
    r = t.post("/api/qualidade/cargas/070201/retorno/lote", json={"pv": "810002", "linhas": [{"i": 0, "qtd": 1}], "motivo": "Avaria", "estado": "avariada",
                                                                  "avaria": "Rasgado", "destino": "AS — assistência (recuperar)"})
    ok(r.status_code == 200, "CD edita o pedido entregue e registra que voltou")
    pend = [a for a in Q.carga("070201")["pendencias"] if a["origem"] == "retorno"]
    ok(len(pend) == 1 and pend[0]["tipo"] == "entregue_voltou" and pend[0]["status"] == "aberta", "vira pendência no PCL (corrigir a baixa no ERP)")
    ok(t.post("/api/qualidade/cargas/070201/retorno/concluir", json={}).json()["resultado"] == "ok", "retorno concluído e enviado ao PCL")
    como("pcl@ks", {"pcl": 3})
    ok(any(a["origem"] == "retorno" for a in t.get("/api/qualidade/pendencias").json()), "PCL vê a pendência nos Ajustes")
    r = t.put(f"/api/qualidade/cargas/070201/pendencias/{pend[0]['id']}", json={"acao": "resolver", "nota": "baixa estornada no ERP"})
    ok(r.status_code == 200 and r.json()["pendencia"]["status"] == "resolvido", "PCL resolve depois de corrigir o ERP")


def teste_routerking():
    print("RouterKing: módulo próprio, histórico de entregas e acesso herdado")
    import modulos as MD
    u, per = {"email": "x"}, {"niveis": {"qualidade": 3}}
    n = MD.niveis_do_usuario(u, per)
    ok(n["routerking"] == 3 and MD.abas_do_usuario(u, per, n)["routerking"]["aovivo"] == 4, "quem tinha a Qualidade inteira continua com o RouterKing (herdado)")
    per2 = {"niveis": {"qualidade": 3}, "abas": {"qualidade": {"aovivo": 0, "motoristas": 0}}}
    n = MD.niveis_do_usuario(u, per2)
    ok(n["routerking"] == 0, "quem NÃO tinha Ao vivo nem Motoristas não ganha o RouterKing")
    u3 = {"email": "y", "abas": {"qualidade": {"aovivo": 2}}}
    n = MD.niveis_do_usuario(u3, {"niveis": {"qualidade": 1}})
    ok(n["routerking"] == 2 and MD.abas_do_usuario(u3, {"niveis": {"qualidade": 1}}, n)["routerking"] == {"aovivo": 2, "historico": 2, "motoristas": 0},
       "aba Ao vivo da pessoa vira Ao vivo + Histórico no RouterKing")
    per4 = {"niveis": {"qualidade": 3, "routerking": 1}}
    n = MD.niveis_do_usuario(u, per4)
    ok(n["routerking"] == 1, "depois que o admin configura o RouterKing, vale o configurado")
    ok(MD.modulo_da_rota("/api/routerking/ao-vivo")["id"] == "routerking" and MD.modulo_da_rota("/routerking")["id"] == "routerking", "rotas do RouterKing protegidas pelo módulo")
    ok(not any(a["id"] in ("aovivo", "motoristas") for a in MD.abas_de("qualidade")), "Qualidade fica só com Operação e Administrativo")

    T.ligar_memoria()
    Q.placas()["ABC1D23"] = {"motorista": "João Ferreira"}
    T.importar("070301", "ABC1D23", [("820001", "Ana Lima", 1), ("820002", "Bruno Reis", 1)])
    app = T.montar()
    mot, t = TestClient(app), TestClient(app)
    T.entrar(mot, "ABC1D23", "João Ferreira")
    mot.post("/api/motorista/cargas/070301/pedido/820001/chat", json={"motivo": "Escadas"})
    mot.post("/api/motorista/cargas/070301/pedido/820001", json={"desfecho": "retorno", "foto": T.FOTO, "cid": "h1"})
    como("frota@ks", {"historico": 1})
    h = t.get("/api/routerking/historico").json()
    ok(h["total"] == 2, "histórico lista todos os clientes do período")
    x = next(i for i in h["itens"] if i["pv"] == "820001")
    ok(x["desfecho"] == "retorno" and x["chats"] == 1 and x["motivos_chat"] == ["Escadas"] and x["foto"], "com desfecho, foto e a conversa")
    ok(t.get("/api/routerking/historico?busca=bruno").json()["total"] == 1, "busca por nome do cliente")
    como("frota@ks", {"aovivo": 1})
    d = t.get("/api/routerking/cargas/070301/pedido/820001").json()
    ok(d["historico"] and d["chat"]["atendimentos"] and d["retorno_itens"] and d["evento"]["foto"], "dados do PDF do caso: linha do tempo, conversa, retorno e foto")
    fid = d["evento"]["foto"]
    ok(t.get(f"/api/routerking/foto/{fid}").status_code == 200, "foto abre no RouterKing")
    como("cd@ks", {"retorno": 2})
    ok(t.get(f"/api/qualidade/foto/{fid}").status_code == 200, "a mesma foto abre na Qualidade (Retorno)")
    como("cd@ks", {"retorno": 2})
    ok(t.get("/api/routerking/historico").status_code == 403, "quem só tem o Retorno não vê o histórico do RouterKing")
    r = t.get("/routerking")
    ok(r.status_code == 200 and 'data-modulo="routerking"' in r.text and "RouterKing" in r.text, "página /routerking com a permissão do RouterKing")


def teste_app_pqi():
    print("App do PQI (arquivos)")
    import json
    raiz = os.path.dirname(os.path.abspath(__file__))
    mf = json.load(open(os.path.join(raiz, "pqi.webmanifest"), encoding="utf-8"))
    ok(mf["scope"] == "/" and mf["display"] == "standalone" and len(mf["icons"]) == 2, "manifesto do PQI")
    ok(all(os.path.exists(os.path.join(raiz, f)) for f in ("pqi-sw.js", "pqi-192.png", "pqi-512.png")), "service worker e ícones existem")
    main = open(os.path.join(raiz, "main.py"), encoding="utf-8").read()
    ok(all(r in main for r in ('"/pqi-sw.js"', '"/pqi-app/manifest.webmanifest"', '"/pqi-app/icone-192.png"')), "rotas públicas do app no main.py")


if __name__ == "__main__":
    teste_etapas()
    teste_avulso()
    teste_ajuste_carga()
    teste_retorno_do_app()
    teste_routerking()
    teste_app_pqi()
    print(f"\n{T.OK} verificações ok.")
