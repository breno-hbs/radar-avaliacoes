# radar-avaliacoes
Assistente que transforma avaliações de restaurante em até 3 ações por semana. Dados simulados. Projeto independente, sem vínculo com o iFood.

## Limitações

- As 85 avaliações são fictícias, criadas com apoio de IA. As classificações foram revisadas manualmente por uma única pessoa (rotulador único).
- Como as avaliações foram criadas com apoio de IA, a taxa de acerto pode sair inflada se uma IA parecida fizer a classificação. Essa limitação continua aberta.
- O caso `crit-007` influenciou uma regra de rotulação: a regra de acúmulo entre `alergia` e `saude`, na versão 1.1 do Guia de Rotulação (2026-10-28).
- A alteração ocorreu antes da execução do classificador e não foi motivada por previsões do modelo.
- Mesmo assim, o conjunto crítico não ficou totalmente independente da definição dos critérios.
- Uma avaliação futura mais rigorosa exigirá novos exemplos independentes, preparados após o congelamento das regras.
