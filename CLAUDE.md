# Radar de Avaliações

Assistente que transforma avaliações de restaurante em até 3 ações por semana.
Dados SIMULADOS. Projeto independente, sem vínculo com o iFood.

## Regras que não mudam
- Dados reais nunca. Tudo é sintético e declarado como tal.
- Não usar logotipo nem identidade visual oficial do iFood, nem apresentar o projeto
  como produto ou parceria dele. Menções factuais à plataforma são permitidas, com a
  identificação "Projeto independente, sem vínculo com o iFood".
- Alertas críticos podem ser enviados automaticamente ao dono, sempre identificados como
  "relato a verificar". Respostas públicas aos clientes NUNCA são enviadas automaticamente.
- Segredos (chaves, token do bot) só em variáveis de ambiente (.env). Nunca no código nem no Git.
- O token do painel é aleatório, guardado só como hash SHA-256 em tokens_painel,
  validade de 7 dias, e nunca aparece em logs.
- Efeito de uma ação é comparação simulada, não prova de causalidade.

## Stack
Python 3.13, FastAPI, SQLite, pytest. n8n e Telegram a partir da Fase 3.

## Fonte da verdade
docs/ESPECIFICACAO.md é a fonte da verdade.
- Nunca altere contratos ou requisitos para fazer código ou testes passarem.
- Se encontrar contradição ou divergência: pare, descreva o problema, apresente as
  alternativas e aguarde a decisão do Breno.

## Testes
Escreva o teste ANTES do código nestes pontos de risco:
- Duplicidade (mesmo id_externo não gera nova avaliação nem novo alerta)
- Autenticação (X-API-Key) e expiração do link do painel
- Persistência após reiniciar o servidor
- Classificação multirrótulo
- Detecção de criticidade, incluindo negações e ambiguidades
Rode os testes relevantes e informe o resultado real. Nunca diga que passaram sem executá-los.

## Dados de avaliação
- 20 de desenvolvimento: únicos usados para ajustar prompt, regras e few-shot.
- 50 gerais e 15 críticos: só medem. Nunca use esses exemplos para ajustar nada.
- Crítico = relato concreto de possível evento ou risco grave, sem exigir confirmação
  de causa. Negação ou dúvida sem indício concreto de evento ou risco grave não é crítica.
  A presença relatada de um alérgeno para um cliente alérgico pode ser crítica mesmo sem reação.
- Congele o prompt antes de avaliar e registre versão, modelo e resultado.
  Ajuste depois de ver o resultado torna a métrica exploratória.

## Fluxo de trabalho (como em equipe)
- Nunca commitar nem dar push direto no main. Um branch por tarefa
  (feat/..., fix/..., test/..., docs/..., chore/...) e um Pull Request por branch.
- Um commit por alteração lógica concluída, só depois da aprovação do Breno.
  Prefixos: feat, fix, test, docs, chore.
- O PR descreve o que mudou, por quê e como foi testado.
- Antes de abrir o PR, use o subagente revisor. Nunca faça merge sem aprovação do Breno.
- Explique decisões arquiteturais relevantes antes de implementá-las. Conceito novo para
  o Breno: explique o porquê na primeira vez que aparecer.
- Não crie arquivos fora do escopo pedido. Não instale dependências sem avisar.

## Comandos
- Ativar ambiente: .\.venv\Scripts\Activate.ps1
- Testes: pytest
- Servidor: uvicorn app.main:app --reload
