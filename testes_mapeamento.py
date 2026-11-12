"""
testes_mapeamento.py — módulo Mapeamento (v6.4), sem Firestore.

    python testes_mapeamento.py

Cobre: acesso (herda o maior dos três sistemas até ser configurado), card no Painel ao lado
dos três (que continuam), página ligada no main.py, e o "modo Mapeamento" nas três telas.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modulos as mods  # noqa: E402

RAIZ = os.path.dirname(os.path.abspath(__file__))
OK = 0


def ok(cond, txt):
    global OK
    assert cond, txt
    OK += 1
    print("  ✓", txt)


def ler(arq):
    return open(os.path.join(RAIZ, arq), encoding="utf-8").read()


print("Acesso")
n = mods.niveis_do_usuario({"email": "a@ks"}, {"niveis": {"organograma": 1, "diagnostico": 0, "mapa": 2}}, {})
ok(n["mapeamento"] == 2, "sem configurar: Mapeamento = maior nível dos três")
ok(n["organograma"] == 1 and n["mapa"] == 2, "os três sistemas continuam com o próprio nível")
n = mods.niveis_do_usuario({"email": "a@ks"}, {"niveis": {"organograma": 3, "mapeamento": 0}}, {})
ok(n["mapeamento"] == 0, "configurado: vale o que o admin definiu")
n = mods.niveis_do_usuario({"email": "a@ks"}, {"niveis": {"mapa": 0}}, {})
ok(n["mapeamento"] == 0, "quem não tem nenhum dos três não vê o Mapeamento")
cards = [c["id"] for c in mods.cards_do_usuario({"email": "a@ks"}, {"mapeamento": 2, "organograma": 1, "mapa": 2, "diagnostico": 1})]
ok({"mapeamento", "organograma", "mapa", "diagnostico"} <= set(cards), "Painel mostra o Mapeamento E os três de sempre")
ok(mods.modulo_da_rota("/mapeamento")["id"] == "mapeamento", "página protegida pelo módulo")

print("Arquivos")
ok('"/mapeamento": "mapeamento.html"' in ler("main.py"), "página ligada no main.py")
m = ler("mapeamento.html")
ok("mapeamento-ir" in m and "embed=mapeamento" in m and "modo=areas" in m, "4 passos e troca de passo pela própria tela")
a = ler("pqi-acesso.js")
ok("PQI_EMBED" in a and '"mapeamento-ir"' in a and "window.parent === window" in a, "modo Mapeamento só dentro do Mapeamento")
ok("EMB_AREAS" in ler("mapa.html"), "Processos: passo Áreas leva o processo para o passo 4")
ok("garantirAtividade" in ler("mapa.html") and "EMB_FLUXO" in ler("mapa.html"), "Fluxo interno: processo novo cria a atividade (no subsetor)")
ok('"/api/diagnostico/dep/{dep}/atividade"' in ler("main.py"), "rota que cria a atividade")
ok(mods.POR_ID["mapeamento"]["nome"] == "Fluxo interno", "módulo se chama Fluxo interno (id continua mapeamento)")
d = ler("diagnostico.html")
ok('"Atividades",renderInventario' in d and '["inventario","organograma"]' in d, "Diagnóstico: Inventário vira Atividades; no Mapeamento só Atividades e Organograma")
ok('v[0]==="importar"' in ler("organograma.html"), "Organograma: sem Importar dentro do Mapeamento")

print(f"\n{OK} verificações ok.")
