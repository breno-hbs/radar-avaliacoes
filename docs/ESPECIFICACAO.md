# Especificação técnica v1 — Radar de Avaliações

Oct 28, 2026 · @Breno

## Promessa e limites

**A v1 demonstra que é possível detectar reclamações, priorizar os casos críticos e transformar os demais problemas em ações acompanháveis para um restaurante pequeno, tudo sobre dados simulados.** "Radar de Avaliações" é o nome de trabalho.

**Perfil do usuário:** dono de um restaurante comum em São Paulo, uma loja só, sem tempo para ler centenas de comentários.

**Limites que valem em código, README, vídeo e posts:**

- As avaliações são **sintéticas**, geradas por IA com problemas controlados. Nada vem do iFood.
- **Não há integração** com sistemas do iFood. As ações do painel são registros internos e nenhuma resposta é publicada em lugar algum.
- **Identidade:** o projeto não usa logotipo nem identidade visual oficial do iFood e não é apresentado como produto ou parceria dele. Menções factuais à plataforma são permitidas, sempre com a identificação "Projeto independente, sem vínculo com o iFood".
- O "antes e depois" entre semanas prova o **mecanismo de medição**, não o efeito real de uma ação. Não prova causalidade.
- Economia de horas é **estimativa de cenário**.
- Diagnósticos são **hipóteses** com evidência, nunca certezas: a IA não conhece a operação do restaurante.
- Alertas críticos são **relatos a verificar**, não fatos comprovados.

## Arquitetura

&#91;embedded content: arquitetura da v1 · 5 componentes, 4 conexões e 1 alerta direto\]

O simulador envia avaliações ao FastAPI. O n8n pede o resumo toda segunda e o entrega pelo Telegram. O alerta crítico sai direto do FastAPI, em vermelho. O dono abre o painel pelo link da mensagem, e o painel só conversa com o FastAPI.

## Categorias e criticidade

**Cada avaliação recebe zero ou mais categorias e, separadamente, zero ou mais marcações críticas.** A criticidade é independente das categorias operacionais. Uma reação alérgica relatada recebe `alergia` como marcação crítica, conforme o Guia de Rotulação v1.1. A marcação `saude` só é acrescentada quando o mesmo texto relata outro evento de saúde sem natureza alérgica. Categorias operacionais são atribuídas somente quando o texto relata problemas correspondentes. As regras de rotulação estão em `docs/GUIA_ROTULACAO.md`, versão 1.1. A classificação considera o conteúdo da avaliação, independentemente da nota atribuída. Avaliações sem relato de problema recebem zero categorias. Avaliações com nota alta que mencionem explicitamente um problema recebem os rótulos correspondentes.

| Categoria | O que cobre | Ação recomendada no card | O restaurante controla? |
| --- | --- | --- | --- |
| `atraso` | Demora no preparo ou na entrega | Investigar o gargalo nos horários das reclamações, sem presumir a causa | Parcialmente |
| `item_faltando` | Item ausente ou errado | Criar checklist de conferência antes de fechar a sacola | Sim |
| `qualidade_temperatura` | Comida fria, sabor, ingrediente, preparo, porção e apresentação | Revisar o preparo ou a conservação do prato citado | Em parte (temperatura também depende do transporte) |
| `embalagem` | Vazamento, embalagem amassada ou inadequada | Testar melhoria da embalagem do item citado | Sim |
| `atendimento` | Trato do restaurante com o cliente | Orientar a equipe responsável | Sim |
| `entregador` | Problema com o entregador | Classificar como problema de entrega e registrar ocorrência; sem ação interna | Não |

**Marcações críticas:** `saude` (intoxicação, mal-estar, objeto estranho), `alergia` e `cobranca_indevida`. Todas são tratadas como **relato a verificar**.

**Fora das marcações:** ofensa e discriminação. O blog de parceiros do iFood informa que comentários ofensivos são descartados antes de chegar à loja ([fonte](https://blog-parceiros.ifood.com.br/avaliacoes-e-moderacoes/)), então o dono provavelmente nunca os veria.

**Decisão em aberto para depois da avaliação:** `qualidade_temperatura` fica junta na v1. Se a matriz de erros da Fase 1 mostrar confusão com `atraso` e `embalagem`, ou se o dono precisar de ações diferentes, a categoria é dividida.

**Decisão tomada (30/10/2026), Fase 2:** uma avaliação com várias marcações críticas gera **um único alerta**, que lista todos os tipos identificados. O critério: a unidade do alerta é o que o dono precisa verificar, e ele verifica uma avaliação, não um tipo de marcação.

## Contratos e dados

**O FastAPI é o único dono das regras e dos dados.** O n8n, o simulador e o painel só chamam as rotas abaixo, e o Telegram só recebe mensagens.

| Rota | Quem chama | Autenticação | O que faz |
| --- | --- | --- | --- |
| `POST /avaliacoes` | Simulador | Cabeçalho `X-API-Key` | Recebe `id_externo`, `texto`, `nota`, `recebida_em`. Valida, ignora duplicata, classifica e, se houver marcação crítica, cria um único alerta com todos os tipos identificados |
| `GET /resumo-semanal` | n8n | `X-API-Key` | Devolve período, total, negativas, 3 principais problemas, críticos, ações pendentes e o link do painel |
| `GET /painel/{token}` | Dono (navegador) | Token com validade (tokens\_painel) | Serve a página mobile-first |
| `GET /api/painel/{token}/cards` | Painel | Token | Cards com problema, evidências e recomendação |
| `POST /api/painel/{token}/acoes` | Painel | Token | Registra uma ação |
| `PATCH /api/painel/{token}/acoes/{id}` | Painel | Token | Muda o estado da ação |
| `GET /api/painel/{token}/historico` | Painel | Token | Semanas anteriores |

**Regras de segurança:**

- O token é aleatório (secrets.token\_urlsafe(32)) e só o seu hash SHA-256 é guardado na tabela tokens\_painel, com escopo do painel, validade inicial de 7 dias e possibilidade de revogação. A URL não carrega dado sensível. Cada resumo semanal gera um link novo.
- O token na URL é uma credencial sensível: não aparece em logs, o acesso publicado usa HTTPS e as páginas enviam o cabeçalho `Referrer-Policy: no-referrer` para evitar vazamento pelo cabeçalho Referer.
- A chave `X-API-Key` e o token do bot ficam em variáveis de ambiente. Nunca entram no código nem no GitHub.
- Requisição sem chave, ou com link expirado, é recusada.

**Tabelas (SQLite):**

| Tabela | Campos principais |
| --- | --- |
| `avaliacoes` | `id`, `id_externo` (único), `texto`, `nota`, `recebida_em`, `prazo_resposta` (= recebida\_em + 5 dias), `categorias`, `criticidade`, `confianca` |
| `alertas` | `id`, `avaliacao_id`, `tipos`, `estado`, `tentativas`, `criado_em`, `enviado_em`; único por `avaliacao_id`. `tipos` é a lista de todas as marcações críticas da avaliação |
| `acoes` | `id`, `categoria`, `semana_inicio`, `estado`, `criada_em`, `concluida_em`, `nota` |
| tokens\_painel | id, token\_hash (único, SHA-256), escopo, criado\_em, expira\_em (= criado\_em + 7 dias), revogado\_em |

**Estados:** alerta → `pendente`, `enviado` ou `falhou`. Ação → `pendente`, `em_andamento` ou `concluida`.

**Ordem do alerta:** o FastAPI grava o alerta como `pendente` **antes** de chamar o Telegram, marca `enviado` ao confirmar e tenta de novo uma vez se falhar; se a nova tentativa também falhar, o estado fica `falhou`. O campo `tentativas` registra as tentativas de envio. A mensagem é única por avaliação e lista todos os tipos identificados, sempre como "relato a verificar". A avaliação e o alerta `pendente` são gravados na mesma transação: se um não for gravado, nenhum dos dois é, e o reenvio da avaliação volta a ser processado. O histórico semanal é calculado das tabelas, sem tabela própria.

**Limite da unicidade:** a restrição única do banco impede um segundo registro de alerta para a mesma avaliação, mas não garante que o Telegram receba uma só mensagem. Se a mensagem for entregue e a confirmação do Telegram se perder (por exemplo, por tempo esgotado), a nova tentativa a envia de novo. Essa entrega duplicada pode ocorrer excepcionalmente. A v1 não garante entrega exatamente uma vez.

**Prioridade:** não perder um alerta crítico. Um alerta `falhou` continua registrado no banco, e o caso também aparece no resumo semanal. Como garantir que um alerta `pendente` ou `falhou` seja entregue é a decisão em aberto abaixo.

**Decisão em aberto (Fase 2):** definir a recuperação de alertas `pendente` após o reinício do servidor e o tratamento dos alertas `falhou` (reenvio automático, reenvio manual ou apenas o registro). Registrar o alerta no banco e mostrá-lo no resumo semanal não equivale a garantir aviso imediato ao dono: um alerta `pendente` ou `falhou` pode ficar sem ser entregue até o próximo resumo. Definir também se o resumo semanal mostra o estado do alerta (`pendente`, `enviado` ou `falhou`), já que o contrato de `GET /resumo-semanal` não o inclui.

**Decisão em aberto (Fase 2):** definir como cada ação registrada será vinculada às avaliações usadas como evidência, considerando uma tabela `acao_avaliacoes` ou estrutura equivalente. Não é necessário implementar isso na Fase 1.

## Regras de negócio

**O dono vê no máximo 3 cards por semana, ordenados por impacto, e decide tudo em menos de 2 minutos.**

- **Pontuação de um problema:** menções × gravidade × tendência. A gravidade é um peso fixo por categoria, definido na Fase 2 e documentado. A tendência soma ponto quando o problema subiu em relação à semana anterior.
- **Conteúdo de um card:** o problema em uma linha, a quantidade e o período, até 3 trechos reais como evidência, a recomendação e o botão "registrar ação". O que não entra nos 3 cards fica um toque adiante, em "outros temas".
- **Linha de cobertura fixa:** total de avaliações lidas, cards, temas monitorados sem ação e avaliações que a IA não soube classificar (baixa confiança).
- **Sem presumir causa:** o texto diz "o padrão sugere" e recomenda investigar. Entregador nunca gera ação interna.
- **Alerta crítico:** sai na hora em que a avaliação entra no endpoint, com o texto "relato a verificar", em uma mensagem por avaliação que lista todos os tipos identificados. Os casos críticos também contam no resumo semanal. O alerta pode ser enviado automaticamente ao dono, sempre como relato a verificar. Respostas públicas aos clientes nunca são enviadas automaticamente.
- **Resumo semanal:** segunda-feira às 8h, no fuso America/Sao\_Paulo. Só é enviado se houver algo a decidir; um indicador "última leitura" no painel mostra que o sistema está vivo.
- **Medição do efeito:** compara a taxa de menções por 100 avaliações entre semanas, antes e depois de uma ação concluída. É uma comparação simulada e não prova causalidade.
- **Prazo de resposta:** cada avaliação guarda o prazo de 5 dias do iFood, para a versão futura de respostas em lote ([fonte](https://blog-parceiros.ifood.com.br/avaliacoes-e-moderacoes/)). Na v1, os rascunhos de resposta em lote são opcionais e o primeiro corte se houver atraso.

## Avaliação, critérios de aceitação e cronograma

**A v1 só está pronta quando a classificação é medida e os oito critérios abaixo são comprovados.**

**Conjuntos de dados (85 avaliações sintéticas, criadas com apoio de IA, com classificações revisadas manualmente por uma única pessoa e separadas entre si):**

- **20 avaliações de desenvolvimento**, usadas só para ajustar prompts, regras e exemplos few-shot. Nenhuma métrica final sai deste conjunto. Inclui alguns críticos e negativos difíceis, para ajustar o prompt nos dois casos.
- **50 avaliações gerais**, com casos de vários problemas por avaliação, avaliações positivas e 8 a 10 negativos difíceis (negações e ambiguidades). Não contêm casos críticos verdadeiros.
- **15 casos críticos separados**, só com relatos efetivamente críticos, cerca de 5 por marcação (saude, alergia, cobranca\_indevida), para medir os casos detectados e os não detectados.
- **Métricas:** F1 micro, F1 macro e F1 por categoria nos 50 gerais. Nos 15 críticos: recall geral como contagem (por exemplo, 14 de 15) e recall por tipo (saúde, alergia, cobrança indevida). Nos 50 gerais: falsos alertas, isto é, avaliações não críticas marcadas como críticas.
- **Contagem das marcações críticas:** uma marcação crítica correta conta como verdadeiro positivo daquele tipo, e uma marcação adicional incorreta conta como falso positivo daquele tipo. O recall geral mede quantas das 15 avaliações críticas foram identificadas como críticas, com qualquer marcação. "Falso alerta por avaliação" significa uma avaliação não crítica marcada como crítica. Exemplo: gabarito `["alergia"]` e previsão `["alergia", "saude"]` dão acerto de `alergia`, falso positivo de `saude`, avaliação crítica detectada e nenhum falso alerta por avaliação. Falsos positivos por marcação crítica são reportados junto ao recall por tipo.
- **Regra de criticidade:** uma avaliação é crítica quando relata um possível evento ou risco grave concreto, sem exigir confirmação de que o restaurante o causou. "Comi e passei mal depois" e "Sou alérgico a amendoim e meu pedido veio com amendoim" são críticos. "Não passei mal, mas achei o gosto estranho" não é. A regra vale para os três conjuntos. O Guia de Rotulação foi refinado durante a rotulação, na versão 1.1, em 2026-10-28 (regra de acúmulo entre `alergia` e `saude`, surgida ao rotular o `crit-007`), antes de qualquer execução do classificador. Um caso de alergia classificado como cobrança indevida não conta como acerto no recall por tipo.
- **Separação:** os três conjuntos não têm avaliações duplicadas nem paráfrases quase idênticas entre si. Isso evita vazamento de dados (data leakage): ajustar o classificador com os mesmos exemplos usados para medi-lo.
- **Diversidade obrigatória:** avaliações ambíguas, vários problemas na mesma avaliação, reclamações sem categoria aplicável e negações ("não tive nenhuma reação alérgica").
- **Congelamento:** os 65 exemplos de avaliação só medem. O prompt é congelado antes de rodá-los, e versão do prompt, modelo e resultado são registrados. Se o prompt for ajustado depois de ver o resultado, as novas métricas são declaradas exploratórias ou exigem um novo conjunto de avaliação.

**Ressalvas que vão para o README e para os posts:**

- 50 avaliações avaliam o protótipo; não garantem desempenho em produção.
- O conjunto crítico não é totalmente independente da definição dos critérios: o caso `crit-007` motivou a regra de acúmulo da versão 1.1 do guia (2026-10-28), antes de qualquer execução do classificador e sem relação com previsões do modelo. Essa limitação deve ser declarada na própria publicação que apresentar o recall dos 15 críticos, junto às ressalvas de amostra pequena e rotulador único.
- Os 15 casos críticos são uma amostra enriquecida, não representam a frequência real.
- Se a mesma IA, ou uma parecida, gera os dados e classifica, a taxa de acerto pode sair inflada. Na v1, as 85 avaliações foram criadas com apoio de IA e nenhuma foi escrita à mão; esse risco fica declarado como limitação aberta. Uma avaliação futura deve usar exemplos escritos à mão ou de outra fonte.
- O teste com 1 ou 2 pessoas é exploratório, não validação estatística de usabilidade.
- As 85 avaliações demonstram o processo de avaliação do classificador, não comprovam desempenho com clientes reais. Essa limitação aparece no README e na apresentação.

| # | Critério | Como comprovar | Fase |
| --- | --- | --- | --- |
| 1 | Classificação multirótulo | Conjuntos separados (20 de desenvolvimento, 50 gerais) e métricas reportadas sobre os 50 | 1 |
| 2 | Casos críticos | 15 casos críticos testados, com recall geral e por tipo; falsos alertas contados nos 50 gerais | 1 |
| 3 | Sem duplicidade | Reenviar a mesma avaliação não gera outra avaliação nem outro alerta | 2 |
| 4 | Segurança | Endpoint sem chave e link expirado são recusados | 2 |
| 5 | Alerta imediato | Tempo medido entre a entrada no endpoint e a confirmação do Telegram | 3 |
| 6 | Resumo semanal | Workflow do n8n executado manualmente, com histórico de execução | 3 |
| 7 | Painel com persistência e histórico | Registrar ação, reiniciar o servidor e recuperar; consultar semanas anteriores | 4 |
| 8 | Decisão em 2 minutos | Teste dos 2 minutos, com o procedimento descrito abaixo da tabela | 4 |

**Teste dos 2 minutos (critério 8):**

- **Cenário:** uma semana sintética fixa, com 3 cards, igual para todos os testadores (1 ou 2 pessoas).
- **Início:** o testador abre o link e a primeira tela termina de carregar.
- **Decisão por card:** registrar uma ação ou dizer em voz alta que não fará nada. O observador anota a decisão verbal, não o sistema, porque a v1 não tem botão para dispensar uma recomendação.
- **Fim:** a decisão do último card.
- **Sucesso:** os 3 cards decididos em até 120 segundos, sem ajuda do observador. O tempo real e as hesitações são registrados mesmo se o teste falhar.
- **O que mede:** o tempo de decisão. Não afirma que todas as decisões foram persistidas no painel. É um teste exploratório, não validação estatística.

**Cronograma de 3 semanas** (datas previstas, partindo de 29/10/2026; se o seu tempo de estudo for menor, as datas andam e o escopo não muda):

| Semana | Fases | Resultado verificável |
| --- | --- | --- |
| 1 (29/10 a 04/11) | 0 e 1: fundamentos, dataset sintético de 85 avaliações, rotulação, ajuste com o conjunto de desenvolvimento e primeira avaliação | Dataset de 85 avaliações rotulado (20 de desenvolvimento, 50 gerais e 15 críticas), prompt congelado e métricas iniciais |
| 2 (05/11 a 11/11) | 2 e 3: FastAPI, SQLite, endpoint seguro, alertas críticos, n8n | Alerta com tempo medido e workflow executado |
| 3 (12/11 a 18/11) | 4: painel, ações persistidas, histórico, teste de 2 minutos, demonstração | Vídeo de 2 minutos, link do app e README |

**Ordem de corte se atrasar:** primeiro os rascunhos de resposta em lote. Os testes de classificação e a persistência nunca saem.

## Cobertura dos requisitos da vaga

**Cada requisito da trilha AI Builder do iFood tem um lugar concreto na v1.**

| Requisito da trilha | Onde é praticado |
| --- | --- |
| Prompt engineering avançado | Classificação multirótulo e geração do diagnóstico, com prompts versionados e testados contra os conjuntos rotulados |
| Orquestração de agentes (Claude, GPT, Gemini) | Classificador, priorizador/diagnóstico e (opcional) redator de respostas como papéis separados; um deles pode ser trocado de modelo para comparar |
| Automações com n8n, Make ou Zapier | Workflow semanal no n8n: agenda, chama `GET /resumo-semanal`, envia pelo Telegram |
| Vibe coding (Cursor, v0, Bolt, Replit) | Painel mobile-first feito em uma dessas ferramentas, ligado à API |
| APIs sem backend profundo | FastAPI pequeno com 7 rotas e SQLite |
| Análise de dados com IA | Classificação, agregação por semana, tendência e métricas de avaliação |
| Redesenhar a função criando agentes | Documento "antes e depois" do trabalho de ler avaliações, com tempo manual estimado e tempo no painel |
| Aprender o Toqan (plataforma interna do iFood) | Não há acesso público encontrado; o conceito de agente (instruções, ferramentas e conhecimento) é praticado nos papéis acima |

Do portfólio esperado: **ferramenta publicada** (painel no ar), **dashboard funcional** (painel), **agente que resolve problema de negócio** (os papéis acima), **automação** (n8n) e **produto do zero em dias** (cronograma de 3 semanas). Economia de horas é estimativa de cenário e deve ser descrita assim.

## Fora da v1 e riscos

**Escopo congelado em 28/10/2026.** Ideia nova entra na lista "Depois do MVP", não na v1.

**Depois do MVP:**

- Respostas em lote com aprovação do dono, mostrando o prazo de 5 dias. Autonomia futura só com amostra mínima, gravidade e revisão humana; nunca para casos críticos.
- Integração real: o iFood tem um módulo de avaliações no portal de desenvolvedores ([página do módulo](https://developer.ifood.com.br/pt-BR/docs/guides/review/)). Não consegui abrir a documentação, e um relato aponta o endpoint `GET /merchants/{merchantId}/reviews`, não verificado por mim. A integração provavelmente exigiria acesso autorizado e um coletor periódico que encaminhasse novas avaliações ao `POST /avaliacoes`.
- WhatsApp no lugar do Telegram. A Cloud API tem número de teste gratuito, mas fora da janela de 24 horas exige templates aprovados ([guia da Cliengo](https://guiawabusiness.cliengo.com/whatsapp-cloud-api)); o tempo de aprovação não foi encontrado.
- Gráficos, filtros por status, perguntas em texto sobre os dados e comparação ajustada por volume de pedidos.

**Riscos:**

| Risco | Resposta |
| --- | --- |
| Aprender n8n, Telegram e a ferramenta de vibe coding do zero | Uma coisa nova por fase, e o n8n só na Semana 2 |
| Classificação inflada por dados gerados pela mesma IA | Risco declarado como limitação aberta no README. Conjuntos de desenvolvimento e de avaliação separados. Avaliação futura com exemplos escritos à mão ou de outra fonte |
| Muito escopo para 3 semanas | Ordem de corte definida; testes e persistência não saem |
| Tempo de estudo menor que o previsto | As datas andam, o escopo não; as horas por dia ainda precisam ser definidas |
| Vazar chave ou segredo no repositório público | `.env` fora do Git e uma busca no histórico antes de publicar |

## Como construir

**O desenvolvimento é feito no Claude Code, com uma arquitetura de agentes definida antes da primeira linha de código**, para que cada agente respeite os mesmos contratos desta especificação.

- **Repositório:** `radar-avaliacoes`, público só depois de uma busca por segredos no histórico (`git log --all -S` com os valores sensíveis) e com `.env` fora do Git.
- **`CLAUDE.md`:** contém esta especificação resumida, as seis categorias, as rotas, os estados e as regras de segurança.
- **Subagentes:** um para a API e o banco, um para o classificador e as métricas, um para o painel e um revisor que lê o código em contexto limpo contra a especificação.
- **Testes primeiro nos pontos de risco:** duplicidade, autenticação, expiração do link e persistência após reiniciar.
- **Modo aula e modo prova:** a cada fase, o porquê é explicado antes do código, e depois você explica o que foi construído com as suas palavras, sem consultar.
- **Documentação:** README com o que é simulado, as métricas medidas, as ressalvas e um vídeo de demonstração de 2 minutos.
