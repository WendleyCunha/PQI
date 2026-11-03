# Como ligar um leitor de verdade

O agente não sabe falar com nenhum fabricante específico: ele chama um **driver**
(um arquivo nesta pasta) que tem sempre os mesmos 6 métodos. Para cada modelo de
leitor, escreve-se um arquivo novo (ex.: `idbio.py`) e coloca-se o nome dele em
`config.json` → `"leitor": "idbio"`.

| Método | O que faz |
|---|---|
| `abrir()` | conecta no leitor (USB) pelo SDK do fabricante |
| `conectado() -> bool` | o leitor está plugado e respondendo? |
| `capturar(espera_seg) -> (template, qualidade) \| None` | espera um dedo, extrai o **template** (nunca a imagem) |
| `esperar_soltar()` | espera a pessoa tirar o dedo (entre as 3 leituras do cadastro) |
| `comparar(a, b) -> int` | pontuação de semelhança entre dois templates |
| `unir(leituras) -> (template, qualidade)` | junta as 3 leituras do cadastro num template só |

O que decide o driver é o **leitor escolhido** (decisão em aberto na especificação, item 1):

- O SDK do fabricante precisa ser **gratuito** e ter versão para Windows.
- Se o SDK não fizer a comparação (`comparar`), dá para usar o **SourceAFIS**
  (código aberto, gratuito) com o template no padrão ISO 19794-2.
- `limiar` em `config.json` é a pontuação mínima para aceitar: calibre com o
  leitor real (Fase 0 da especificação: identificar em < 2 s com a base inteira).

Assim que o modelo for definido, o driver dele é o próximo arquivo a escrever.
