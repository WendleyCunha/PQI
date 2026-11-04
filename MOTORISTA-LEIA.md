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
