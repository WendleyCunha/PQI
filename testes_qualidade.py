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
    ok(all(g["numero"] != num for g in t.get("/api/qualidade/rua/ao-vivo").json()["cargas"]), "avulsa não aparece no Ao vivo")
    ok(any(x["numero"] == num and x["avulsa"] for x in t.get("/api/qualidade/cargas").json()), "aparece na lista de cargas marcada como avulsa")
    ok(t.post(f"/api/qualidade/cargas/{num}/retorno/concluir", json={}).json()["resultado"] == "ok", "conclui o retorno avulso")
    como("pcl@ks", {"pcl": 3, "gap": 3})
    ok(any(x["carga"] == num for x in t.get("/api/qualidade/gap").json()["retornos"]), "as peças entram no GAP para tabular")
    ok(t.post(f"/api/qualidade/cargas/{num}/apontar", json={"texto": ""}).json()["status"] == "baixada", "PCL aponta no ERP e baixa")


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
    teste_app_pqi()
    print(f"\n{T.OK} verificações ok.")
