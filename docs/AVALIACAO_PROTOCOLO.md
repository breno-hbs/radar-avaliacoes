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
- saídas inválidas, **citadas na mesma frase dos falsos alertas**, com **uma só base**: "nas 3
  rodadas e nos 65 itens" (M de 195; inválida = previsão `None` em gerais ou críticos). Essa base
  vale na frase de cada rodada, nas médias, no cabeçalho e nos dados JSON, onde a contagem
  OFICIAL é o campo `saidas_invalidas_total` (as contagens brutas por rodada ou conjunto, se
  existirem, têm prefixo `auxiliar_`; `chamadas_planejadas` = 195 e `chamadas_realizadas` inclui
  retentativas); a contagem por rodada, se
  aparecer, vem separada e rotulada como detalhe.

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
  aborto. Caso misto: técnica, técnica, conteúdo → `None` (saída inválida), porque a última falha
  foi de conteúdo; conteúdo, conteúdo, técnica → aborto. O relatório lista o tipo de cada tentativa
  dos itens inválidos.
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
  opção aplica as mesmas travas de hashes, git e tag/arquivos congelados do `--executar` e recusa
  diário incompleto, abortado, com hashes ou SHA da tag diferentes dos registrados.

## 4. Travas antes da primeira chamada

1. Os hashes do prompt, do schema, dos parâmetros e da regra de retentativa, o modelo e as versões
   do SDK conferem com `prompts/classificador/v1.congelamento.md`; senão, o script sai sem usar a
   API.
2. Sem alterações não commitadas em `prompts/`, `app/`, `avaliacao/` e `ajuste/`; o relatório grava
   o `git rev-parse HEAD` da execução.
3. Sem `--executar`, o script só faz um dry-run (versão, hashes, número de chamadas), sem abrir os
   dados e sem chamar a API.
4. Com `--executar` **e** com `--so-relatorio`: a tag `classificador-v1-congelado` existe, o `HEAD` descende dela e
   `app/classificador.py`, `app/contrato.py` e `app/metricas.py` são idênticos aos da tag.
5. Só com `--executar` (nunca no dry-run nem no `--so-relatorio`): o SHA-256 do JSON canônico de `geral.json` e `critico.json` confere com o do
   registro (nunca no dry-run); os hashes vão para o diário e o relatório.
6. Só com `--executar`: os conjuntos são validados (50 e 15 itens, rótulos do contrato) e o cliente
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

Os comandos abaixo foram escritos para o **Windows PowerShell 5.1** (um comando por linha, sem
`&&`; o ambiente virtual ativado com `.\.venv\Scripts\Activate.ps1`). O `python -c` fica numa linha só,
com aspas duplas por fora e simples por dentro (testado no PowerShell 5.1).

Pré-requisitos, nesta ordem:
1. PR do congelamento mergeado; **executar a partir de `main` atualizada**, nunca da branch da
   feature (um squash faria a tag deixar de ser ancestral do `HEAD`):

   ```powershell
   git checkout main
   git pull
   ```

2. Antes de criar a tag, conferir os hashes dos dados localmente (só imprime hashes) e comparar com
   `dados_geral_sha256` e `dados_critico_sha256` do `v1.congelamento.md`:

   ```powershell
   python -c "from avaliacao.congelamento import hash_dados; print(hash_dados('data/geral.json')); print(hash_dados('data/critico.json'))"
   ```

3. Criar a tag no commit do merge e **enviá-la ao GitHub**. A tag marca o instante do congelamento;
   não se move (`git tag -f` é proibido) e, se um item congelado precisar mudar, vira a v2:

   ```powershell
   git tag classificador-v1-congelado
   git push origin classificador-v1-congelado
   ```

   O código confere a tag **local** (existe, `HEAD` descendente, arquivos congelados iguais); ele
   **não** confere que a tag foi enviada ao GitHub nem que se está em `main`. Isso é procedimento.
4. Árvore limpa (`git status`) e `ANTHROPIC_API_KEY` no `.env`.

O relatório grava o **SHA do commit da tag** e o **`HEAD` da execução**; o diário grava os dois no
início (`--so-relatorio` exige que o SHA da tag continue o mesmo).

```powershell
python -m avaliacao.rodar_oficial
python -m avaliacao.rodar_oficial --executar
python -m avaliacao.rodar_oficial --so-relatorio
```

A primeira linha é o dry-run (não abre dados nem chama a API). A segunda é a avaliação oficial (195
chamadas planejadas). A terceira regenera o relatório de um diário completo, sem chamar a API.
`--so-relatorio` aplica as mesmas travas de hashes, git e tag/arquivos congelados do `--executar`.
Para ver só a ajuda e os códigos de saída: `python -m avaliacao.rodar_oficial --help`.

Códigos de saída (também na ajuda do script, `--help`): 0 sucesso ou dry-run; 1 aborto durante a
execução (erro permanente, erro técnico esgotado, qualquer exceção) ou falha ao gerar o relatório;
2 congelamento diverge do código (também é o código de erro de argumentos); 3 alterações não
commitadas ou git indisponível; 4 avaliação já concluída (ou relatório presente, possivelmente
incompleto); 5 diário anterior sem `--motivo-tecnico`, motivo sem nada a arquivar, diário completo
(use `--so-relatorio`) ou falha ao arquivar; 6 conjunto de dados inválido; 7 tag, ancestralidade ou
arquivos congelados; 8 hash dos dados diverge do registro; 9 outra execução em andamento ou não foi
possível criar o lock; 10 cliente não pôde ser criado (chave ausente?); 11 `--so-relatorio` inválido
(diário ausente, incompleto, abortado, com hashes ou SHA da tag diferentes); 130 interrompido
(Ctrl+C).
