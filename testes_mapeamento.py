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


print("Acesso — Organograma e Processos viraram passos do Fluxo interno (v7.3)")
U = {"email": "a@ks"}
ab = lambda u, perfil, perfis=None: mods.abas_do_usuario(u, perfil, mods.niveis_do_usuario(u, perfil, perfis or {}), perfis or {})["mapeamento"]
perfil = {"niveis": {"organograma": 1, "diagnostico": 0, "mapa": 2}}
n = mods.niveis_do_usuario(U, perfil, {})
ok(n["mapeamento"] == 2 and "organograma" not in n and "mapa" not in n, "não existem mais os sistemas Organograma e Processos; o Fluxo interno vale o maior")
a = ab(U, perfil)
ok(a == {"organograma": 1, "atividades": 0, "areas": 2, "processos": 2}, "cada passo herda o que a pessoa tinha: Organograma 1 · Áreas/Processos 2 (Adicionar)")
a = ab(U, {"niveis": {"organograma": 3, "mapa": 3}})
ok(a["organograma"] == 4 and a["processos"] == 4, "quem editava tudo continua podendo excluir (aba 4)")
a = ab({"email": "b@ks", "acessos": {"mapa": "perfil:ger"}}, {"niveis": {"mapa": 1}}, {"ger": {"niveis": {"mapa": 3}}})
ok(a["processos"] == 4 and a["organograma"] == 0, "exceção individual antiga (perfil:Gestor em Processos) continua valendo")
a = ab({"email": "c@ks", "modulos": ["organograma"]}, None)
ok(a["organograma"] == 4 and a["processos"] == 0, "cadastro antigo sem perfil (lista de módulos) também é respeitado")
n = mods.niveis_do_usuario(U, {"niveis": {"diagnostico": 2}}, {})
ok(n["mapeamento"] == 2 and ab(U, {"niveis": {"diagnostico": 2}})["organograma"] == 0 and ab(U, {"niveis": {"diagnostico": 2}})["atividades"] == 2,
   "só Diagnóstico: entra no Fluxo interno, mas só o passo Atividades")
p2 = {"niveis": {"mapeamento": 1, "organograma": 3}, "abas": {"mapeamento": {"processos": 3}}}
a = ab(U, p2)
ok(a["organograma"] == 4 and a["processos"] == 3, "configurado em Segurança: aba definida vale; aba não definida nunca fica abaixo do que a pessoa já tinha")
p3 = {"niveis": {"mapeamento": 1, "organograma": 3}, "abas": {"mapeamento": {"organograma": 1}}}
ok(ab(U, p3)["organograma"] == 1, "para reduzir de propósito: defina a aba em Segurança")
ok(mods.niveis_do_usuario(U, {"niveis": {"mapa": 0}}, {})["mapeamento"] == 0, "quem não tinha nada não vê o Fluxo interno")
adm = mods.abas_do_usuario({"papel": "admin"}, None, mods.niveis_do_usuario({"papel": "admin"}, None, {}), {})["mapeamento"]
ok(set(adm.values()) == {4}, "administrador: tudo")
cards = [c["id"] for c in mods.cards_do_usuario(U, {"mapeamento": 2, "diagnostico": 1, "melhoria": 2})]
ok("mapeamento" in cards and "organograma" not in cards and "mapa" not in cards, "Painel: sem os cards Organogramas e Processos")
m = mods.modulo_da_rota("/api/mapa/processos")
ok(m["id"] == "mapeamento" and mods.abas_da_rota(m, "/api/mapa/processos") == ["areas", "processos"], "API de processos: protegida pelos passos Áreas/Processos")
ok(mods.abas_da_rota(mods.modulo_da_rota("/organograma"), "/organograma") == ["organograma"], "página do organograma: protegida pelo passo Organograma")
ok(mods.modulo_da_rota("/mapeamento")["id"] == "mapeamento", "página protegida pelo módulo")

print("O Diagnóstico foi dividido (v7.5): Atividades → Fluxo interno · ferramentas → Melhoria Contínua")
ok("diagnostico" not in mods.IDS_VALIDOS, "não existe mais o sistema Diagnóstico (nem o card no Painel)")
ml = lambda u, perfil, perfis=None: mods.abas_do_usuario(u, perfil, mods.niveis_do_usuario(u, perfil, perfis or {}), perfis or {})["melhoria"]
ok(ml(U, {"niveis": {"diagnostico": 3}})["diagnostico"] == 4 and ab(U, {"niveis": {"diagnostico": 3}})["atividades"] == 4,
   "quem editava o Diagnóstico continua editando: Atividades (Fluxo interno) e Ferramentas (Melhoria)")
ok(ml(U, {"niveis": {"mapeamento": 3}})["diagnostico"] == 0, "Melhoria é para todos, mas as Ferramentas do diagnóstico não: só quem tinha o Diagnóstico")
ok(ml(U, {"niveis": {"diagnostico": 1}, "abas": {"melhoria": {"diagnostico": 3}}})["diagnostico"] == 3, "configurado em Segurança: vale a aba")
ok(ml({"email": "x@ks", "acessos": {"diagnostico": 2}}, None)["diagnostico"] == 2, "exceção individual antiga no Diagnóstico também vale")
ok(mods.nivel_efetivo_sistema(2, {"diagnostico": 4}, "melhoria") == 2, "Editar nas ferramentas NÃO dá Editar nas melhorias")
r = mods.rota_diagnostico
ok(r("/api/diagnostico/dep/log/inventario", "PUT") == [("mapeamento", ["atividades"])], "gravar Atividades: aba Atividades do Fluxo interno")
ok(r("/api/diagnostico/dep/log/organograma", "PUT") == [("mapeamento", ["atividades"])], "organograma real: Fluxo interno")
ok(r("/api/diagnostico/dep/log/entrevistas", "PUT") == [("melhoria", ["diagnostico"])], "entrevistas, RACI, Gemba…: Ferramentas da Melhoria")
ok(r("/api/diagnostico-ia", "POST") == [("melhoria", ["diagnostico"])], "resumo e sugestões com IA: Melhoria")
ok(len(r("/api/diagnostico/dep/log", "GET")) == 2 and len(r("/diagnostico")) == 2, "abrir o setor e a tela: quem tem qualquer uma das duas")
ok(r("/api/diagnostico/departamentos", "POST") == [("mapeamento", ["atividades"])], "criar departamento (setor novo): Fluxo interno")
ok(r("/api/mapa/processos") is None, "o resto não muda")
mm = ler("main.py")
ok("_nivel_diag(usuario_atual(request), k)" in mm, "abrir o setor devolve só as seções que a pessoa pode ver")
ok("agt.provedor()" in mm and "agt.ia_texto(" in mm, "resumo com IA usa a IA do sistema (Groq gratuita)")
d = ler("diagnostico.html")
ok('data-modulo="mapeamento" data-aba="atividades"' in d and "EMBED_MELHORIA" in d and "GRUPOS_MELHORIA" in d, "a tela mostra Atividades no Fluxo interno e as ferramentas na Melhoria")
ok('"/diagnostico?embed=melhoria"' in ler("melhoria.html") and 'nivelAba("diagnostico")' in ler("melhoria.html"), "Melhoria: aba Ferramentas do diagnóstico")
ok('"/diagnostico"].indexOf(location.pathname)' in ler("pqi-acesso.js"), "link antigo do Diagnóstico abre no Fluxo interno")

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
ok("pqi-ajuda.js" in ler("pqi-acesso.js") and '"/pqi-ajuda.js"' in ler("main.py"), "(i) explicativo carregado só dentro do Fluxo interno")
ok("GUIA" in ler("mapeamento.html") and "Pronto quando" in ler("mapeamento.html"), "cada passo diz o que fazer, quando está pronto e o próximo")
d = ler("diagnostico.html")
ok('"Atividades",renderInventario' in d and 'ABAS_FLUXO = ["inventario","organograma"]' in d, "Diagnóstico: Inventário vira Atividades; no Mapeamento só Atividades e Organograma")
ok('["importar","importar","Importar"]' in ler("organograma.html") and 'v[0]==="importar"' not in ler("organograma.html"), "Organograma: Importar de volta no passo 1")
ok('data-modulo="mapeamento" data-aba="organograma"' in ler("organograma.html") and 'data-modulo="mapeamento" data-aba="processos"' in ler("mapa.html"), "telas dos passos usam o acesso do passo")
ok("PQI_ABRIR_FLUXO" in ler("index.html") and "location.replace(\"/mapeamento?ir=\"" in ler("pqi-acesso.js"), "Agente e links antigos abrem no Fluxo interno")

print(f"\n{OK} verificações ok.")
