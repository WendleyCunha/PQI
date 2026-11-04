"""
testes_motorista.py — testes do app do motorista e do painel ao vivo SEM Firestore.

    python testes_motorista.py

Cobre: login pela placa com a senha inicial e troca obrigatória, escolha do motorista,
só carga VALIDADA aparece, a palavra nunca sai no app, palavra ligada/desligada por
motorista, bloqueio depois de N erros e liberação pelo PCL, retorno com motivo e foto
obrigatória, parcial, reenvio offline sem duplicar, correção em 15 min, chegada no CD,
painel ao vivo (contadores, palavra, baixa no ERP), reset da senha derrubando a sessão,
GPS e permissão por aba.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import qualidade as Q  # noqa: E402
import motorista as M  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ── banco na memória ──────────────────────────────────────────────────────
BANCO: dict = {}


def _get(col, i):
    return copy.deepcopy(BANCO.get(col, {}).get(i))


def _set(col, i, d):
    BANCO.setdefault(col, {})[i] = copy.deepcopy(d)


def _periodo(col, campo, de, ate):
    return [copy.deepcopy(v) for v in BANCO.get(col, {}).values() if de <= v.get(campo, "") <= ate]


def _abertos(col):
    return [copy.deepcopy(v) for v in BANCO.get(col, {}).values() if v.get("aberta")]


def ligar_memoria():
    BANCO.clear()
    Q._fs_get, Q._fs_set, Q._fs_periodo, Q._fs_status_abertos = _get, _set, _periodo, _abertos
    M._fs_get, M._fs_set = _get, _set
    Q.buscar_na_carteira = lambda pvs: {}
    Q.CARGAS.clear()
    Q._CARREGADO.update(ok=False, desde="")
    for k in Q.CAD:
        Q.CAD[k] = None
    M.APP["dados"] = None
    M.GPS.clear()
    import seguranca as seg
    seg._falhas.clear()


# ── PQI de teste com login falso (mesma regra de aba do main.py) ─────────
USUARIO = {"email": "pcl@ks", "nome": "Ana PCL", "abas": {}}


def usuario_atual(request: Request):
    return USUARIO


def nivel_aba(request, mod, aba):
    return int(USUARIO["abas"].get(aba, 0))


def exigir_aba(request, mod, abas, minimo, acao):
    abas = [abas] if isinstance(abas, str) else abas
    if not any(nivel_aba(request, mod, a) >= minimo for a in abas):
        raise HTTPException(403, f"{acao}: sem permissão")


def montar():
    app = FastAPI()
    Q.ligar(app, usuario_atual=usuario_atual, exigir_aba=exigir_aba, nivel_aba=nivel_aba)
    M.ligar(app)
    return app


OK = 0


def ok(cond, txt):
    global OK
    assert cond, txt
    OK += 1
    print("  ✓", txt)


def importar(numero, placa, pvs):
    peds = [{"seq": str(i + 1), "tipo": "VENDA", "pv": pv, "nf": f"9{i}", "cliente": f"C{i}", "nome": nome, "endereco": "Rua A, 1",
             "bairro": "Centro", "cidade": "SAO PAULO", "entregar": [{"desc": "CMP TESTE 138", "qtd": qtd}], "obs": []}
            for i, (pv, nome, qtd) in enumerate(pvs)]
    return Q.importar_mapa({"data_mapa": Q.datetime.now().strftime("%d/%m/%Y"), "arquivo": "teste.pdf",
                            "cargas": [{"numero": numero, "placa": placa, "transportadora": "KYODAI", "pedidos": peds}]},
                           {"por": "PCL", "email": "pcl@ks"})


FOTO = "data:image/jpeg;base64," + "A" * 200


# ══════════════════════════════════════════════════════════════════════════
def teste_fluxo():
    ligar_memoria()
    Q.placas()["ABC1D23"] = {"motorista": "João Ferreira"}
    importar("064201", "ABC1D23", [("739812", "Ana Rocha", 1), ("739835", "Carlos Dias", 2), ("739840", "Fernanda Melo", 1), ("739851", "Roberto Nunes", 3)])
    importar("064299", "XYZ9K87", [("740001", "Outra Placa", 1)])
    app = montar()
    mot = TestClient(app)      # celular do motorista
    pq = TestClient(app)       # PQI (login falso)

    print("Login pela placa")
    r = mot.post("/api/motorista/login", json={"placa": "abc-1d23", "senha": "errada"})
    ok(r.status_code == 401, "senha errada não entra")
    r = mot.post("/api/motorista/login", json={"placa": "ZZZ0Z00", "senha": M.SENHA_INICIAL})
    ok(r.status_code == 401, "placa que não existe não entra nem com a senha inicial")
    r = mot.post("/api/motorista/login", json={"placa": "abc-1d23", "senha": M.SENHA_INICIAL})
    ok(r.status_code == 200 and r.json()["trocar_senha"], "senha inicial entra e exige troca (placa digitada com traço e minúscula)")
    ok([m["nome"] for m in r.json()["motoristas"]] == ["João Ferreira"], "o nome do Cadastros → placa já aparece para escolher")
    ok(mot.get("/api/motorista/cargas").status_code == 403, "sem trocar a senha não vê carga")
    ok(mot.post("/api/motorista/senha", json={"nova": M.SENHA_INICIAL, "repetir": M.SENHA_INICIAL}).status_code == 400, "não aceita a própria senha inicial")
    ok(mot.post("/api/motorista/senha", json={"nova": "abc", "repetir": "abc"}).status_code == 400, "senha curta recusada")
    r = mot.post("/api/motorista/senha", json={"nova": "rota2026", "repetir": "rota2026"})
    ok(r.status_code == 200, "trocou a senha")
    ok(mot.get("/api/motorista/cargas").status_code == 403, "sem escolher o motorista não vê carga")
    mid_joao = r.json()["motoristas"][0]["id"]
    ok(mot.post("/api/motorista/quem", json={"id": "m00000000"}).status_code == 404, "motorista de outra placa não serve")
    r = mot.post("/api/motorista/quem", json={"nome_novo": "Marcos Lima"})
    ok(r.status_code == 200 and r.json()["motorista"]["nome"] == "Marcos Lima", "motorista novo se cadastra pelo app (palavra desligada)")
    ok(mot.post("/api/motorista/quem", json={"id": mid_joao}).status_code == 200, "João assume a placa")

    print("Cargas e palavra")
    r = mot.get("/api/motorista/cargas").json()
    ok([c["numero"] for c in r["cargas"]] == ["064201"], "só a carga VALIDADA da própria placa aparece")
    txt = str(r)
    palavras = BANCO[Q.COL_CARGAS]["064201"]["rua"]["palavras"]
    ok(len(set(palavras.values())) == 4, "cada pedido ganhou uma palavra diferente")
    ok(not any(w in txt for w in palavras.values()), "nenhuma palavra sai na resposta do app")
    ok(r["cargas"][0]["palavra_ligada"] is False, "palavra desligada por padrão")
    USUARIO["abas"] = {"motoristas": 3, "aovivo": 2}
    ms = pq.get("/api/qualidade/rua/motoristas").json()
    joao = next(m for m in ms["motoristas"] if m["nome"] == "João Ferreira")
    r = pq.put(f"/api/qualidade/rua/motoristas/{joao['id']}", json={"nome": "João Ferreira", "placas": joao["placas"], "palavra": True})
    ok(r.status_code == 200 and r.json()["palavra"] is True, "PCL liga a palavra só para o João")
    ok(mot.get("/api/motorista/cargas").json()["cargas"][0]["palavra_ligada"] is True, "app do João passa a pedir a palavra")

    print("Baixas na rua")
    r = mot.post("/api/motorista/cargas/064201/pedido/739812", json={"desfecho": "entregue"})
    ok(r.json()["resultado"] == "pede_palavra", "ENTREGUE sem palavra: o app pede a palavra")
    r = mot.post("/api/motorista/cargas/064201/pedido/739812", json={"desfecho": "entregue", "palavra": "naoexiste"})
    ok(r.json()["resultado"] == "palavra_errada" and r.json()["restam"] == 4, "palavra errada conta tentativa")
    w = palavras[Q.num_chave("739812")]
    r = mot.post("/api/motorista/cargas/064201/pedido/739812", json={"desfecho": "entregue", "palavra": w.lower() + " ", "cid": "a1", "lat": -23.5, "lng": -46.6})
    ok(r.json()["resultado"] == "ok", "palavra certa (minúscula, com espaço) libera ENTREGUE")
    r = mot.post("/api/motorista/cargas/064201/pedido/739812", json={"desfecho": "entregue", "palavra": w, "cid": "a1"})
    ok(r.json()["resultado"] == "ok", "reenvio do mesmo registro (sem sinal) não duplica nem dá erro")
    ok(mot.post("/api/motorista/cargas/064201/pedido/739812", json={"desfecho": "retorno", "motivo": "Cliente ausente", "cid": "x9"}).status_code == 409,
       "outro registro no mesmo pedido é recusado")
    for _ in range(5):
        mot.post("/api/motorista/cargas/064201/pedido/739835", json={"desfecho": "entregue", "palavra": "chute"})
    ok(mot.post("/api/motorista/cargas/064201/pedido/739835", json={"desfecho": "entregue", "palavra": palavras[Q.num_chave("739835")]}).status_code == 423,
       "depois de 5 erros trava até o PCL liberar (nem a palavra certa passa)")
    r = mot.post("/api/motorista/cargas/064201/pedido/739835/liberacao", json={"motivo": "Cliente não recebeu a mensagem"})
    ok(r.status_code == 200 and r.json()["pedidos"][1]["liberacao"] == "pendente", "motorista pede liberação")
    av = pq.get("/api/qualidade/rua/ao-vivo").json()
    ok(av["kpi"]["liberacoes"] == 1, "painel ao vivo mostra o pedido de liberação")
    r = pq.put("/api/qualidade/rua/cargas/064201/liberacao/739835", json={"acao": "liberar", "nota": "cliente confirmou por telefone"})
    ok(r.status_code == 200 and r.json()["liberacao"]["status"] == "liberada", "PCL libera")
    r = mot.post("/api/motorista/cargas/064201/pedido/739835", json={"desfecho": "entregue", "cid": "b1"})
    ok(r.json()["resultado"] == "ok", "liberado pelo PCL: registra ENTREGUE sem palavra")
    ok(BANCO[Q.COL_CARGAS]["064201"]["rua"]["pedidos"][Q.num_chave("739835")]["palavra_ok"] is False, "fica registrado que entrou SEM palavra")
    ok(mot.post("/api/motorista/cargas/064201/pedido/739840", json={"desfecho": "retorno"}).status_code == 400, "retorno exige motivo")
    ok(mot.post("/api/motorista/cargas/064201/pedido/739840", json={"desfecho": "retorno", "motivo": "Avaria no transporte"}).status_code == 400,
       "avaria exige foto")
    r = mot.post("/api/motorista/cargas/064201/pedido/739840", json={"desfecho": "retorno", "motivo": "Avaria no transporte", "foto": FOTO, "cid": "c1"})
    ok(r.json()["resultado"] == "ok" and len(BANCO[M.COL_FOTOS]) == 1, "retorno com avaria grava a foto")
    ok(mot.post("/api/motorista/cargas/064201/chegada", json={}).status_code == 409, "não fecha a carga com pedido sem registro")
    r = mot.post("/api/motorista/cargas/064201/pedido/739851", json={"desfecho": "parcial", "motivo": "Cliente ausente", "palavra": palavras[Q.num_chave("739851")],
                                                                     "linhas": [{"i": 0, "qtd": 2}], "cid": "d1"})
    ok(r.json()["resultado"] == "ok", "entrega PARCIAL: 2 de 3 peças voltam")
    r = mot.post("/api/motorista/cargas/064201/pedido/739851/desfazer")
    ok(r.status_code == 200 and not r.json()["pedidos"][3]["desfecho"], "motorista corrige o próprio registro em até 15 min")
    mot.post("/api/motorista/cargas/064201/pedido/739851", json={"desfecho": "parcial", "motivo": "Cliente ausente", "palavra": palavras[Q.num_chave("739851")],
                                                                "linhas": [{"i": 0, "qtd": 2}], "cid": "d2"})

    print("GPS e painel ao vivo")
    ok(mot.post("/api/motorista/gps", json={"lat": -23.56, "lng": -46.64, "prec": 12}).json()["ok"], "GPS recebido")
    av = pq.get("/api/qualidade/rua/ao-vivo").json()
    c = av["cargas"][0]
    ok(c["fase"] == "rua" and c["pos"]["lat"] == -23.56, "carga aparece NA RUA com a posição ao vivo")
    ok(c["cont"] == {**c["cont"], "entregue": 2, "retorno": 1, "parcial": 1, "pendente": 0}, "contadores batem (2 entregues, 1 retorno, 1 parcial)")
    ok(c["cont"]["pecas_esperadas"] == 3 and c["cont"]["pecas_recebidas"] == 0, "3 peças esperadas no CD, nenhuma recebida ainda")
    ok(c["pedidos"][0]["palavra"] == w, "o PCL vê a palavra (suporte)")
    ok(c["cont"]["sem_baixa_erp"] == 3, "3 pedidos entregues/parciais esperando baixa no ERP")
    ok(pq.put("/api/qualidade/rua/cargas/064201/baixa-erp/739840", json={"feito": True}).status_code == 409, "retorno não recebe baixa de entrega")
    r = pq.put("/api/qualidade/rua/cargas/064201/baixa-erp/739812", json={"feito": True})
    ok(r.status_code == 200 and r.json()["baixa_erp"]["por"] == "Ana PCL", "PCL marca a baixa no ERP com o nome dele")
    ok(mot.post("/api/motorista/cargas/064201/pedido/739812/desfazer").status_code == 409, "depois da baixa no ERP o motorista não corrige mais")
    r = mot.post("/api/motorista/cargas/064201/chegada", json={})
    ok(r.status_code == 200 and r.json()["chegada"], "Cheguei no CD fecha a rua")
    ok(mot.post("/api/motorista/cargas/064201/pedido/739812/desfazer").status_code == 409, "depois da chegada, só o PCL corrige")
    ok(pq.get("/api/qualidade/rua/ao-vivo").json()["cargas"][0]["fase"] == "chegou", "painel mostra a carga no CD")
    cg = BANCO[Q.COL_CARGAS]["064201"]
    ok(cg["status"] == "validada", "o status da carga no CD não muda: o Retorno continua recebendo as peças")
    ok(any("motorista" in h["por"] for h in cg["historico"]), "histórico da carga registra o motorista e a placa")
    pal = pq.get("/api/qualidade/rua/palavras").json()
    ok(any(x["pv"] == "739812" and x["palavra"] == w and x["exigida"] for x in pal), "lista de palavras do dia para o ERP")

    print("Senha, reset e permissões")
    USUARIO["abas"] = {"aovivo": 1}
    ok(pq.post("/api/qualidade/rua/placas/ABC1D23/resetar").status_code == 403, "só quem tem a aba Motoristas (Editar) reseta")
    ok(pq.put("/api/qualidade/rua/cargas/064201/baixa-erp/739835", json={"feito": True}).status_code == 403, "Visualizar não marca baixa")
    USUARIO["abas"] = {"motoristas": 3}
    ok(pq.post("/api/qualidade/rua/placas/ABC1D23/resetar").status_code == 200, "reset da senha")
    ok(mot.get("/api/motorista/cargas").status_code == 401, "reset derruba o celular logado")
    r = mot.post("/api/motorista/login", json={"placa": "ABC1D23", "senha": "rota2026"})
    ok(r.status_code == 401, "senha antiga não vale mais")
    r = mot.post("/api/motorista/login", json={"placa": "ABC1D23", "senha": M.SENHA_INICIAL})
    ok(r.status_code == 200 and r.json()["trocar_senha"], "volta a entrar com a inicial e precisa trocar")
    USUARIO["abas"] = {}
    ok(pq.get("/api/qualidade/rua/ao-vivo").status_code == 403, "sem a aba, sem painel ao vivo")
    for _ in range(5):
        mot.post("/api/motorista/login", json={"placa": "ABC1D23", "senha": "x"})
    ok(mot.post("/api/motorista/login", json={"placa": "ABC1D23", "senha": M.SENHA_INICIAL}).status_code == 429, "5 erros bloqueiam a placa por alguns minutos")


def teste_paginas():
    print("Páginas do app")
    app = montar()
    t = TestClient(app)
    for rota in ("/motorista/", "/motorista/sw.js", "/motorista/manifest.webmanifest", "/motorista/icone-192.png", "/motorista/icone-512.png"):
        ok(t.get(rota).status_code == 200, f"{rota} abre")
    r = t.get("/motorista", follow_redirects=False)
    ok(r.status_code == 307 and r.headers["location"] == "/motorista/", "/motorista leva para /motorista/")


if __name__ == "__main__":
    teste_fluxo()
    teste_paginas()
    print(f"\n{OK} verificações ok.")
