# Protocolo da avaliação oficial do classificador (v1)

Dados simulados. Projeto independente, sem vínculo com o iFood.

Este protocolo foi escrito **antes de qualquer número** da avaliação oficial. Ele vale para a v1
congelada (`prompts/classificador/v1.congelamento.md`). Fonte dos requisitos:
`docs/ESPECIFICACAO.md`, seção "Avaliação, critérios de aceitação e cronograma".

## 1. Dados e rodadas

- Conjuntos: **50 avaliações gerais** (`data/geral.json`) e **15 críticas** (`data/critico.json`).
  Só `avaliacao/rodar_oficial.py` pode ler esses arquivos.
- Uma **avaliação oficial = 3 rodadas completas** (195 chamadas no mínimo: 65 avaliações × 3) com
  a v1 congelada.
- Reportam-se **as três rodadas e a média** de cada métrica. **Nunca** se escolhe a melhor rodada
  e **nunca** se repete a avaliação para melhorar o resultado.

## 2. Métricas (especificação)

Nos 50 gerais:
- F1 micro, F1 macro e F1 por categoria (as 6 categorias);
- falsos alertas: avaliação **não crítica marcada como crítica** (conta 1 por avaliação);
- saídas inválidas, **citadas na mesma frase dos falsos alertas**.

Nos 15 críticos:
- recall geral como contagem (por exemplo, 14 de 15): a avaliação conta como detectada com
  **qualquer** marcação crítica;
- recall por tipo (`saude`, `alergia`, `cobranca_indevida`): alergia prevista como
  `cobranca_indevida` **não** é acerto de alergia;
- falsos positivos por marcação, reportados junto ao recall por tipo.

Em ambos: tokens e custo estimado (com a fonte do preço), e a **variação entre as rodadas, item a
item**. Média de métricas indefinidas (`None`) considera só as rodadas em que ela é definida, e o
relatório diz quantas entraram.

## 3. Execução atômica e aborto

- A execução é **atômica**. Erro permanente da API (4xx de configuração), erro técnico que esgota
  as 3 tentativas (timeout, falha de conexão, 408, 409, 429, 5xx), Ctrl+C ou qualquer exceção
  **abortam**. Nenhum resultado parcial é reportado nem exibido, e **nada vira saída inválida por
  causa técnica**: saída inválida é só falha de conteúdo da resposta (JSON inválido, fora do
  contrato, `max_tokens`, `refusal`, sem bloco de texto).
- Antes de abortar por erro técnico, cada chamada é tentada até 3 vezes, com espera fixa de 2 s
  antes da 2ª e 5 s antes da 3ª. A **última** falha decide: de conteúdo → saída inválida; técnica →
  aborto.
- O script grava cada item no diário (`avaliacao/resultados/v1/diario.jsonl`) assim que termina,
  para que Ctrl+C não perca chamadas pagas. O **relatório e as métricas só existem** se as 3 rodadas
  terminarem.
- **O diário parcial não é aberto durante a execução nem depois de um aborto, antes de refazer.**
- Os registros parciais são **arquivados como log de execução abortada** em
  `avaliacao/abortadas/` (não se apaga nada) e a avaliação é **refeita do zero**. O arquivamento só
  acontece depois que tag, dados, conjuntos e cliente foram validados.
- **Só se refaz por causa técnica, nunca por causa de resultado.** O motivo técnico é obrigatório
  (`--motivo-tecnico`), fica gravado no log arquivado e é citado no relatório final, que informa
  também o número de execuções abortadas anteriores (0 se não houver).
- **Causa técnica** é somente uma destas: (a) erro permanente da API de configuração (4xx: chave
  inválida, modelo inexistente, pedido malformado, saldo); (b) falha de rede ou timeout esgotado
  (inclui 408, 409, 429 e 5xx esgotados); (c) interrupção do processo (Ctrl+C, queda de energia,
  encerramento do sistema); (d) falha ao gravar (disco cheio, permissão, arquivo bloqueado).
  **NUNCA vale o conteúdo ou o valor de um resultado**: nota baixa, erro de classificação, recall
  ou F1 ruins, uma categoria que "foi mal", ou qualquer coisa que se tenha visto nos dados parciais.
- Uma avaliação concluída nunca é sobrescrita: o script se recusa se já existir relatório.
- Se as 3 rodadas terminam e só a geração do relatório falha, o diário completo é preservado e o
  relatório é regenerado com `--so-relatorio`, **sem chamar a API** e sem refazer nada. Essa
  opção recusa diário incompleto, abortado ou com hashes diferentes dos registrados.

## 4. Travas antes da primeira chamada

1. Os hashes do prompt, do schema, dos parâmetros e da regra de retentativa, o modelo e as versões
   do SDK conferem com `prompts/classificador/v1.congelamento.md`; senão, o script sai sem usar a
   API.
2. Sem alterações não commitadas em `prompts/`, `app/`, `avaliacao/` e `ajuste/`; o relatório grava
   o `git rev-parse HEAD` da execução.
3. Sem `--executar`, o script só faz um dry-run (versão, hashes, número de chamadas), sem abrir os
   dados e sem chamar a API.
4. Com `--executar`: a tag `classificador-v1-congelado` existe, o `HEAD` descende dela e
   `app/classificador.py`, `app/contrato.py` e `app/metricas.py` são idênticos aos da tag.
5. Com `--executar`: o SHA-256 do JSON canônico de `geral.json` e `critico.json` confere com o do
   registro (nunca no dry-run); os hashes vão para o diário e o relatório.
6. Com `--executar`: os conjuntos são validados (50 e 15 itens, rótulos do contrato) e o cliente
   é criado **antes** de qualquer arquivamento; um arquivo de trava (`avaliacao/resultados/v1.lock`,
   criação exclusiva) impede duas execuções ao mesmo tempo.
7. O prompt é lido uma só vez: o texto conferido por hash é o mesmo enviado à API.

## 5. Mudança depois de ver resultado

Qualquer ajuste do prompt (ou de qualquer item congelado) depois de ver o resultado vira uma
**nova versão (v2)**, com as métricas dela declaradas **EXPLORATÓRIAS** (especificação, seção
"Congelamento") ou um novo conjunto de avaliação. O **resultado oficial da v1 continua publicado**.

## 6. Ressalvas obrigatórias em qualquer publicação

- Amostra pequena: 50 gerais e 15 críticos avaliam o protótipo; não garantem desempenho em produção.
- Rotulador único.
- Uma avaliação futura deve usar exemplos escritos à mão ou de outra fonte (especificação).
- As 85 avaliações são fictícias, criadas com apoio de IA; se a mesma IA, ou uma parecida, gera os
  dados e classifica, a taxa de acerto pode sair **inflada**.
- Os 15 críticos são uma amostra enriquecida, não a frequência real.
- O conjunto crítico não é totalmente independente da definição dos critérios: o `crit-007`
  motivou a regra de acúmulo do guia v1.1 (antes de qualquer execução do classificador) e o
  `crit-009` expôs quem escreveu o prompt (a cláusula derivada dele foi removida antes de qualquer
  medição oficial).
- As 85 avaliações **não comprovam** desempenho com clientes reais.
- "Nunca chegou" é classificado como `atraso` (limitação da v1).
- Alergia de terceiro fica por interpretação do modelo (o prompt não tem cláusula para isso).
- As **saídas inválidas** são sempre citadas na **mesma frase** dos falsos alertas.
- Os testes de usabilidade com 1 ou 2 pessoas são exploratórios, não validação estatística.

## 7. Como executar (quando chegar a hora)

Pré-requisitos: PR do congelamento mergeado, tag `classificador-v1-congelado` no commit do merge,
árvore limpa e `ANTHROPIC_API_KEY` no `.env`.

```bash
python -m avaliacao.rodar_oficial               # dry-run: não abre dados nem chama a API
python -m avaliacao.rodar_oficial --executar    # avaliação oficial (195 chamadas)
```

Códigos de saída: 0 sucesso ou dry-run; 1 aborto durante a execução (erro permanente ou técnico
esgotado, qualquer exceção); 2 congelamento diverge do código (também é o
código de erro de argumentos); 3 alterações não commitadas; 4 avaliação já concluída; 5 diário
anterior sem `--motivo-tecnico`, motivo sem nada a arquivar ou diário completo que deve usar
`--so-relatorio`; 6 conjunto de dados inválido; 7 tag, ancestralidade ou arquivos congelados; 8 hash
dos dados diverge do registro; 9 outra execução em andamento (trava); 10 cliente não pôde ser
criado (chave ausente?); 11 `--so-relatorio` inválido (diário ausente, incompleto, abortado ou com
hashes diferentes); 130 interrompido (Ctrl+C).
