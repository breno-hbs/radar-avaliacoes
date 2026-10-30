# DESCARTADO: não é a v1 oficial

Dados simulados. Projeto independente, sem vínculo com o iFood.

Esta pasta guarda a execução **DESCARTADA** da v1 (3 rodadas dos 20 de desenvolvimento,
2026-10-29T21:34:42-03:00) feita com uma redação que continha duas cláusulas depois removidas:

- na linha de alergia, a frase completa era: "alergia: reação alérgica relatada, ou presença
  relatada de um alérgeno que representa risco concreto ao cliente, por exemplo quando o cliente
  declara alergia, própria ou de quem vai consumir o pedido, e recebe o alérgeno; neste caso pode
  ser crítica mesmo sem reação." (o resto da linha, com as exclusões, era igual). A cláusula
  "de quem vai consumir o pedido" vem da decisão do caso crit-009 em `docs/CASOS_AMBIGUOS.md`, que
  não está no guia v1.1. Na v1 oficial a frase é: "reação alérgica relatada, ou presença de
  alérgeno que representa risco concreto ao cliente; neste caso pode ser crítica mesmo sem reação.";
- na linha do entregador, o acréscimo completo era: "Não entregar na porta (ou no ponto esperado)
  só é entregador se o texto indicar descumprimento do combinado ou comportamento inadequado; sem
  isso, não rotule entregador." O trecho "(ou no ponto esperado)" ampliava o guia (que fala só em
  entregar "na minha porta"). Na v1 oficial o acréscimo é o mesmo sem esse trecho.

**Isto NÃO é a v1 oficial e NÃO é resultado dela.** A v1 oficial é `prompts/classificador/v1.md`, com
o relatório em `ajuste/relatorios/v1.md`. Esta execução foi descartada antes de qualquer medição
oficial (nenhuma execução nos 15 críticos nem nos 50 gerais) e é mantida só como registro.

## Arquivos

- `v1.md` e `v1_rodada1..3.json`: relatório e saídas brutas da execução descartada. Cada um tem o
  aviso "DESCARTADO: não é a v1 oficial" no topo (nos JSON, na chave `aviso`; o resto do conteúdo não
  foi alterado). Resultado: 20 de 20, 220.290 tokens de entrada, 1.523 de saída, ~US$ 0,456.
- `v1-descartado.md`: o prompt usado nessa execução, **RECONSTRUÍDO** a partir do diff aprovado
  (`prompts/classificador/v0.md` mais as duas linhas descartadas). Não é o arquivo original byte a
  byte: o original não foi preservado. A reconstrução é consistente com os tokens registrados (todo
  item gastou 60 tokens de entrada a mais do que na v1 oficial), mas isso não foi verificado por
  contagem.
