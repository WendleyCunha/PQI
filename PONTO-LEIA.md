# PONTO — registro por digital (módulo do PQI)

Arquivos: `ponto.py` (rotas `/api/ponto/*`), `ponto.html` (tela, padrão visual da Qualidade),
entrada em `modulos.py`, 3 linhas em `main.py`, `cryptography` no `requirements.txt`,
pasta `kingponto-agente/` (programa do leitor) e `testes_ponto.py`.

## Abas

| Aba | Para quê | Nível mínimo |
|---|---|---|
| **Hoje** | quem está trabalhando, no intervalo, já saiu, ausente, atrasado; batidas em tempo real; terminais online | Visualizar |
| **Bater ponto** | tela do terminal: **nada para escolher** — a pessoa só encosta o dedo. Modo tela cheia | Adicionar |
| **Espelho** | batidas, previsto, trabalhado, saldo e ocorrências por dia; imprimir | Visualizar |
| **Tratamento** | ocorrências pendentes, ajustes com motivo, histórico, integridade das batidas | Visualizar (ajustar: Editar) |
| **Cadastros** | colaboradores (do Organograma), digitais, unidades, terminais, regras e feriados; teste sem leitor (só admin) | Editar (excluir: Excluir) |

## O terminal é o login

- Quem entra no PQI com a aba **Bater ponto** e abre essa tela transforma o aparelho (PC ou celular Android com o leitor)
  no terminal **daquele login**. Ninguém escolhe terminal e não existe segredo para digitar.
- O terminal nasce sozinho no primeiro uso (um por login) e aparece em Cadastros → Terminais.
- **Unidade automática**: 1º a do cadastro de ponto da pessoa do login (mesmo e-mail no Organograma);
  senão, a de quem bater o primeiro ponto ali. Corrigiu à mão em Cadastros → Terminais? Fica gravado para aquele login.
- Toda batida guarda o login e o terminal que a registraram. Perdeu o aparelho: **Bloquear** o terminal daquele login.

## Liberar o acesso (Segurança)

1. **Perfil Gerente** → sistema **Ponto = Editar**. Todas as abas seguem (Editar no sistema = Excluir nas abas).
   Nenhum outro perfil recebe o Ponto sozinho. O administrador sempre vê tudo.
2. **Alguém de alçada menor que precisa editar SÓ os Cadastros, por completo**
   (acesso por pessoa → Ponto):
   - sistema: **Visualizar**
   - aba **Cadastros: Excluir** (edição completa, inclusive excluir digital/terminal)
   - abas Hoje, Bater ponto, Espelho, Tratamento: **Sem acesso**
   O servidor confere aba por aba — testado em `testes_ponto.py`.

## Antes de ligar a digital (uma vez, no Render)

Crie a variável de ambiente **`PONTO_CHAVE`** (é a chave que cifra as digitais; fica fora do banco):

```cmd
python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Cole o resultado em Render → Environment → `PONTO_CHAVE`. **Guarde uma cópia em lugar seguro**:
se ela se perder, as digitais cadastradas não abrem mais (é só recadastrar).
Opcional: `PONTO_EMPREGADOR` = nome que sai no comprovante.

Sem a `PONTO_CHAVE`, o resto do Ponto funciona e a tela avisa que a digital está desligada.

## Regras que não se quebram

- **Batida imutável**: não existe rota para alterar ou apagar. Cada batida tem **NSR** (sequência sem buraco)
  e **selo encadeado** (hash). A aba Tratamento → Integridade mostra se alguém mexeu direto no banco.
- **Ajuste é registro novo** (incluir, desconsiderar, abonar, justificar) com motivo obrigatório e autor.
- **Nunca bloqueia batida por horário**; só sinaliza.
- **Hora oficial = servidor** (Brasília). A do computador fica só como evidência.
- **Organograma é o dono das pessoas**: o Ponto só lê. CPF, matrícula, unidade e jornada estruturada
  ficam no cadastro do ponto. A jornada vem **sugerida** do texto de horário do Organograma.
- **Digital**: só o template do leitor, cifrado (AES-256-GCM). Termo LGPD obrigatório antes da leitura.
- **Batida por digital precisa da assinatura do leitor** com a chave do terminal do login (gerada pelo servidor; desafio de uso único).

## Firestore (coleções novas, prefixo `pto_`)

`pto_marc` (batidas) · `pto_ajustes` · `pto_colab` · `pto_config` (`geral` e `contador`).
Nenhuma coleção existente é alterada. O Organograma é só lido.

## Testar

```cmd
python testes_ponto.py
```

56 verificações: apuração, terminal pelo login e unidade automática, NSR em paralelo, hash, assinatura, replay, ajuste, permissões por aba.

## Ainda não feito (próximas fases da especificação)

- Driver do **leitor real** (depende do modelo escolhido) — ver `kingponto-agente/leitores/MODELO_DRIVER.md`.
- **App Android** (abre o PQI e faz a ponte com o leitor USB-C/OTG) — para usar o celular como terminal.
- **AFD / AEJ** no layout oficial da Portaria 671 e assinatura CAdES (precisa do e-CNPJ).
- Fechamento mensal com trava, espelho assinado pelo colaborador, informe da folha.
- Férias, banco de horas com vencimento, portal do colaborador, batida offline, contingência com foto.
- Jornada que atravessa a meia-noite (turno noturno) e adicional noturno.
