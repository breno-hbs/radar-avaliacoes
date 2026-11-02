# Antes e depois (estimativa de cenário)

Dados simulados. Projeto independente, sem vínculo com o iFood. Este documento compara uma estimativa com uma medida; não prova ganho de tempo com clientes reais.

| | Tempo | Origem |
|---|---|---|
| Antes: ler as avaliações da semana (cerca de 20 a 25) e decidir o que fazer | 20 min (1.200 s) | Estimativa do autor, não medida |
| Depois: decidir os 3 cards do painel (medido até o 3º marcado como concluído) | 226 s (3 min 46 s) | Medido em 01/11/2026, 1 testadora |
| Meta do projeto | 120 s | Definida na especificação |

Diferença entre a estimativa e o tempo medido: 974 s a menos (cerca de 81%). A meta de 120 s não foi atingida.

## O que esta comparação NÃO mostra

- O "antes" é um palpite do autor, sem medida nem fonte. Mudando a estimativa, muda o ganho.
- O "depois" vem de 1 pessoa, no computador, com mouse, numa semana sintética fixa de 24 avaliações. Não é validação estatística.
- A testadora hesitou na rolagem do mouse (fator externo ao painel), marcou os 3 cards como concluídos, não preencheu a nota e, depois do 3º, não soube o que fazer. O tempo por card não foi anotado.
- Os dois tempos medem coisas diferentes: o "antes" inclui ler as avaliações e decidir; o "depois" só decide sobre cards já prontos. Na semana de teste, as 24 avaliações vêm rotuladas à mão e o ranking é calculado pelo código (config/ranking.json), sem chamar a API de classificação. Na operação real, a classificação é feita pela API; esse tempo e custo não entram neste comparativo.
- A testadora não foi avaliada como dona de restaurante real.

## Como refazer

Roteiro e registro em [`docs/TESTE_2_MINUTOS.md`](TESTE_2_MINUTOS.md). Um segundo teste, de preferência no celular e com mais de uma pessoa, é o próximo passo.
