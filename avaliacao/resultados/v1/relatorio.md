# Avaliação oficial da v1 (prompt congelado). Dados simulados. Projeto independente, sem vínculo com o iFood.

## Metadados

- Versão do prompt: v1
- SHA-256 do prompt: e84b4b75d8313505a205153e6937d2920d9088a299bb505b469760236d9f9491
- SHA-256 do schema de saída: f6bfb7f8cf400f5ba4f787d802b398f192aebb8e74877b6320e20b7219f8259a
- SHA-256 dos parâmetros da chamada: 3ed4602dc12e7f5f2e769d8825cf83848ea3ef13c839a33e64ca925ab9b867dc
- SHA-256 da regra de retentativa: d06f01a0f85ca877f108e6d007d97c96643587a301bbb26879f097b0cd95827f
- SHA-256 dos dados gerais: c18bcc055ec08c14764012a9b1e488388332a3573632d5bc4521aab824805881
- SHA-256 dos dados críticos: 6e7859f1e4995d26af5be6e26237c962f142ee21c16d6484a5f78a70909a6329
- Modelo: claude-sonnet-5-5
- SDK: anthropic 2.3.0 (python-dotenv 1.2.4)
- Parâmetros (sem o schema): `{"model": "claude-sonnet-5-5", "max_tokens": 400, "thinking": {"type": "between_tools"}, "output_config": {"effort": "medium"}}`
- Sem temperature/top_p/top_k (rejeitados pelo modelo); a variação é medida repetindo as rodadas.
- Commit da tag classificador-v1-congelado: f84a722114356f96ce8a15efe093b941bb286bbd
- HEAD da execução: f84a722114356f96ce8a15efe093b941bb286bbd
- Modelos informados pela API: claude-sonnet-5-5
- Início: 2026-10-30T00:17:45-03:00
- Fim: 2026-10-30T00:21:25-03:00
- Chamadas planejadas: 195
- Chamadas realizadas: 195 (inclui retentativas)
- Tentativas extras: 0
- Saídas inválidas nas 3 rodadas e nos 65 itens: 0 de 195

## Execuções abortadas anteriores: 0

Nenhuma.

## Rodada 1

- Detalhe desta rodada: saídas inválidas: 0 de 65 itens.

### Rodada 1, avaliações gerais (50)

- F1 micro: 1,000 (precisão 1,000, recall 1,000)
- F1 macro: 1,000
- categorias_fora_do_macro: []

| Categoria | tp | fp | fn | Precisão | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| atraso | 11 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| item_faltando | 10 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| qualidade_temperatura | 9 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| embalagem | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| atendimento | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| entregador | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |

Uma saída inválida entra nas contas como previsão vazia.

Falsos alertas: 0 de 50 avaliações não críticas marcadas como críticas (saídas inválidas nas 3 rodadas e nos 65 itens: 0 de 195).

### Rodada 1, avaliações críticas (15)

- Recall geral: 15 de 15
- Recall por tipo (alergia classificada como cobrança indevida não conta como acerto em alergia):
  - saude: 5 de 5, falsos positivos da marcação: 0
  - alergia: 5 de 5, falsos positivos da marcação: 0
  - cobranca_indevida: 5 de 5, falsos positivos da marcação: 0

## Rodada 2

- Detalhe desta rodada: saídas inválidas: 0 de 65 itens.

### Rodada 2, avaliações gerais (50)

- F1 micro: 1,000 (precisão 1,000, recall 1,000)
- F1 macro: 1,000
- categorias_fora_do_macro: []

| Categoria | tp | fp | fn | Precisão | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| atraso | 11 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| item_faltando | 10 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| qualidade_temperatura | 9 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| embalagem | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| atendimento | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| entregador | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |

Uma saída inválida entra nas contas como previsão vazia.

Falsos alertas: 0 de 50 avaliações não críticas marcadas como críticas (saídas inválidas nas 3 rodadas e nos 65 itens: 0 de 195).

### Rodada 2, avaliações críticas (15)

- Recall geral: 15 de 15
- Recall por tipo (alergia classificada como cobrança indevida não conta como acerto em alergia):
  - saude: 5 de 5, falsos positivos da marcação: 0
  - alergia: 5 de 5, falsos positivos da marcação: 0
  - cobranca_indevida: 5 de 5, falsos positivos da marcação: 0

## Rodada 3

- Detalhe desta rodada: saídas inválidas: 0 de 65 itens.

### Rodada 3, avaliações gerais (50)

- F1 micro: 1,000 (precisão 1,000, recall 1,000)
- F1 macro: 1,000
- categorias_fora_do_macro: []

| Categoria | tp | fp | fn | Precisão | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| atraso | 11 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| item_faltando | 10 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| qualidade_temperatura | 9 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| embalagem | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| atendimento | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |
| entregador | 8 | 0 | 0 | 1,000 | 1,000 | 1,000 |

Uma saída inválida entra nas contas como previsão vazia.

Falsos alertas: 0 de 50 avaliações não críticas marcadas como críticas (saídas inválidas nas 3 rodadas e nos 65 itens: 0 de 195).

### Rodada 3, avaliações críticas (15)

- Recall geral: 15 de 15
- Recall por tipo (alergia classificada como cobrança indevida não conta como acerto em alergia):
  - saude: 5 de 5, falsos positivos da marcação: 0
  - alergia: 5 de 5, falsos positivos da marcação: 0
  - cobranca_indevida: 5 de 5, falsos positivos da marcação: 0

## Médias das três rodadas

Reportadas as três rodadas e a média; nenhuma rodada foi escolhida.

### Avaliações gerais

- F1 micro: média 1,000 (3 de 3 rodadas)
- F1 macro: média 1,000 (3 de 3 rodadas)

Média só dos valores definidos; a última coluna diz quantas rodadas entraram.

| Categoria | F1 médio | Rodadas com F1 definido |
| --- | --- | --- |
| atraso | 1,000 | 3 de 3 |
| item_faltando | 1,000 | 3 de 3 |
| qualidade_temperatura | 1,000 | 3 de 3 |
| embalagem | 1,000 | 3 de 3 |
| atendimento | 1,000 | 3 de 3 |
| entregador | 1,000 | 3 de 3 |

Falsos alertas: média 0,00 de 50 avaliações não críticas marcadas como críticas (saídas inválidas nas 3 rodadas e nos 65 itens: 0 de 195).

### Avaliações críticas

- Recall geral: média 15,00 de 15
- Recall por tipo (média de contagens):
  - saude: média 5,00 de 5, falsos positivos da marcação: média 0,00
  - alergia: média 5,00 de 5, falsos positivos da marcação: média 0,00
  - cobranca_indevida: média 5,00 de 5, falsos positivos da marcação: média 0,00

## Variação entre rodadas

Nenhuma previsão mudou entre as rodadas.

## Erros

Itens com previsão diferente do gabarito (categorias ou criticidade) ou saída inválida.

### Rodada 1, gerais

Nenhum erro.

### Rodada 1, críticos

- **crit-014** (tentativas: 1)
  - texto: "minha janta nunca apareceu aqui, mas deram baixa como entregue. depois cancelaram no chat dizendo que não ia cobrar e mesmo assim descontaram tudo do cartão"
  - gabarito: categorias ["atraso"], criticidade ["cobranca_indevida"]
  - previsão: categorias ["entregador", "atraso"], criticidade ["cobranca_indevida"]
  - modelos informados pela API: claude-sonnet-5-5
- **crit-015** (tentativas: 1)
  - texto: "veio sem a coca que fazia parte do combo. a loja reconheceu e disse que devolveria o valor da bebida até ontem, mas não caiu nada e continuo pagando por ela"
  - gabarito: categorias ["item_faltando"], criticidade ["cobranca_indevida"]
  - previsão: categorias ["item_faltando", "atendimento"], criticidade ["cobranca_indevida"]
  - modelos informados pela API: claude-sonnet-5-5

### Rodada 2, gerais

Nenhum erro.

### Rodada 2, críticos

- **crit-014** (tentativas: 1)
  - texto: "minha janta nunca apareceu aqui, mas deram baixa como entregue. depois cancelaram no chat dizendo que não ia cobrar e mesmo assim descontaram tudo do cartão"
  - gabarito: categorias ["atraso"], criticidade ["cobranca_indevida"]
  - previsão: categorias ["entregador", "atraso"], criticidade ["cobranca_indevida"]
  - modelos informados pela API: claude-sonnet-5-5
- **crit-015** (tentativas: 1)
  - texto: "veio sem a coca que fazia parte do combo. a loja reconheceu e disse que devolveria o valor da bebida até ontem, mas não caiu nada e continuo pagando por ela"
  - gabarito: categorias ["item_faltando"], criticidade ["cobranca_indevida"]
  - previsão: categorias ["item_faltando", "atendimento"], criticidade ["cobranca_indevida"]
  - modelos informados pela API: claude-sonnet-5-5

### Rodada 3, gerais

Nenhum erro.

### Rodada 3, críticos

- **crit-014** (tentativas: 1)
  - texto: "minha janta nunca apareceu aqui, mas deram baixa como entregue. depois cancelaram no chat dizendo que não ia cobrar e mesmo assim descontaram tudo do cartão"
  - gabarito: categorias ["atraso"], criticidade ["cobranca_indevida"]
  - previsão: categorias ["entregador", "atraso"], criticidade ["cobranca_indevida"]
  - modelos informados pela API: claude-sonnet-5-5
- **crit-015** (tentativas: 1)
  - texto: "veio sem a coca que fazia parte do combo. a loja reconheceu e disse que devolveria o valor da bebida até ontem, mas não caiu nada e continuo pagando por ela"
  - gabarito: categorias ["item_faltando"], criticidade ["cobranca_indevida"]
  - previsão: categorias ["item_faltando", "atendimento"], criticidade ["cobranca_indevida"]
  - modelos informados pela API: claude-sonnet-5-5

## Tokens e custo estimado

- Respostas com uso registrado: 195 (entrada) e 195 (saída), de 195
- Tokens de entrada: 706527
- Tokens de saída: 4871
- Custo estimado: US$ 1,4618 (entrada a US$ 2 / MTok, saída a US$ 10 / MTok)
- Fonte do preço: https://platform.claude.com/docs/en/about-claude/pricing (preço consultado em 2026-10-29)
- É estimativa: tentativas que terminaram em exceção não têm uso registrado. O custo real está no Console.

## Ressalvas obrigatórias

1. Amostra pequena: 50 gerais e 15 críticos. Os números avaliam o protótipo; não garantem desempenho em produção e carregam muita incerteza.
2. Rotulador único: as classificações de referência foram revisadas manualmente por uma só pessoa; não há medida de concordância entre rotuladores.
3. As 85 avaliações fictícias foram criadas com apoio de IA e nenhuma foi escrita à mão. Se a mesma IA, ou uma parecida, gera os dados e classifica, há risco de acerto inflado; isso fica declarado como limitação aberta.
4. Amostra crítica enriquecida: os 15 casos críticos foram escolhidos para medir detecção e a amostra não representa a frequência real de relatos críticos.
5. O conjunto crítico não é totalmente independente da definição dos critérios: o caso crit-007 motivou a regra de acúmulo da versão 1.1 do guia (2026-10-28), antes de qualquer execução do classificador. Há também exposição ao crit-009: a primeira redação do prompt trazia uma cláusula derivada desse caso, removida antes de qualquer medição oficial.
6. As 85 avaliações demonstram o processo de avaliação do classificador e não comprovam desempenho com clientes reais.
7. Convenção de rotulação: o pedido que "nunca chegou" é classificado como atraso.
8. Alergia de terceiro (pedido de outra pessoa que é alérgica) fica por interpretação do modelo: o prompt não tem cláusula sobre isso.
9. Saídas inválidas devem ser citadas na mesma frase dos falsos alertas: uma saída inválida vale como previsão vazia e pode esconder um falso alerta.
