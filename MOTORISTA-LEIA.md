# APP DO MOTORISTA e PAINEL AO VIVO (módulo Qualidade do PQI · v5.2)

Arquivos: `motorista.py` (rotas `/api/motorista/*` do app e `/api/qualidade/rua/*` do painel),
`motorista.html` (o app), `motorista-sw.js` + `motorista.webmanifest` + `motorista-192/512.png` (instalar no Android),
abas novas em `qualidade.html`, 2 abas em `modulos.py`, 7 linhas em `main.py` e `testes_motorista.py`.

## O fluxo

1. A carga é importada e validada como hoje (fase 1: importada → validada; fase 2: montagem → doca → validada).
2. **Só carga validada aparece no app** da placa.
3. O motorista toca **Iniciar rota**: o GPS liga e o PCL vê "Na rua".
4. Em cada cliente: **Entregue**, **Retorno** (motivo e foto) ou **Parcial** (quais peças voltaram).
   Sem sinal, o registro fica guardado no celular e vai sozinho quando a conexão volta.
5. Terminou: **Cheguei no CD**. A carga sai de "Na rua".
6. Aba **Ao vivo** (PCL): tudo aparece na hora. O PCL marca **Baixado no ERP** em cada entrega
   (a baixa no Protheus continua manual) e vê os retornos informados × peças recebidas no CD.
7. O **Retorno** do CD continua igual e agora mostra o que o motorista informou ("📱 Motorista: …").
   A peça só entra no estoque pelo Retorno. O app não muda o status da carga.

## Placa (padrão único, sem traço)

- Em todo o PQI a placa fica **sem traço e sem espaço**: o PDF traz `FIB-3J44` e o sistema grava `FIB3J44`.
  Vale para o Mapa importado, Cadastros → placas, o app, o Ao vivo e os indicadores. Dados antigos com traço
  são convertidos sozinhos ao carregar.
- O motorista pode digitar com ou sem traço, maiúscula ou minúscula.
- Uma placa entra no app se estiver em Cadastros → placas, em **Motoristas e app** (placas de um motorista)
  ou numa carga dos últimos 15 dias.

## Trocar a placa da carga

- No detalhe da carga ou no Ao vivo: **🚚 Trocar placa** (aba PCL ou Ao vivo, nível Adicionar).
- A placa nova **assume todas as entregas pendentes**. O que o motorista anterior já registrou fica na carga.
  A placa antiga deixa de ver a carga no app. O motorista novo toca **Iniciar rota** de novo.
- Fica no histórico (de → para, quem, quando, motivo). Depois de "Cheguei no CD" não dá mais para trocar.
- PDF novo da mesma carga com a placa antiga não desfaz a troca; PDF com outra placa manda.
- PDF novo **não mexe** em carga que o motorista já começou no app (fica em "bloqueadas" no relatório da importação).

## Voltar para a montagem

- Com a fase 1, a carga importada vai direto para o Retorno. Para passar a montar os paletes:
  Cadastros → Listas → ligue a **fase 2** → **Gravar as listas** → **↩ Levar para a montagem**
  (cargas de hoje em diante sem retorno e sem registro no app). Também há o botão em cada carga.

## Palavra da entrega

- Cada pedido ganha uma palavra (ex.: GIRASSOL), diferente das outras da carga.
- **Liga/desliga por motorista** em **Motoristas e app**, para implantar aos poucos.
- Ligada: **Entregue/Parcial só com a palavra**. Maiúscula, minúscula e acento não importam. Retorno não pede palavra.
- Errou N vezes (padrão 5, ajustável) → trava. O motorista toca "Cliente não tem a palavra" e o PCL
  **libera ou nega** na aba Ao vivo. Fica registrado se entrou com palavra ou liberado pelo PCL.
- A palavra **nunca** vai para o app; só o servidor confere. No PQI ela aparece no Ao vivo (suporte).
- Para o cliente receber: `GET /api/qualidade/rua/palavras?data=AAAA-MM-DD` (pela ponte do hub, no ERP do Desenvolvimento).

## Login do app

- Endereço: `https://<pqi>/motorista/` · no Chrome do Android aparece **Instalar** (vira ícone).
- **Placa** + senha inicial **King@2026** (ou a da variável `MOTORISTA_SENHA_INICIAL` no Render).
  O app obriga a trocar no 1º acesso. Depois o motorista escolhe o próprio nome (ou se cadastra).
- **Resetar senha** (Motoristas e app) volta para a inicial e derruba o celular logado.
- 5 senhas erradas bloqueiam a placa por 5 min (aparece em Segurança → bloqueios).
- O cookie do app (`pqi_motorista`) não abre nada do PQI.

## Liberar o acesso (Segurança)

- Aba **Ao vivo**: Visualizar = ver; Adicionar = liberar palavra e marcar baixa no ERP; Editar = reabrir registro do motorista.
- Aba **Motoristas e app**: Editar = palavra, motoristas, reset de senha.
- Quem já tem a aba **PCL** também enxerga e trata o Ao vivo.

## Limites conhecidos

- **GPS só com o app aberto** (limite do navegador). Há a opção "Manter a tela ligada".
  Rastreio com a tela apagada exige um app nativo (APK).
- Mapa do Ao vivo usa OpenStreetMap (gratuito).
- Plano grátis do Firestore: a posição ao vivo fica na memória; a trilha grava 1 ponto a cada 3 min.

## Testes

```
python testes_motorista.py
```

# QUALIDADE COMO APP (v5.3)

- Endereço: `https://<pqi>/qualidade` aberto **direto no Chrome do celular** (não pelo Painel).
  Aparece **⤓ Instalar app** (ou menu ⋮ → Instalar app / Adicionar à tela inicial). Vira o ícone "Qualidade".
- O login é o mesmo do PQI (e-mail e senha). Sem login, o app abre a tela de login e volta sozinho para a Qualidade.
- Cada pessoa vê **só as abas liberadas** para ela (Segurança → abas da Qualidade). Quem tem uma aba só
  (ex.: o chapa da Montagem) nem vê o menu: abre direto na etapa dele.
- No app instalado o botão "Painel de Sistemas" vira **Sair** (para trocar de pessoa no mesmo celular).
- Para não pedir login todo dia, ligue "Lembrar de mim" na configuração de login do PQI.

# v5.4 — APP DO PQI, UMA ETAPA POR PESSOA, SEM LEITOR, ENTRADA AVULSA

## App do PQI (sistema todo)
- Tela de login: **⤓ Baixar o app no celular** (Chrome/Android instala; no iPhone mostra o passo a passo).
- O app abre o Painel com os sistemas e as abas liberados para o login. Quem tem um sistema só abre direto nele.
- **Manter conectado por 30 dias** vem marcado (os dias vêm de Configurações).

## Cada etapa da carga por uma pessoa
- Etapas: **PCL** (importar o PDF, pendências, refazer, trocar placa, Ao vivo, apontar no ERP),
  **montagem**, **conferência na doca** e **retorno**.
- Quem agiu numa etapa de uma carga não age em outra etapa **da mesma carga**. A tela mostra o aviso.
- O **ADMIN** faz todas (para testes). Quem fez cada etapa fica em `etapas_pessoas` na carga.

## Peça com código e sem leitor
- Montagem/doca: **✓ 1 sem leitor** → pede o motivo → a peça conta, marcada "sem leitor" (aparece no detalhe da carga).
- Retorno: **Voltou 1 (sem leitor)** e, no lote, o campo "Sem leitor? Motivo".
- Se fechar o palete com a peça sem confirmar, ela vira FALTA (pendência no PCL), como antes.

## Entrada avulsa (Retorno)
- **＋ Entrada avulsa**: peça que chegou fora do PDF (loja, cliente, transferência…). Vira a carga **AVaammdd-nn**,
  já Em retorno, e segue: concluir retorno → GAP → PCL aponta no ERP. Não aparece no app do motorista nem no Ao vivo.

Testes: `python testes_qualidade.py`

# v5.5 — CHAT MOTORISTA ↔ FROTA E CLIENTE COMPLETO NO AO VIVO

- App: a tela fica **sempre ligada** com o motorista logado (sem opção na tela). Não existe mais "Trocar motorista"
  (para trocar, **Sair** e entrar de novo).
- No cliente: **‹ Voltar** e **💬 Chat com a frota**. Na 1ª vez o motorista escolhe o motivo (padrão: Ausente, Avaria,
  Escadas — mude em **Motoristas e app → Motivos do chat**). **Voltar não encerra**; ao entrar de novo continua a conversa.
  Quem encerra é a frota (Ao vivo). Depois de encerrado, um novo chat pede o motivo de novo.
- Ao vivo: caixa **💬 Chat com motorista** (abertos, com mensagens novas primeiro). Clicar no cliente (ou "Abrir chat")
  mostra **tudo do cliente**: endereço, NF, palavra-chave, peças, retirada, observações do Mapa, o registro da rua e o chat.
- A conversa fica **gravada na carga, por cliente** (aparece no detalhe da carga em "Conversas com o motorista") e
  abrir/encerrar entra no histórico.

# v5.6 — CHAT ESTÁVEL, PRAZO DO ATENDIMENTO, MOTORISTA SÓ PELO PQI

- Chat do app não redesenha mais a tela: o que o motorista está escrevendo não some (só as mensagens atualizam).
- Motivo do chat em destaque (grande, negrito) no app e no Ao vivo.
- **Contagem regressiva** do atendimento: começa quando o motorista escolhe o motivo, aparece no app e no Ao vivo
  (amarelo nos últimos 3 min, vermelho quando estoura). Padrão 15 min — **Motoristas e app → Prazo do atendimento**.
- **Motoristas e app** em 3 abas: Motoristas · Motivos do chat com a frota · Prazo do atendimento.
- **Logou com a placa = está logado**: o app abre direto nas entregas do motorista ligado à placa em Motoristas e app
  (sem tela de escolha e sem "Não estou na lista"; o app não cadastra nome). Placa com mais de um motorista ativo:
  vale o cadastrado/alterado por último. Placa sem motorista: o app avisa para pedir ao PCL.
- **Excluir motorista** (nível Excluir na aba): o celular dele sai; só volta se a placa aparecer de novo num PDF importado.

# v5.7 — TUDO SE FALA: APP → RETORNO, AJUSTE NA CARGA, BUSCA NO AO VIVO

- **Ao vivo**: 🔓 *Liberar sem palavra* em cada pedido sem registro (o PCL libera antes do motorista pedir) e
  🔎 **busca** por carga, PV, cliente, código, NF, placa, motorista, palavra, bairro.
- **App**: pendentes em cima, já registrados embaixo; sem o número de sequência.
- **Motivos únicos**: a lista de Motoristas e app → Motivos vale para o chat E para Retorno/Parcial. Se o motorista
  já escolheu o motivo no chat daquele cliente, o Retorno/Parcial usa esse motivo sem perguntar.
- **Foto obrigatória** em todo Retorno e Parcial.
- **Retorno alimentado pelo app**: o retorno/parcial do motorista já aparece na aba Retorno como
  "📱 Motorista informou que voltou — aguardando chegar no CD". O CD toca **Recebida no CD** (ou lê o código)
  e informa estado e destino. Não dá para concluir o retorno com peça do app não recebida (receba ou remova ✕).
  Se o motorista corrigir o registro (ou o PCL reabrir), as peças ainda não recebidas saem do Retorno.
- **Ajuste na carga (PCL)**, no detalhe da carga, antes de validar:
  - ✂ **Retirar pedido**: o pedido sai da carga; as leituras dele saem do palete.
  - ＋ **Agrupar pedido**: se o PV está em outra carga ainda no CD, ele MUDA de carga (as duas travam);
    senão o PCL digita cliente e itens (SKU pela Carteira).
  - A etapa em andamento (montagem, ou conferência se a montagem já fechou) fica **TRAVADA** — sem leitura e sem
    fechar — até quem está na etapa tocar **✓ Ajuste feito no palete**. PDF novo da carga supera ajustes pendentes.

Testes: `python testes_qualidade.py` (74) · `python testes_motorista.py` (143)

# v5.8 — ABA RETORNO MAIS SIMPLES

- Lista: **🏁 Chegaram no CD** em destaque no topo (o motorista tocou "Cheguei no CD"), com quantas peças do app
  faltam receber. Embaixo, as outras cargas pendentes de retorno (coluna "No app": Na rua / Não saiu).
- Na carga: só aparece ação no que **voltou** (📱 peças do motorista → **Recebida no CD**). Pedido entregue fica limpo,
  com **✎ Editar**: aí aparecem "Não entregue — pedido todo", "Entrega parcial" e "Voltou 1".
- Editar um pedido que o motorista deu **ENTREGUE** e registrar que voltou vira **pendência no PCL**
  ("ENTREGUE NO APP, MAS VOLTOU") para corrigir a baixa no ERP. O PCL resolve em PCL → Ajustes.
