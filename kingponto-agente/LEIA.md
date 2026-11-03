# KingPonto Agente

Programa que roda no **computador da loja** onde o leitor de digital USB está ligado.
A tela **Ponto → Bater ponto** do PQI conversa com ele (só dentro do próprio computador).

## Instalar (uma vez por computador)

1. Instale o Python 3.11 (python.org → marque "Add to PATH").
2. Copie esta pasta `kingponto-agente` para o computador (ex.: `C:\kingponto-agente`).
3. No CMD:
   ```cmd
   cd C:\kingponto-agente
   pip install cryptography
   python agente.py
   ```
4. Ele pergunta só duas coisas:
   - **Endereço do PQI** → o mesmo que você abre no navegador (ex.: `https://pqi.onrender.com`).
   - **Driver do leitor** → `simulado` para testar sem leitor; o nome do driver do modelo comprado depois.
   Não há id de terminal nem segredo: **o terminal é o login**. Quem entrar no PQI neste
   computador e abrir **Ponto → Bater ponto** conecta o leitor ao terminal daquele login.
5. Deixe a janela aberta. Para iniciar junto com o Windows: crie um atalho para
   `python C:\kingponto-agente\agente.py` em `shell:startup`.

## Primeira vez no Chrome

Ao abrir **Bater ponto**, o Chrome pode perguntar se o site pode acessar **dispositivos da rede local**.
Clique em **Permitir** — é o agente, no próprio computador.

## Segurança

- Só escuta em `127.0.0.1` e só responde ao endereço do PQI configurado.
- A chave do terminal vem do PQI pelo login (ninguém digita) e fica só na memória.
- As digitais chegam **cifradas** e ficam só na memória; cada leitura é **assinada** e o desafio vale uma vez só.
- Perdeu o aparelho? No PQI, Cadastros → Terminais → **Bloquear** o terminal daquele login.
