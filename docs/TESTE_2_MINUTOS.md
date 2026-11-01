# Teste dos 2 minutos (critério 8)

Dados simulados. Projeto independente, sem vínculo com o iFood.

Este roteiro segue a seção "Teste dos 2 minutos" de `docs/ESPECIFICACAO.md`, que é a fonte da verdade.
É um teste exploratório com 1 ou 2 pessoas, não uma validação estatística.

## Preparação (observador)

1. Gere a semana de teste num banco novo (o script recusa um arquivo que já existe):

   ```powershell
   .\.venv\Scripts\Activate.ps1
   $env:RADAR_URL_BASE = "http://localhost:8000"
   python scripts/semana_de_teste.py data\teste2min.db
   ```

2. Suba o servidor apontando para esse banco, em outro terminal:

   ```powershell
   $env:RADAR_DB = "data\teste2min.db"
   $env:RADAR_API_KEY = "chave-de-teste"
   $env:RADAR_URL_BASE = "http://localhost:8000"
   uvicorn app.main:app
   ```

   O servidor lê o `.env` do projeto, que pode ter o token real do Telegram. No PowerShell, `$env:X = ""`
   remove a variável em vez de deixá-la vazia, então não dá para bloquear o envio por aí. A proteção é outra: o
   banco de teste não tem nenhum alerta e o painel só lê. Durante o teste, **não** chame `POST /avaliacoes` nem
   `/resumo-semanal`.

3. O script imprime o link do painel na última linha. Cada execução gera um token novo, que vale 7 dias. O token
   não é gravado em arquivo nem em log: copie o link da tela. Perdeu o link? Rode o script de novo, com outro
   caminho de banco.
4. A semana é sempre a mesma: 24 avaliações e 3 cards (item faltando, qualidade/temperatura e atendimento).
   Só as datas mudam, porque ela é a última semana completa.

## Instrução ao testador

Leia em voz alta, sem acrescentar nada (a frase e a regra de não responder perguntas são do roteiro, não da
especificação):

> Você é dono de um restaurante. Decida o que fazer com cada problema.

Para cada card, registre uma ação **ou** diga em voz alta que não fará nada. O observador não explica a tela
nem responde perguntas sobre como usá-la.

## Roteiro

1. **Início:** o testador abre o link e a primeira tela termina de carregar. O observador dispara o cronômetro
   nesse momento.
2. **Decisão por card:** vale registrar uma ação ou dizer que não fará nada. O observador anota a decisão
   **verbal**, não o que o sistema gravou, porque a v1 não tem botão para dispensar uma recomendação.
3. **Fim:** a decisão do 3º cartão. O observador para o cronômetro e anota o tempo.
4. **Sucesso:** os 3 cards decididos em **120 segundos ou menos, sem ajuda do observador**.
5. Se o testador pedir ajuda, o observador anota o que foi pedido e a ajuda dada, e o teste conta como falha.
6. **Registre o tempo mesmo se o teste falhar** (desistência, estouro dos 120 s ou ajuda). Um tempo de falha
   também é dado.

O teste mede o tempo de decisão. Ele não afirma que todas as decisões foram persistidas no painel.

## Registro

Uma linha por testador. Preencher durante o teste.

| Testador | Tempo até a decisão do 3º cartão (s) | Sucesso (≤ 120 s, sem ajuda)? | Hesitações observadas | Onde errou | Pediu ajuda? (o quê) |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |
|  |  |  |  |  |  |

## Depois do teste

Os resultados entram no README com o número de testadores, os tempos reais e as ressalvas: poucos testadores,
semana sintética, teste exploratório, sem validação estatística.
