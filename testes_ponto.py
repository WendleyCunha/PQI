"""
testes_ponto.py — testes do módulo Ponto SEM Firestore (banco na memória).

    python testes_ponto.py

Cobre: regras de apuração, NSR sem buraco e sem repetição, corrente de hash,
batida assinada pelo agente (e recusa de batida falsificada), batida repetida,
ajuste que nunca altera a batida original, e permissão por aba (Gerente × pessoa
com só a aba Cadastros).
"""
import base64
import copy
import hashlib
import hmac
import json
import os
import sys
import threading
from datetime import date, timedelta

os.environ.setdefault("PONTO_CHAVE", base64.urlsafe_b64encode(b"k" * 32).decode())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ponto as P  # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ── banco na memória ──────────────────────────────────────────────────────
BANCO: dict = {}
_trava_tx = threading.Lock()


def _get(col, i):
    return copy.deepcopy(BANCO.get(col, {}).get(i))


def _set(col, i, d):
    BANCO.setdefault(col, {})[i] = copy.deepcopy(d)


def _todos(col):
    return [{**copy.deepcopy(v), "_id": k} for k, v in BANCO.get(col, {}).items()]


def _periodo(col, campo, de, ate):
    return [copy.deepcopy(v) for v in BANCO.get(col, {}).values() if de <= v.get(campo, "") <= ate]


def _gravar_marc(montar):
    with _trava_tx:
        cont = BANCO.get(P.COL_CFG, {}).get("contador") or {"nsr": 0, "hash": ""}
        m = montar(cont)
        _set(P.COL_MARC, f"{m['nsr']:010d}", m)
        _set(P.COL_CFG, "contador", {"nsr": m["nsr"], "hash": m["hash"]})
        return m


ORG = [{"id": "loja", "dados": {"nome": "Loja Osasco", "cor": "#2E4A7A", "pessoas": [
    {"id": "p1", "nome": "Ana Souza", "cargo": "Gerente de loja", "horario": "08:00 às 17:48 · intervalo 12:00-13:00", "escala": "6x1"},
    {"id": "p2", "nome": "Bruno Lima", "cargo": "Vendedor", "gestor_id": "p1", "horario": "09:00 às 18:00", "email": "Bruno@ks"},
    {"id": "p3", "nome": "", "cargo": "Vendedor", "vaga": True}]}}]


def ligar_memoria():
    BANCO.clear()
    P._fs_get, P._fs_set, P._fs_todos, P._fs_periodo = _get, _set, _todos, _periodo
    P._fs_gravar_marcacao, P._fs_organograma = _gravar_marc, (lambda: copy.deepcopy(ORG))
    P._cfg = None
    P._colab = None
    P._aj = None
    P._marc.update(carregado=False, desde="", lista=[])
    P._org.update(em=0.0, pessoas={})
    P._DESAFIOS_USADOS.clear()


# ── app de teste com login falso (mesma regra de aba do main.py) ─────────
USUARIO = {"email": "gerente@ks", "nome": "Gerente", "papel": "usuario", "abas": {}}


def usuario_atual(request: Request):
    return USUARIO


def nivel_aba(request, mod, aba):
    if USUARIO.get("papel") == "admin":
        return 4
    return int(USUARIO["abas"].get(aba, 0))


def exigir_aba(request, mod, abas, minimo, acao):
    abas = [abas] if isinstance(abas, str) else abas
    if not any(nivel_aba(request, mod, a) >= minimo for a in abas):
        raise HTTPException(403, f"{acao}: sem permissão")


def cliente():
    app = FastAPI()
    P.ligar(app, usuario_atual=usuario_atual, exigir_aba=exigir_aba, nivel_aba=nivel_aba)
    return TestClient(app)


GERENTE = {"hoje": 4, "marcar": 4, "espelho": 4, "tratamento": 4, "cadastros": 4}
OK = 0


def ok(cond, txt):
    global OK
    assert cond, txt
    OK += 1
    print("  ✓", txt)


# ══════════════════════════════════════════════════════════════════════════
def teste_apuracao():
    print("Apuração (funções puras)")
    R = dict(P.REGRAS_PADRAO)
    j = {"dias": [0, 1, 2, 3, 4], "entrada": "08:00", "saida_int": "12:00", "retorno": "13:00", "saida": "17:48"}
    seg = "2026-11-02"   # segunda
    prev = P.previsto_do_dia(j, seg, [])
    ok(prev["min"] == 528, "jornada 08:00–17:48 com 1 h de intervalo = 8h48 previstas")
    r = P.apurar_dia(["08:03", "12:00", "13:00", "17:50"], prev, R)
    ok(r["saldo"] == 0 and not r["inconsistencias"], "variação de 5 min no dia cai na tolerância")
    r = P.apurar_dia(["08:20", "12:00", "13:00", "17:48"], prev, R)
    ok(r["saldo"] == -20 and "atraso" in r["inconsistencias"], "atraso de 20 min desconta 20 (passou da tolerância, conta tudo)")
    r = P.apurar_dia(["08:00", "12:00", "13:00"], prev, R)
    ok("marcacao_impar" in r["inconsistencias"] and r["saldo"] == 0, "batida ímpar vira pendência e não calcula saldo")
    r = P.apurar_dia([], prev, R)
    ok(r["falta"] and r["saldo"] == -528, "dia útil sem batida = falta")
    r = P.apurar_dia([], prev, R, abonado=True)
    ok(not r["falta"] and r["saldo"] == 0, "dia abonado não é falta")
    r = P.apurar_dia(["08:00", "12:00", "12:30", "17:48"], prev, R)
    ok("intervalo_curto" in r["inconsistencias"], "intervalo de 30 min em jornada > 6 h é sinalizado")
    r = P.apurar_dia(["08:00", "12:00", "13:00", "20:30"], prev, R)
    ok(r["saldo"] == 162 and "extra_excedida" in r["inconsistencias"], "2h42 extras → alerta de mais de 2 h")
    sab = P.previsto_do_dia(j, "2026-11-07", [])
    r = P.apurar_dia(["09:00", "13:00"], sab, R)
    ok(sab["min"] == 0 and "trabalho_em_folga" in r["inconsistencias"], "batida no sábado de folga é sinalizada")
    ok(P.previsto_do_dia(j, seg, [seg])["min"] == 0, "feriado não tem previsto")
    ok(P.jornada_do_horario("08:00 às 17:48 · intervalo 12:00-13:00") == {"dias": [0, 1, 2, 3, 4], "entrada": "08:00", "saida_int": "12:00", "retorno": "13:00", "saida": "17:48"},
       "horário em texto do Organograma vira jornada sugerida")
    ok(P.cpf_valido("52998224725") and not P.cpf_valido("12345678900"), "validação de CPF")


def teste_fluxo():
    print("Fluxo completo (cadastro → digital → batida assinada → espelho → ajuste)")
    ligar_memoria()
    USUARIO.update(papel="usuario", abas=dict(GERENTE))
    c = cliente()
    ok(c.put("/api/ponto/unidades", json={"unidades": [{"nome": "Loja Osasco", "cidade": "Osasco", "uf": "sp"}]}).status_code == 200, "cria unidade")
    ok(not any(r.path == "/api/ponto/terminais" and "POST" in r.methods for r in P.router.routes), "não existe mais cadastro manual de terminal nem segredo")
    lista = c.get("/api/ponto/colaboradores").json()
    ok(len(lista) == 2 and all(not p["cadastrado"] for p in lista), "pessoas vêm do Organograma (vaga fica de fora)")
    r = c.put("/api/ponto/colaboradores/loja.p1", json={"cpf": "529.982.247-25", "matricula": "100", "unidade_id": "loja-osasco", "ativo": True,
                                                       "jornada": {"dias": [0, 1, 2, 3, 4, 5, 6], "entrada": "08:00", "saida_int": "12:00", "retorno": "13:00", "saida": "17:48"}})
    ok(r.status_code == 200 and r.json()["ativo"], "ativa a pessoa no ponto com CPF e jornada")
    ok(c.put("/api/ponto/colaboradores/loja.p2", json={"cpf": "52998224725", "ativo": True, "jornada": {}}).status_code == 409, "CPF repetido é recusado")
    ok(c.put("/api/ponto/colaboradores/loja.p2", json={"cpf": "111", "ativo": True, "jornada": {}}).status_code == 400, "CPF inválido é recusado")

    # o terminal é o LOGIN: nasce sozinho, sem escolher e sem segredo
    T = c.get("/api/ponto/terminal").json()
    seg, tid = T["chave"], T["id"]
    ok(T["login"] == "gerente@ks" and T["ativo"] and seg, "abrir Bater ponto cria o terminal deste login automaticamente")
    ok(c.get("/api/ponto/terminal").json()["id"] == tid and len(P.cfg()["terminais"]) == 1, "o mesmo login é sempre o mesmo terminal")
    ok(T["unidade_id"] == "", "sem pista ainda, a unidade fica em branco (automática)")
    ok("chave" not in json.dumps(c.get("/api/ponto/inicio").json()["terminais"]) and "chave_cif" not in json.dumps(c.get("/api/ponto/inicio").json()), "a chave nunca aparece nas listagens")

    # cadastro da digital: o agente lê e cifra com a chave do terminal do login
    k_cap = P._k_terminal(seg, "captura")
    template = hashlib.sha256(b"simulado|ana").digest() * 2
    pac = P.cifrar(k_cap, template, b"captura")
    ok(c.post("/api/ponto/colaboradores/loja.p1/digital", json={"dedo": 2, "pacote": pac, "qualidade": 90}).status_code == 400,
       "sem aceite do termo LGPD a digital não é gravada")
    r = c.post("/api/ponto/colaboradores/loja.p1/digital", json={"dedo": 2, "pacote": pac, "qualidade": 90, "termo_lgpd": True})
    ok(r.status_code == 200 and r.json()["digitais"][0]["dedo"] == 2, "digital gravada (sem escolher leitor/terminal)")
    guardado = BANCO[P.COL_COLAB]["loja.p1"]["digitais"][0]["t"]
    ok(template not in base64.b64decode(guardado), "no banco a digital está cifrada (nunca o template aberto)")
    falso = P.cifrar(P._k_terminal("outra-chave", "captura"), template, b"captura")
    ok(c.post("/api/ponto/colaboradores/loja.p1/digital", json={"dedo": 3, "pacote": falso, "termo_lgpd": True}).status_code == 400,
       "leitura cifrada com outra chave é recusada")

    pk = c.get("/api/ponto/terminal/pacote").json()
    itens = json.loads(P.decifrar(P._k_terminal(seg, "pacote"), pk["pacote"], tid.encode()))["itens"]
    ok(base64.b64decode(itens[0]["t"]) == template and itens[0]["p"] == "loja.p1", "pacote de digitais abre com a chave do terminal do login")

    def bater(pessoa="loja.p1", segredo=seg, score=100):
        d = c.get("/api/ponto/desafio").json()["desafio"]
        mom = "2026-11-03T08:01:00-03:00"
        ass = hmac.new(P._k_terminal(segredo, "assina"), f"{d}|{pessoa}|{score}|{mom}".encode(), hashlib.sha256).hexdigest()
        return c.post("/api/ponto/marcar", json={"pessoa": pessoa, "score": score, "momento_aparelho": mom, "desafio": d, "assinatura": ass})

    r = bater()
    ok(r.status_code == 200 and r.json()["nsr"] == 1 and r.json()["tipo"] == "Entrada", "batida só com o dedo (nada escolhido) grava NSR 1 (Entrada)")
    ok(P.terminal(tid)["unidade_id"] == "loja-osasco" and P.terminal(tid)["unidade_auto"], "unidade do terminal veio sozinha (a de quem bateu o 1º ponto)")
    ok(BANCO[P.COL_MARC]["0000000001"]["operador"] == "gerente@ks", "a batida guarda o login do terminal")
    ok(bater().status_code == 409, "batida repetida em menos de 2 min não grava")
    ok(bater(segredo="falso").status_code == 403, "assinatura falsa (sem o leitor) é recusada")
    d = c.get("/api/ponto/desafio").json()["desafio"]
    ass = hmac.new(P._k_terminal(seg, "assina"), f"{d}|loja.p1|100|x".encode(), hashlib.sha256).hexdigest()
    corpo = {"pessoa": "loja.p1", "score": 100, "momento_aparelho": "x", "desafio": d, "assinatura": ass}
    P.cfg()["regras"]["duplicada_min"] = 0   # libera a regra dos 2 min para o resto do teste
    ok(c.post("/api/ponto/marcar", json=corpo).status_code == 200, "batida nova grava")
    ok(c.post("/api/ponto/marcar", json=corpo).status_code == 400, "o mesmo desafio não serve duas vezes (sem replay)")
    ok(c.post("/api/ponto/marcar", json={"metodo": "teste", "pessoa": "loja.p1"}).status_code == 403, "modo teste é só para administrador")

    # outro login = outro terminal; unidade pelo Organograma (mesmo e-mail)
    eu = dict(USUARIO)
    USUARIO.update(email="bruno@ks", nome="Bruno")
    c.put("/api/ponto/unidades", json={"unidades": [{"id": "loja-osasco", "nome": "Loja Osasco"}, {"nome": "CD Barueri"}]})
    c.put("/api/ponto/colaboradores/loja.p2", json={"cpf": "11144477735", "ativo": True, "unidade_id": "cd-barueri", "jornada": {}})
    T2 = c.get("/api/ponto/terminal").json()
    ok(T2["id"] != tid and T2["unidade_id"] == "cd-barueri", "outro login vira outro terminal, com a unidade da pessoa do login no Organograma")
    ok(c.put(f"/api/ponto/terminais/{T2['id']}", json={"unidade_id": "loja-osasco"}).status_code == 200 and not P.terminal(T2["id"])["unidade_auto"],
       "corrigir a unidade à mão fica gravado para aquele login")
    ok(c.put(f"/api/ponto/terminais/{T2['id']}", json={"ativo": False}).status_code == 200 and
       c.post("/api/ponto/marcar", json={"metodo": "teste", "pessoa": "loja.p1"}).status_code == 403, "terminal bloqueado não registra batida")
    USUARIO.clear(); USUARIO.update(eu)

    # muitas batidas em paralelo: NSR sem buraco nem repetição
    USUARIO["papel"] = "admin"
    for _ in range(25):
        for pid in ("loja.p1", "loja.p2"):
            assert c.post("/api/ponto/marcar", json={"metodo": "teste", "pessoa": pid}).status_code == 200
    nsrs = sorted(m["nsr"] for m in BANCO[P.COL_MARC].values())
    ok(nsrs == list(range(1, len(nsrs) + 1)), f"{len(nsrs)} batidas em 2 pessoas: NSR de 1 a {len(nsrs)} sem buraco nem repetição")
    USUARIO["papel"] = "usuario"

    # corrente íntegra; adulterar no banco quebra
    ok(c.get("/api/ponto/integridade").json()["ok"], "corrente de hash íntegra")
    copia = copy.deepcopy(P._marc["lista"][0])
    P._marc["lista"][0]["momento"] = "2026-11-03T07:00:00-03:00"
    ok(not c.get("/api/ponto/integridade").json()["ok"], "alterar uma batida direto no banco é detectado")
    P._marc["lista"][0] = copia

    # ajuste nunca altera a batida
    antes = copy.deepcopy(BANCO[P.COL_MARC])
    h = P.hoje()
    ok(c.post("/api/ponto/ajustes", json={"pessoa": "loja.p1", "data": h, "tipo": "incluir", "horario": "12:00", "motivo": ""}).status_code == 400, "ajuste sem motivo é recusado")
    r = c.post("/api/ponto/ajustes", json={"pessoa": "loja.p1", "data": h, "tipo": "incluir", "horario": "12:00", "motivo": "esqueceu no almoço — confirmado"})
    ok(r.status_code == 200 and BANCO[P.COL_MARC] == antes, "ajuste grava à parte e a batida original fica intacta")
    e = c.get(f"/api/ponto/espelho?pessoa=loja.p1&de={h}&ate={h}").json()
    ok("12:00" in e["dias"][0]["batidas"], "espelho já considera a batida incluída")
    ok(sum(1 for x in P.ajustes() if x["horario"] == "12:00") == 1, "o ajuste aparece uma vez só (sem duplicar na memória)")
    ok(not e["dias"][0]["falta"] and "marcacao_impar" not in e["dias"][0]["inconsistencias"], "o dia de hoje (ainda em andamento) não vira falta nem pendência")
    ok(not any(r.path.endswith("{nsr}") and "DELETE" in r.methods for r in P.router.routes) and
       not any("/marc" in r.path and ({"PUT", "DELETE", "PATCH"} & r.methods) for r in P.router.routes), "não existe rota para alterar ou apagar batida")


def teste_permissoes():
    print("Permissões por aba")
    ligar_memoria()
    c = cliente()
    USUARIO.update(papel="usuario", abas={})
    ok(c.get("/api/ponto/inicio").status_code == 403, "sem nenhuma aba: não abre")
    USUARIO["abas"] = {"cadastros": 4}   # alçada menor: SÓ Cadastros, edição completa
    ok(c.get("/api/ponto/colaboradores").status_code == 200, "só-Cadastros: vê os colaboradores")
    ok(c.put("/api/ponto/unidades", json={"unidades": [{"nome": "CD"}]}).status_code == 200, "só-Cadastros: edita unidades")
    t = c.get("/api/ponto/terminal").json()
    ok(c.delete(f"/api/ponto/terminais/{t['id']}").status_code == 200, "só-Cadastros com Excluir: exclui terminal")
    ok(c.get("/api/ponto/hoje").status_code == 403, "só-Cadastros: NÃO vê o Hoje")
    ok(c.get("/api/ponto/espelho?pessoa=loja.p1").status_code == 403, "só-Cadastros: NÃO vê espelho")
    ok(c.post("/api/ponto/ajustes", json={"pessoa": "x", "data": "2026-01-01", "tipo": "abonar", "motivo": "teste teste"}).status_code == 403, "só-Cadastros: NÃO ajusta ponto")
    USUARIO["abas"] = {"cadastros": 3}
    t = c.get("/api/ponto/terminal").json()
    ok(c.delete(f"/api/ponto/terminais/{t['id']}").status_code == 403, "Cadastros em Editar (sem Excluir): não exclui")
    USUARIO["abas"] = {"hoje": 1, "espelho": 1}
    ok(c.put("/api/ponto/unidades", json={"unidades": []}).status_code == 403, "só consulta: não grava cadastro")


if __name__ == "__main__":
    teste_apuracao()
    teste_fluxo()
    teste_permissoes()
    print(f"\n{OK} verificações ok.")
