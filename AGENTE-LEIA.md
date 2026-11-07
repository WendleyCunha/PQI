# Agente IA do PQI (v6.0)

Botão **Agente** no topo do Painel (ao lado de Expandir e Tema). Abre um popup de conversa:
a pessoa pergunta e recebe o **passo a passo oficial**, com a fonte (artigo, versão, data e quem atualizou) e 👍 / 👎.

## Quem faz o quê
- **Qualquer pessoa logada**: pergunta e avalia a resposta.
- **Administrador e perfil "Gerente"**: veem a ⚙️ dentro do popup → Artigos, Pendências, Histórico e Painel.
  Para incluir outros perfis, use a variável `AGENTE_PERFIS_ALIMENTAM` no Render (ex.: `Gerente, Gestor`).

## Como o agente "aprende"
1. Pergunta sem resposta na base, ou resposta marcada com 👎, vira **pendência**.
2. Quem alimenta responde a pendência → vira **artigo** (ou nova versão do artigo).
3. Na hora, o agente passa a responder com o conteúdo novo.

### Atribuir a uma pessoa do Organograma
Na pendência, **👤 Atribuir a alguém** lista as pessoas do Organograma (por setor). A pessoa precisa ter, no
Organograma, o **mesmo e-mail do login do PQI** — quem não tem aparece desabilitado ("sem login no PQI" /
"sem e-mail no Organograma"). Ela vê um 📥 no agente e o número no botão, escreve a resposta e envia;
quem alimenta **revisa e publica** (ou **devolve** com um recado). O histórico registra quem escreveu.

Nada do que as pessoas escrevem entra na base sozinho: só o que passa pela ⚙️.
Toda alteração fica no **Histórico**, e cada artigo guarda as últimas 20 versões (botão "Voltar a esta").

## Custo
- **Padrão: sem IA externa (custo zero).** O agente procura na base e devolve o passo a passo do artigo certo.
- **Opcional:** para a IA escrever a resposta (sempre usando só os artigos), configure no Render:
  - `AGENTE_IA=groq` + `GROQ_API_KEY` (plano gratuito do Groq), ou
  - `AGENTE_IA=anthropic` (usa a mesma `ANTHROPIC_API_KEY` do resumo do Diagnóstico — é pago).
  Se a IA falhar ou estiver fora do ar, o agente volta a responder pela base.

## Arquivos
- `agente.py` — rotas `/api/agente/*` e regras.
- `pqi-agente.js` — botão e popup (carregado pelo `index.html` depois do login).
- `testes_agente.py` — `python testes_agente.py`.
- Firestore: `agente_artigos`, `agente_pendencias`, `agente_perguntas`, `agente_historico`.
