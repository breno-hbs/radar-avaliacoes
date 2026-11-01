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

O simulador envia avaliações ao FastAPI. O n8n pede o resumo toda segunda e o entrega pelo Telegram. O alerta crítico sai direto do FastAPI, em vermelho. Um segundo workflow do n8n, de hora em hora, chama `POST /alertas/reenviar` para reenviar os alertas que não saíram. O dono abre o painel pelo link da mensagem do resumo semanal, e o painel só conversa com o FastAPI.

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

**Decisão tomada (30/10/2026):** `qualidade_temperatura` continua junta na v1. Na avaliação oficial, os 9 itens da categoria nos 50 gerais não tiveram erros nas 3 rodadas, mas a amostra é pequena. A categoria só é dividida se o dono precisar de ações diferentes ou se uma avaliação futura mostrar confusão com `atraso` e `embalagem`.

**Decisão tomada (30/10/2026), Fase 2:** uma avaliação com várias marcações críticas gera **um único alerta**, que lista todos os tipos identificados. O critério: a unidade do alerta é o que o dono precisa verificar, e ele verifica uma avaliação, não um tipo de marcação.

## Contratos e dados

**O FastAPI é o único dono das regras e dos dados.** O n8n, o simulador e o painel só chamam as rotas abaixo, e o Telegram só recebe mensagens.

| Rota | Quem chama | Autenticação | O que faz |
| --- | --- | --- | --- |
| `POST /avaliacoes` | Simulador | Cabeçalho `X-API-Key` | Recebe `id_externo`, `texto`, `nota`, `recebida_em` (com fuso). Valida, ignora duplicata, classifica e, se houver marcação crítica, cria um único alerta com todos os tipos identificados |
| `GET /resumo-semanal` | n8n | `X-API-Key` | Devolve período, total, negativas, 3 principais problemas, críticos (com o estado do alerta de cada um), as avaliações `invalida` da semana a verificar manualmente, os alertas `falhou` ainda não reconhecidos, de qualquer semana, ações pendentes e o link do painel |
| `POST /alertas/reenviar` | n8n | Cabeçalho `X-API-Key` | Tenta uma vez cada alerta `pendente` ou `falhou` elegível (menos de 5 tentativas e criado há mais de 5 minutos) e devolve 200 com `elegiveis`, `enviados`, `falharam` e `configurado` |
| `GET /painel/{token}` | Dono (navegador) | Token com validade (tokens\_painel) | Serve a página mobile-first |
| `GET /api/painel/{token}/cards` | Painel | Token | Cards com problema, evidências e recomendação |
| `POST /api/painel/{token}/acoes` | Painel | Token | Registra uma ação com `categoria`, `semana_inicio` e de 1 a 3 evidências (só `avaliacao_id` e `posicao`); o servidor confere as evidências e deriva o trecho exibido do texto da avaliação |
| `PATCH /api/painel/{token}/acoes/{id}` | Painel | Token | Muda o estado e/ou a nota da ação |
| `GET /api/painel/{token}/historico` | Painel | Token | Semanas com ações registradas, da mais recente à mais antiga (inclui a semana mais recente), com as ações, as evidências (trecho exibido e posição) e o texto original das avaliações |

**Regras de segurança:**

- O token é aleatório (secrets.token\_urlsafe(32)) e só o seu hash SHA-256 é guardado na tabela tokens\_painel, com escopo do painel, validade inicial de 7 dias e possibilidade de revogação. A URL não carrega dado sensível. Cada resumo semanal gera um link novo.
- O token na URL é uma credencial sensível: não aparece em logs, o acesso publicado usa HTTPS e as páginas enviam o cabeçalho `Referrer-Policy: no-referrer` para evitar vazamento pelo cabeçalho Referer.
- A chave `X-API-Key` e o token do bot ficam em variáveis de ambiente. Nunca entram no código nem no GitHub.
- Requisição sem chave, ou com link expirado, é recusada. A chave `X-API-Key` é lida de uma variável de ambiente e comparada em tempo constante; requisição sem chave ou com chave errada recebe 401.

**Decisão tomada (31/10/2026), Fase 2:** validação do token do painel (`app/tokens.py`). Regras:

- O token é válido se o hash existe, `revogado_em` é nulo e o instante atual é menor que `expira_em`. No instante exato de `expira_em` o link já é inválido. Um token fora do formato `^[A-Za-z0-9_-]{43}$` é recusado antes de consultar o banco, e a busca é sempre pelo hash.
- Token inexistente, expirado, revogado ou malformado: todas as rotas com `{token}` respondem 404 com o mesmo corpo genérico, "link inválido ou expirado". A resposta não revela o motivo.
- A validação confere o escopo esperado (`painel` nas rotas do painel). Um token de outro escopo responde o mesmo 404.
- O log de acesso do uvicorn registra o caminho completo. Um filtro de log troca o trecho do token por `***` em `/painel/<token>` e `/api/painel/<token>/...`, no log de acesso e nos loggers do app. Se o filtro deixar de valer com outra configuração de log, a alternativa é rodar o uvicorn com `--no-access-log`.

**Tabelas (SQLite):**

| Tabela | Campos principais |
| --- | --- |
| `avaliacoes` | `id`, `id_externo` (único), `texto`, `nota`, `recebida_em`, `prazo_resposta` (= recebida\_em + 5 dias), `categorias`, `criticidade`, `status_classificacao` |
| `alertas` | `id`, `avaliacao_id`, `tipos`, `estado`, `tentativas`, `criado_em`, `enviado_em`, `tardio` (inteiro 0 ou 1, padrão 0), `reconhecido_em` (instante UTC, nulo por padrão); único por `avaliacao_id`. `tipos` é a lista de todas as marcações críticas da avaliação |
| `acoes` | `id`, `categoria`, `semana_inicio`, `estado`, `criada_em`, `concluida_em`, `nota` |
| `acao_avaliacoes` | `acao_id` (referência a `acoes`), `avaliacao_id` (referência a `avaliacoes`), `trecho_exibido`, `posicao` (de 1 a 3); chave primária (`acao_id`, `avaliacao_id`) e único por (`acao_id`, `posicao`) |
| tokens\_painel | id, token\_hash (único, SHA-256), escopo, criado\_em, expira\_em (= criado\_em + 7 dias), revogado\_em |

**Estados:** alerta → `pendente`, `enviado` ou `falhou`. O alerta é `pendente` quando foi criado e ainda não foi tentado, ou quando o envio inicial está em andamento (incluída a nova tentativa dele); é `falhou` quando teve pelo menos uma tentativa e ela falhou. O Telegram não configurado não é tentativa. Durante um reenvio, um alerta `pendente` continua `pendente` com a tentativa somada, e um `falhou` continua `falhou` até o resultado. Ação → `pendente`, `em_andamento` ou `concluida`. Classificação → `ok` ou `invalida`.

**Ordem do alerta:** o FastAPI grava o alerta como `pendente` **antes** de chamar o Telegram, marca `enviado` ao confirmar e tenta de novo uma vez se falhar (entre as duas tentativas o alerta continua `pendente`); se a nova tentativa também falhar, o estado fica `falhou` (a recuperação posterior está na decisão tomada abaixo). O campo `tentativas` registra as tentativas de envio. A mensagem é única por avaliação e lista todos os tipos identificados, sempre como "relato a verificar". A avaliação e o alerta `pendente` são gravados na mesma transação: se um não for gravado, nenhum dos dois é, e o reenvio da avaliação volta a ser processado. O histórico semanal é calculado das tabelas, sem tabela própria.

**Limite da unicidade:** a restrição única do banco impede um segundo registro de alerta para a mesma avaliação, mas não garante que o Telegram receba uma só mensagem. Se a mensagem for entregue e a confirmação do Telegram se perder (por exemplo, por tempo esgotado), a nova tentativa a envia de novo. Essa entrega duplicada pode ocorrer excepcionalmente. A v1 não garante entrega exatamente uma vez.

**Decisão tomada (31/10/2026):** envio do alerta crítico ao Telegram. Regras:

- O `POST /avaliacoes` grava a avaliação e o alerta `pendente`, responde normalmente e envia a mensagem em tarefa de fundo. A resposta e o código HTTP não dependem do Telegram.
- O `criado_em` do alerta do envio inicial é o instante em que a requisição entrou no endpoint, em UTC (o do alerta criado pelo reprocessamento está na decisão sobre o reprocessamento). O tempo do critério 5 é `enviado_em − criado_em` dos alertas enviados com `tardio` = 0 (o envio inicial), lido da tabela `alertas`; os alertas tardios ficam fora, conforme a decisão sobre a recuperação de alertas.
- Se o token ou o chat do Telegram não estiver configurado, nada é enviado: o alerta fica `pendente`, com `tentativas` igual a 0, e um aviso é registrado no log.

**Decisão tomada (31/10/2026), Fase 2:** mensagem do alerta crítico. O critério: um token por alerta multiplicaria as credenciais válidas por 7 dias no histórico do Telegram, e o alerta já traz o relato. Regras:

- A mensagem tem estes elementos, nesta ordem: o prefixo "(dados simulados)" (como o resumo semanal); a linha "🔴 Alerta crítico: relato a verificar"; o id da avaliação; os tipos identificados; o texto da avaliação, truncado em 300 caracteres; e, só nos reenvios (pela rota ou na subida do servidor; o envio do script de reprocessamento não é reenvio e não leva essa linha), uma linha com o instante de criação do alerta em dd/mm HH:MM, no fuso America/Sao\_Paulo.
- O alerta não leva o link do painel: o link só vai no resumo semanal.
- A especificação é a fonte da verdade do formato: o código (`montar_mensagem`, em `app/alertas.py`) e os testes a seguem.

**Prioridade:** não perder um alerta crítico. Um alerta `falhou` continua registrado no banco, e o caso também aparece no resumo semanal até ser reconhecido. Como garantir que um alerta `pendente` ou `falhou` seja entregue está na decisão tomada sobre a recuperação de alertas, abaixo.

**Decisão tomada (31/10/2026), Fase 2:** recuperação de alertas `pendente` e `falhou`. O critério: não perder um alerta crítico sem gerar spam. Regras:

- Elegibilidade: antes de cada varredura (na subida do servidor e na rota `POST /alertas/reenviar`), todo alerta `pendente` com `tentativas` igual ou maior que 5 passa para `falhou`, sem envio e sem marcar `tardio`. Depois disso, só são enviados alertas `pendente` ou `falhou` com `tentativas` menor que 5 (teto `MAX_TENTATIVAS_TOTAL` = 5, contando as tentativas do envio inicial).
- Ao subir o servidor, o app reenvia, em tarefa de fundo, os alertas `pendente`, pela mesma regra, com o mesmo teto e a mesma janela de 5 minutos do reenvio. É intencional que a subida do servidor reenvie só os `pendente` (ela recupera envios interrompidos), enquanto a rota também reenvia os `falhou`.
- Nova rota `POST /alertas/reenviar` (chamada pelo n8n, com `X-API-Key`): tenta uma vez cada alerta elegível. Incrementa `tentativas` antes de cada envio, marca `enviado` e `enviado_em` no sucesso e deixa o alerta `falhou` na falha (em qualquer caminho, uma tentativa que falha deixa o alerta `falhou`; a exceção é a primeira falha do envio inicial, que mantém o alerta `pendente` até a nova tentativa). Responde 200 com `{elegiveis, enviados, falharam, configurado}`, em que `elegiveis` é a contagem de alertas elegíveis no início da chamada, e `enviados` + `falharam` pode ficar abaixo dela quando outra execução reivindicou o alerta primeiro. Com o Telegram não configurado, nada muda, inclusive `tardio` (não é tentativa: o alerta continua `pendente`, sem incrementar `tentativas`): `configurado` é falso, os demais valores são zero e a rota só registra o aviso. O "nada muda" vale para o envio: a varredura de estado (`pendente` com 5 ou mais tentativas vira `falhou`) roda mesmo assim, porque não envia nada.
- Se a 5ª tentativa falhar, o alerta fica `falhou`, deixa de ser elegível ao reenvio e continua listado no resumo semanal enquanto não for reconhecido.
- O reenvio só considera alertas com `criado_em` há mais de 5 minutos, para não disputar com o envio inicial em andamento. A janela vale para as varreduras (subida do servidor e rota); o envio que o próprio script de reprocessamento faz logo depois de gravar o alerta não a respeita.
- O n8n chama a rota de hora em hora, em um segundo workflow pequeno (Fase 3). A aceitação desse workflow é ele ser executado uma vez, manualmente, e devolver 200 com `configurado` verdadeiro; ele não faz parte do critério 6.
- A mensagem de um reenvio traz uma linha com o instante de criação do alerta.
- Alerta tardio: a coluna `tardio` (inteiro 0 ou 1, padrão 0) da tabela `alertas` vale 1 desde o instante em que um reenvio ou o reprocessamento começa a agir sobre o alerta, mesmo que a tentativa falhe. O envio inicial nunca a altera, e os alertas criados pelo reprocessamento já nascem com `tardio` = 1. O critério 5 e `scripts/medir_alertas.py` medem só os alertas `enviado` com `tardio` = 0. Um banco criado antes da coluna a recebe ao ser aberto, na mesma migração da coluna `reconhecido_em` (migração simples, sem perder dados). A implementação e a atualização do script ficam para a implementação seguinte.
- Reconhecimento: a coluna `reconhecido_em` (instante UTC, nulo por padrão) da tabela `alertas` registra que o dono verificou o relato por fora. Ela não muda o estado: o alerta continua `falhou`. `scripts/reconhecer_alerta.py --id N` (ou `--id-externo`) grava `reconhecido_em` com o instante atual, é idempotente (não sobrescreve o primeiro instante) e só roda localmente. O script aceita apenas alerta em `falhou` e recusa os demais estados. Um alerta com `reconhecido_em` preenchido não é elegível ao reenvio. Um botão no painel fica como candidato da Fase 4.
- Vale o limite da unicidade acima: um reenvio pode duplicar uma mensagem que foi entregue sem confirmação. Execuções simultâneas (n8n, subida do servidor e chamada manual) também podem duplicar uma mensagem; a janela de 5 minutos atenua o risco, e ele é aceito.
- Limitação registrada: com o Telegram fora por cerca de 3 horas ou mais (2 tentativas do envio inicial mais 3 reenvios horários), o alerta chega a 5 tentativas, fica `falhou` e aparece no resumo semanal. É o risco aceito do teto contra spam.
- A v1 roda com um processo (uvicorn sem `--workers`, que é o padrão). O SQLite tem um escritor por vez, e a trava que evita o envio duplicado de alerta vale dentro do processo. Com `--workers N`, uma mensagem de alerta pode sair duplicada num restart; é uma limitação conhecida, e a alternativa futura é um estado "em envio" no esquema.
- Risco aceito: a trava fica presa durante a rede, então uma chamada do n8n pode esperar a recuperação da subida terminar.
- As datas dos alertas são comparadas como texto (elegibilidade e janela de 5 minutos) porque o banco normaliza tudo para UTC; um valor gravado por fora, com outro fuso, está fora do contrato.

**Decisão tomada (31/10/2026), Fase 2:** a coluna `confianca` é substituída por `status_classificacao`, com os valores `ok` e `invalida`. O critério: o classificador congelado da v1 devolve só `categorias` e `criticidade`, sem nenhum valor de confiança, e pedir um ao modelo alteraria o prompt e o esquema congelados (seria uma v2, com conjunto novo de avaliação). Regras:

- `invalida` é o resultado da regra de retentativa congelada (`REGRA_RETENTATIVA`, em `app/classificador.py`, que é a fonte desta lista) quando a última falha, depois de até 3 chamadas, é de conteúdo da resposta: resposta cortada (`max_tokens`), recusa (`refusal`), resposta sem bloco de texto, texto que não é JSON ou JSON fora do contrato. Todas as demais são `ok`. Falha técnica (timeout, erro de conexão, 408, 409, 429, 5xx) e erro permanente da API (400, 401, 403 e outros) não são `invalida`; ver a decisão tomada sobre falhas do classificador.
- É um estado objetivo da classificação, não uma medida de dúvida nem uma probabilidade.
- A avaliação `invalida` é gravada com `categorias` e `criticidade` vazias e é contada na linha de cobertura, em "avaliações que a IA não conseguiu classificar (saída inválida)".
- Marcar como suspeitas as avaliações com nota baixa e sem categoria fica para uma versão futura, porque a especificação ainda não define nota baixa.

**Decisão tomada (31/10/2026), Fase 2:** reprocessamento das avaliações `invalida`. O critério: sem ruído e sem presumir. Regras:

- Não há reprocessamento automático nem alerta genérico. O reprocessamento é manual, por `scripts/reprocessar_invalidas.py` (a implementar na implementação seguinte): reclassifica cada avaliação `invalida` com a regra de retentativa congelada. No sucesso, atualiza a avaliação para `ok` com `categorias` e `criticidade`; se sair marcação crítica, cria o alerta (já com `tardio` = 1, fora do critério 5) no mesmo passo da atualização. O `criado_em` desse alerta é o instante em que o script o grava (UTC), e a janela de 5 minutos conta a partir dele. O próprio script envia o alerta logo depois de gravá-lo, pela regra de envio da decisão de recuperação (incrementa `tentativas` antes de cada envio); o script faz uma tentativa, sem retentativa imediata; esse envio não é reenvio e não leva a linha de instante de criação. Se ela falhar, o alerta fica `falhou` e a chamada horária o retoma enquanto `tentativas` for menor que 5; com o Telegram não configurado, o alerta continua `pendente`.
- O resumo semanal lista todas as avaliações `invalida` da semana (`id_externo` e nota, sem texto) como "classificação falhou, verificar manualmente", porque a avaliação `invalida` é gravada sem `criticidade` e o sistema não sabe se ela é crítica. Uma avaliação `invalida` existente na semana conta como algo a decidir para o campo `enviar` do `GET /resumo-semanal`.
- Limitação registrada: nenhuma rota expõe o texto de uma avaliação `invalida`; o dono confere pelo `id_externo` no app de origem.
- Limitação registrada: não há alerta imediato para um crítico com saída inválida. Alertar toda `invalida` com nota baixa fica para uma versão futura, porque a especificação ainda não define nota baixa.

**Decisão tomada (31/10/2026), Fase 2:** comportamento do `POST /avaliacoes` diante de falhas do classificador. O critério: nada fica gravado pela metade, e a v1 não tem terceiro estado de classificação nem reprocessamento automático. Regras:

- Saída inválida (conteúdo ruim depois da regra de retentativa): a avaliação é gravada como `invalida`, conforme a decisão acima.
- Falha técnica esgotada (timeout, erro de conexão, 408, 409, 429 ou 5xx depois das 3 chamadas): o endpoint devolve 503 e não grava nada. O simulador reenvia a avaliação depois.
- Erro permanente da API (400, 401, 403 e outros), que indica problema de configuração: o endpoint devolve 500 e não grava nada. O erro não é repetido.
- Risco aceito: se a avaliação não for reenviada, ela se perde, inclusive uma avaliação crítica. O reprocessamento manual das `invalida` não recupera uma avaliação que nunca foi gravada.
- Respostas: 201 quando a avaliação é gravada, com `avaliacao_id`, `criada` verdadeiro e `alerta_id` (nulo quando a avaliação não gerou alerta). Isso inclui a avaliação gravada como `invalida`, que recebe 201 com `alerta_id` nulo e não deve ser tratada como erro nem reenviada. 200 com `avaliacao_id` e `criada` falso quando o `id_externo` já existe (duplicata). 401 sem chave, com chave errada ou com `RADAR_API_KEY` não configurada (ausente ou vazia, o que recusa toda requisição), conferida antes de ler o corpo. 422 para corpo que não é JSON ou objeto, campo ausente ou de tipo errado, `id_externo` vazio e `recebida_em` ilegível, sem fuso ou fora do intervalo. 500 genérico, sem detalhe, para erro permanente da API, falha inesperada, `RADAR_DB` ausente ou chave da Anthropic ausente.

**Decisão tomada (31/10/2026), Fase 2:** simulador de avaliações (`scripts/simulador.py`). Regras:

- Envia as 85 avaliações dos arquivos de dados (20 de desenvolvimento, 50 gerais e 15 críticas). O corpo leva só `id_externo`, `texto`, `nota` e `recebida_em`; o gabarito nunca é enviado.
- Os arquivos não trazem `recebida_em`, então o simulador o gera: semente fixa, 4 semanas consecutivas (segunda 00:00 a segunda 00:00, America/Sao\_Paulo) com 21, 21, 21 e 22 avaliações, dia e horário entre 11:00 e 22:59, com fuso. O mesmo resultado se repete com a mesma semente.
- Esse volume semanal é uma **hipótese do simulador**: não vem de dado real nem de medição de restaurante algum, e serve só para exercitar o mecanismo (a amostra mínima N = 20 é atingida em todas as semanas).
- Falhas: 503 e erro de rede reenviam a mesma avaliação (3 tentativas, esperas de 5 s e 15 s); 401 interrompe o envio; 422 e 500 são registrados sem reenvio. Reenviar é seguro, porque a duplicata responde 200.
- Parada por falhas seguidas: o simulador para depois de 3 falhas finais seguidas, de qualquer tipo (503 ou rede esgotados, 422, 500 ou código inesperado); um 200 ou 201 zera a contagem. Sai com código 4; o 401 continua parando na hora, com código 3.
- Parâmetros: a semana inicial padrão é a segunda-feira que abre as 4 semanas completas anteriores à semana atual (America/Sao\_Paulo); as avaliações são enviadas em ordem de `recebida_em`, com timeout de 60 s e 2 s de intervalo, e a chave vem de `RADAR_API_KEY`. Erro de transporte que não é de rede (por exemplo, URL com esquema inválido) não é reenviado.
- `scripts/medir_alertas.py` abre o banco só para leitura e mostra a contagem de alertas por estado e o mínimo, a mediana e o máximo de `enviado_em − criado_em` dos `enviado`. O tempo inclui a classificação, porque `criado_em` é a entrada no endpoint, e inclui o reenvio nos alertas com nova tentativa do envio inicial. Hoje o script mede todos os `enviado`; a partir da implementação seguinte, mede só os `enviado` com `tardio` = 0, conforme a decisão sobre a recuperação de alertas. Em um banco sem a coluna `tardio`, o script conta todos como `tardio` = 0 (antes da coluna, todo envio era inicial), avisa uma vez na saída e continua somente leitura.

**Decisão tomada (30/10/2026), Fase 2:** cada ação registrada fica ligada às avaliações que serviram de evidência, na tabela `acao_avaliacoes`. O critério: o sistema guarda o que o dono viu quando decidiu. Regras:

- Uma ação tem até três avaliações como evidência, e uma avaliação pode ser evidência de várias ações. O banco garante a integridade referencial com `acoes` e `avaliacoes`.
- A ligação é gravada no momento em que o dono registra a ação, com as evidências efetivamente exibidas no card. O servidor não recalcula o ranking nesse momento.
- `trecho_exibido` guarda o trecho mostrado ao dono e `posicao`, a ordem no card, para que o histórico mostre os trechos que o dono viu, mesmo que a seleção de trechos mude no futuro. A contagem de menções continua calculada das tabelas e pode diferir da exibida se uma avaliação for reclassificada.
- Não se associam à ação todas as avaliações da categoria. A quantidade de menções continua calculada das tabelas.
- O histórico permite consultar a ação, suas evidências e o texto original de cada avaliação.
- A medição do efeito continua usando `categoria` e `semana_inicio`, e não usa essa tabela.

**Decisão tomada (31/10/2026), Fase 2:** evidências do `POST /api/painel/{token}/acoes`. O critério: o servidor nunca confia em texto do cliente. Regras:

- O corpo traz `categoria`, `semana_inicio` (segunda-feira local) e de 1 a 3 evidências, cada uma só com `avaliacao_id` e `posicao` (de 1 a 3, distintas, e com `avaliacao_id` distintos). Qualquer outro campo, inclusive `trecho_exibido`, é recusado com 422.
- O servidor confere que cada avaliação existe, foi recebida na semana do card e tem a categoria do card, e que a categoria participa do ranking (`entregador` é recusado). Não recalcula o ranking nem confere se o card estava entre os 3.
- O `trecho_exibido` é derivado pelo servidor do texto da avaliação, pela mesma função usada no `GET /api/painel/{token}/cards`: os primeiros 140 caracteres, cortados em limite de palavra, com reticências. O que o dono vê no card é o que fica gravado.
- O painel renderiza todo texto de avaliação como texto puro (`textContent`), nunca como HTML.
- Token inválido segue a regra uniforme do 404.
- Os códigos de erro, a regra contra ação duplicada e a conferência de que a avaliação estava no card ficam para a Fase 4, ao desenhar o painel.

**Decisão tomada (31/10/2026), Fase 2/4:** rotas de leitura do painel, `GET /api/painel/{token}/cards` e `GET /api/painel/{token}/historico`. Regras:

- Semana dos cards: `?semana=AAAA-MM-DD`, com a mesma validação do `GET /resumo-semanal` (422 fora do formato ou fora da segunda-feira), ou a última semana completa. O token é conferido antes: link inválido dá o 404 uniforme mesmo com `?semana=` inválido.
- Evidências do card: até 3 avaliações da semana com a categoria, ordenadas por menor nota, depois por `recebida_em` mais recente, depois por `id`. Avaliações críticas entram, marcadas como relato a verificar (`critica` verdadeiro).
- Trecho: uma função única (`app/trechos.py`), a mesma que o `POST /api/painel/{token}/acoes` usará: espaços e quebras de linha normalizados, até 140 caracteres cortados em limite de palavra, com reticências quando houve corte. O trecho tem até 140 caracteres de texto e a reticência não conta (até 141 no total).
- Resposta de `/cards`: `periodo`; até 3 `cards` (`categoria`, `problema` com o nome curto, `mencoes`, `taxa_por_100` com 1 casa, `tendencia_em_alta`, `recomendacao`, `evidencias` [{`avaliacao_id`, `posicao` de 1 a 3, `trecho`, `nota`, `critica`}] e `acao`, que é {`id`, `estado`} da ação da categoria, ou nulo); `outros_temas`; `temas_sem_acao`; `cobertura` (o mesmo texto do resumo); `ultima_leitura`. Semana sem avaliações responde 200 com `cards` vazio.
- `ultima_leitura` é o `recebida_em` mais recente do banco (o banco não guarda o instante de gravação), em ISO com fuso America/Sao\_Paulo, ou nulo quando não há avaliações.
- `acoes.semana_inicio` é gravada como o início da semana (segunda-feira 00:00 America/Sao\_Paulo) convertido para UTC (ISO com `+00:00`), o mesmo valor do limite inicial da semana usado nas consultas; é por ele que o card encontra a sua ação. O futuro `POST /api/painel/{token}/acoes` grava no mesmo formato.
- Resposta de `/historico`: `semanas` com pelo menos uma ação, da mais recente à mais antiga, no máximo 12. Cada uma traz `semana_inicio` (ISO com fuso America/Sao\_Paulo), `rotulo` e `acoes` [{`id`, `categoria`, `estado`, `criada_em`, `concluida_em`, `nota`, `evidencias` [{`posicao`, `trecho_exibido`, `texto_original`}]}]. `criada_em` e `concluida_em` saem em ISO com fuso America/Sao\_Paulo, como `periodo` e `ultima_leitura` (o banco continua em UTC). O trecho exibido é o gravado em `acao_avaliacoes`, nunca recalculado.
- As duas rotas só leem o banco, respondem com `Cache-Control: no-store` em todas as respostas (200, 404, 422 e 500) e devolvem os textos de avaliação como dados.
- A página do painel busca o JSON e renderiza os textos com `textContent`; não embute dados de avaliação em `<script>` inline nem usa `innerHTML`.

**Decisão tomada (31/10/2026), Fase 4:** rotas de ação do painel, `POST /api/painel/{token}/acoes` e `PATCH /api/painel/{token}/acoes/{id}`. Regras:

- Uma ação por (`categoria`, `semana_inicio`). A segunda recebe 409 com {`id`, `estado`} da existente. Um índice único no banco garante isso (`CREATE UNIQUE INDEX IF NOT EXISTS`, criado na abertura do banco); é seguro porque a tabela `acoes` nunca foi gravável antes.
- Estados: `pendente` → `em_andamento` → `concluida` e `pendente` → `concluida`, sem voltar atrás. Transição inválida responde 409 com o estado atual. Repetir o mesmo estado responde 200 sem mudança. `concluida_em` é gravado uma vez, na primeira conclusão.
- Nota: texto livre opcional de até 500 caracteres, aceito no POST e no PATCH, editável sempre (inclusive depois de concluída) e devolvido como dado.
- Códigos do POST: 201; token inválido 404, sempre antes de qualquer validação do corpo; corpo inválido 422; duplicata 409. Códigos do PATCH: 200; id inexistente 404 (`{"detail": "ação não encontrada"}`, depois da checagem do token); id que não é inteiro 422; transição inválida 409.
- `semana_inicio` no corpo do POST é a data local da segunda-feira (AAAA-MM-DD) e não pode ser posterior à semana atual (422). É gravada como o início da semana em UTC, como já decidido.
- Evidências do card: o POST **não** confere que a avaliação estava entre as evidências exibidas no card. O servidor confere só que a avaliação existe, é da semana e tem a categoria. Motivo: uma avaliação nova pode mudar o card entre abrir a tela e salvar, e o POST falharia sem o dono ter feito nada errado. O POST aceita ação para categoria sem card (em "outros temas"), pois só exige que a categoria esteja no ranking e não seja `entregador`.
- Nota vazia: `null` apaga a nota; texto vazio ou só com espaços também vira `null` (POST e PATCH). Uma nota com texto não é alterada (sem `strip`). O limite de 500 caracteres continua valendo para o que chega. No PATCH, uma nota vazia sozinha é uma mudança válida, não um "corpo vazio".
- O corpo do 409 traz `detail` além de `id` e `estado` (duplicata) ou `estado` (transição inválida).
- O 422 tem dois formatos: lista de {`campo`, `erro`} quando a validação automática recusa o corpo, e texto simples em `detail` quando é regra do servidor. A página trata qualquer 422 como "não foi possível salvar" e não lê o `detail`.
- PATCH com 409: a nota enviada junto **não** é gravada (tudo ou nada).
- Janela entre conferir as evidências e gravar: hoje é inofensiva, porque o reprocessamento só altera avaliações `invalida`, que nunca passam na conferência. Se isso mudar, a conferência precisa ir para dentro da transação.

**Decisão tomada (31/10/2026), Fase 4, bloco 4.3:** página do painel, `GET /painel/{token}`. O critério: a página é só uma vitrine das rotas que já existem, e nenhum texto vindo do servidor é interpretado como HTML. Regras:

- Os arquivos da página ficam em `app/painel/` (HTML, CSS e JS), sem framework e sem etapa de build. A rota confere o token com a mesma validação das rotas do painel e devolve o HTML. O CSS e o JS são servidos em `/estatico/painel/...`, fora do caminho que leva o token.
- Token inexistente, expirado, revogado, malformado ou de outro escopo: 404 com a página estática "Link inválido ou expirado", a mesma para todos os motivos. `/painel/` sem token ou com mais de um trecho no caminho dá a mesma página com 404. Falha de configuração ou de banco continua 500 genérico.
- Cabeçalhos da página (200 e 404): `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` e `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. Por isso o HTML não tem script inline, atributo `style` nem atributo de evento.
- O JS lê o token do caminho e chama só `/cards`, `/historico`, o POST e o PATCH de ações. O token não vai para `localStorage`, cookie, query string nem console. O HTML não traz dado de avaliação; todo texto vindo da API entra na página com `textContent`. Um teste varre o JS contra `innerHTML`, `insertAdjacentHTML`, `outerHTML`, `document.write`, `eval` e `new Function`.
- Na abertura, a página busca `/cards` (última semana completa) e `/historico`. Os cards trazem só `id` e `estado` da ação; a nota e as datas vêm do histórico, pelo `id`.
- `outros_temas`, `temas_sem_acao` e o histórico trazem só a categoria. O nome curto e a recomendação de cada categoria são repetidos no JS, e um teste confere que são iguais aos de `app/resumo.py`.
- Semana sem cards: a explicação de volume pequeno só aparece quando `cobertura.total_lido` é menor que N = 20. O N fica no JS, e um teste confere que é igual ao `amostra_minima` da configuração do ranking.
- O POST envia todas as evidências do card (só `avaliacao_id` e `posicao`) e omite a nota vazia. 201 mostra o card com a ação; 409 recarrega os dados e mostra o estado real, sem erro; 404 mostra a página de link inválido; 422, 500 ou falha de rede mostram "Não foi possível salvar", sem ler o corpo do erro e mantendo a nota digitada. O POST e o PATCH decidem só pelo código de status e não leem o corpo da resposta.
- No PATCH, 200 e 409 recarregam os dados; 422, 500 ou falha de rede mostram "A mudança não foi salva" (a ação já existe, então a página não diz "nada foi registrado"). O 404 do PATCH também recarrega, porque pode ser "ação não encontrada" ou link inválido: se o link for inválido, o `/cards` responde 404 e a página mostra a tela de link inválido.
- Depois da primeira carga, a página recarrega os cards com `?semana=` da semana aberta, para que a virada da semana com a página aberta não troque os cards na tela.
- A página só oferece "Marcar como concluída" e "Editar nota" (o estado `em_andamento` é exibido se existir). O selo de uma ação `pendente` diz "Decidida".
- A página não promete link novo na segunda-feira: o workflow do n8n ainda não existe.
- Texto do card: a página mostra a taxa ("X em cada 100 avaliações citam este problema") e o período no subtítulo, e o botão se chama "Vou fazer isso". Isso substitui, só para o painel, "a quantidade e o período" e o botão "registrar ação" da seção "Regras de negócio". A regra "o padrão sugere" vale para a mensagem do resumo semanal; o painel usa "O que fazer". Os contratos e as mensagens do resumo semanal não mudam.
- Limite conhecido da v1: `/historico` não traz `critica`, então a tela 05 não mostra o rótulo "relato a verificar" em uma evidência crítica. O contrato não muda.
- Limites conhecidos de navegação e carga: o "voltar" do celular sai do painel, e a falha ao recarregar depois de salvar mostra "Não consegui carregar" sem confirmar que a ação foi salva.
- Divergências aprovadas em relação ao desenho: cards decididos completos (não compactos), fonte system-ui (a Inter não está em `app/painel/`) e "1 de 3 decidido" no singular.
- Fase 3: o workflow do n8n deve enviar a mensagem do resumo sem pré-visualização de link (`disable_web_page_preview`), para que os servidores do Telegram não abram `/painel/<token>`.
- Pendência conhecida: o teste automático no navegador (Playwright) não existe. A conferência foi manual.
- Acabamento (só apresentação): a linha técnica de cobertura nas telas 01 e 06 fica atrás do botão "Como o Radar leu esta semana" (recolhida a cada carga, texto do servidor sem alteração, só com `textContent`), e a tela 01 mostra uma faixa de atenção acima dos cartões quando `cobertura.total_lido` é menor que a amostra mínima (o mesmo N = 20 do JS). Nenhum contrato muda; o texto técnico continua disponível.

**Decisão tomada (31/10/2026), Fase 2:** esquema de `acao_avaliacoes`, integridade e datas, adotados na camada de persistência (`app/banco.py`). Regras:

- `trecho_exibido` e `posicao` não aceitam nulo.
- As chaves estrangeiras usam `ON DELETE RESTRICT`: não se apaga uma ação, uma avaliação ou uma avaliação com alerta enquanto houver registros ligados a elas.
- O esquema permite uma ação sem evidências. A exigência de pelo menos uma evidência fica para a API, ao registrar a ação.
- `recebida_em` precisa ter fuso: uma data sem fuso é recusada. As datas (`recebida_em`, `prazo_resposta`, `criado_em`, `enviado_em` e `reconhecido_em` do alerta, `expira_em` do token) são gravadas em UTC, em ISO 8601 com `+00:00`.
- As semanas continuam definidas em America/Sao\_Paulo. Para consultar uma semana, os limites são convertidos para UTC, o que permite comparar os textos das datas.

**Requisito de implementação:** o SQLite só aplica chaves estrangeiras com `PRAGMA foreign_keys=ON` em cada conexão. A Fase 2 deve ativá-lo e ter um teste que tente gravar uma evidência de avaliação inexistente.

## Regras de negócio

**O dono vê no máximo 3 cards por semana, ordenados por impacto, e decide tudo em menos de 2 minutos.**

- **Pontuação de um problema:** `pontuacao = mencoes × gravidade × fator_tendencia`. A gravidade é um peso fixo por categoria e o fator de tendência multiplica a pontuação quando o problema subiu em relação à semana anterior. Os valores estão na decisão tomada abaixo.
- **Conteúdo de um card:** o problema em uma linha, a quantidade e o período, até 3 trechos reais como evidência, a recomendação e o botão "registrar ação". O que não entra nos 3 cards fica um toque adiante, em "outros temas".
- **Linha de cobertura fixa:** total de avaliações lidas, cards, temas monitorados sem ação e avaliações que a IA não conseguiu classificar (saída inválida). Quando o mínimo de avaliações para comparar a tendência não é atingido em alguma das duas semanas, a linha traz "tendência não calculada: amostra pequena".
- **Sem presumir causa:** o texto diz "o padrão sugere" e recomenda investigar. Entregador nunca gera ação interna.
- **Alerta crítico:** sai na hora em que a avaliação entra no endpoint, com o texto "relato a verificar", em uma mensagem por avaliação que lista todos os tipos identificados. Os casos críticos também contam no resumo semanal. O alerta pode ser enviado automaticamente ao dono, sempre como relato a verificar. Respostas públicas aos clientes nunca são enviadas automaticamente.
- **Resumo semanal:** segunda-feira às 8h, no fuso America/Sao\_Paulo. Só é enviado se houver algo a decidir, exceto em semana de volume pequeno (menos de N = 20 avaliações), quando, mesmo sem cards e sem nenhum outro motivo para enviar, sai um aviso curto; um indicador "última leitura" no painel mostra que o sistema está vivo.
- **Medição do efeito:** compara a taxa de menções por 100 avaliações entre semanas, antes e depois de uma ação concluída. É uma comparação simulada e não prova causalidade.
- **Prazo de resposta:** cada avaliação guarda o prazo de 5 dias do iFood, para a versão futura de respostas em lote ([fonte](https://blog-parceiros.ifood.com.br/avaliacoes-e-moderacoes/)). Na v1, os rascunhos de resposta em lote são opcionais e o primeiro corte se houver atraso.

**Decisão tomada (31/10/2026), Fase 2:** priorização dos cards semanais. O critério: o peso de gravidade reflete o quanto o problema, quando ocorre, pode comprometer o pedido. Os pesos e o fator de tendência são **hipóteses de priorização da v1, configuráveis e sem validação com dados reais**; não medem prejuízo, reembolso nem perda de clientes.

| Categoria | Peso de gravidade |
| --- | --- |
| `qualidade_temperatura` | 3 |
| `item_faltando` | 3 |
| `atraso` | 2 |
| `embalagem` | 2 |
| `atendimento` | 2 |
| `entregador` | não participa do ranking |

- A fórmula está no bullet "Pontuação de um problema", acima: gravidade e tendência multiplicam as menções.
- `fator_tendencia` vale 1,25 quando a taxa de menções por 100 avaliações aumentou em relação à semana anterior. Nos demais casos (taxa igual, taxa menor ou semana anterior não comparável, isto é, inexistente ou com menos de N avaliações) vale 1,0. O aumento é estrito: qualquer taxa maior conta, sem tolerância. A primeira semana do histórico não tem semana anterior. A comparação usa a taxa por 100 avaliações, e não a quantidade absoluta, para que uma semana com mais avaliações não conte como crescimento.
- Nesta configuração inicial nenhuma categoria tem peso 1.
- `entregador` não ocupa um dos três cards, porque o restaurante não tem ação interna definida para ele. As menções continuam contadas e visíveis em "temas monitorados sem ação".
- Limitação deliberada: `qualidade_temperatura` reúne problemas de intensidades diferentes e todos recebem o mesmo peso 3. A decisão sobre dividir a categoria está na seção "Categorias e criticidade".
- Os casos com marcação crítica seguem o fluxo próprio de alertas, independentemente do ranking semanal.

**Decisão tomada (31/10/2026), Fase 2:** complemento do ranking dos cards semanais: desempate, amostra mínima e definições de cálculo. Como na decisão anterior, os valores são hipóteses configuráveis da v1, sem validação com dados reais.

- **Peso e tendência:** com as mesmas menções, o peso de gravidade pesa mais do que a tendência. Uma categoria de peso 2 em alta (2 × 1,25 = 2,5) ainda fica abaixo de uma de peso 3 sem alta (3). É intencional.
- **Desempate:** quando as pontuações são iguais, ordena-se por maior peso de gravidade, depois por mais menções e, por último, pelo identificador da categoria em ordem alfabética. Os mesmos dados produzem sempre a mesma ordem.
- **Amostra mínima:** o fator de tendência só é calculado se as duas semanas comparadas têm pelo menos N = 20 avaliações. Caso contrário, vale 1,0. Os cards continuam sendo calculados por menções × gravidade, apenas sem o bônus de tendência. Se a semana anterior tem amostra suficiente e taxa 0, um aumento real vale 1,25.
- **Resumo com volume pequeno:** a semana com menos de N avaliações ainda gera resumo. Se houver cards, o resumo sai normalmente. Se não houver nenhum e nada mais motivar o envio, sai um aviso curto com o total de avaliações lidas, para que o silêncio não seja confundido com sistema parado. A linha de cobertura traz "tendência não calculada: amostra pequena" quando o mínimo não é atingido em alguma das duas semanas.
- **`mencoes`:** número de avaliações da semana que têm a categoria; uma avaliação conta uma vez por categoria. A semana é o intervalo de segunda-feira 00:00 (inclusive) até a segunda-feira seguinte 00:00 (exclusive), no fuso America/Sao\_Paulo, pelo campo `recebida_em`. Uma avaliação recebida na segunda-feira, mesmo antes das 8h, pertence à semana que começa nessa segunda; o resumo de segunda às 8h cobre a semana que terminou. O denominador de "por 100 avaliações" é o total de avaliações recebidas na semana, inclusive as positivas, as sem categoria e as de saída inválida.
- **Avaliações críticas:** contam em `mencoes` das suas categorias operacionais e podem ser evidência de um card, mantendo o rótulo "relato a verificar". O alerta crítico segue o fluxo próprio.
- **Configuração:** pesos, fator de tendência e N ficam em um arquivo de configuração versionado no código. O ranking exibido não é gravado, então mudar um valor muda o ranking calculado de semanas passadas. Para as ações registradas, o sistema guarda as evidências exibidas (`acao_avaliacoes`), mas não a pontuação, a posição do card nem a versão dos pesos.

**Decisão tomada (31/10/2026), Fase 2:** cálculo do ranking dos cards (`config/ranking.json`, `app/ranking.py`). Regras:

- A configuração é um JSON com `pesos` (as seis categorias; inteiro positivo ou `null` para `entregador`, que fica fora do ranking), `fator_tendencia` (número maior que 1) e `amostra_minima` (inteiro maior ou igual a 1). Chave ausente, chave ou categoria desconhecida e valor fora do tipo são recusados ao carregar.
- Taxas, fator e pontuação são frações exatas, sem ponto flutuante. O aumento da taxa é comparado por multiplicação cruzada de inteiros: `mencoes_atual × total_anterior > mencoes_anterior × total_atual`.
- "Outros temas" são as categorias do ranking com menções que ficaram fora dos 3 cards, na mesma ordem. "Temas monitorados sem ação" são as categorias fora do ranking (`entregador`), sempre listadas com suas menções.
- Avaliação negativa é a de nota menor ou igual a 2.
- O validador da configuração recusa um `fator_tendencia` que viole "peso vence tendência": para cada par de pesos distintos consecutivos, `fator × menor_peso` deve ser menor que `maior_peso`. Com pesos 2 e 3, 1,5 é recusado e 1,49 é aceito; com 1,25 continua valendo.
- O resultado do ranking traz `motivo_sem_tendencia`: `null` quando a tendência foi calculada; `sem_semana_anterior` quando não há semana anterior, com o texto de cobertura "tendência não calculada: sem semana anterior"; `amostra_pequena` quando a semana anterior existe, mas uma das duas semanas tem menos de N avaliações, com o texto "tendência não calculada: amostra pequena". O endpoint passa `anterior=None` quando a semana anterior tem 0 avaliações.

**Decisão tomada (31/10/2026), Fase 2:** `GET /resumo-semanal` (`app/resumo.py`). Regras:

- Parâmetro opcional `?semana=AAAA-MM-DD`, que precisa ser uma segunda-feira (data local de America/Sao\_Paulo); fora disso, 422. Sem ele, vale a última semana completa. A chave é conferida antes do parâmetro.
- A resposta traz `periodo`, `total`, `negativas`, `invalidas`, até 3 `cards` (categoria, menções, taxa por 100 com 1 casa, tendência em alta, problema e recomendação da tabela de categorias; sem pontuação), `outros_temas`, `temas_sem_acao`, `criticos`, `invalidas_a_verificar` (lista com `id_externo` e nota, sem texto), `alertas_falhos` (os alertas em estado `falhou` com `reconhecido_em` nulo, de qualquer semana, com `id_externo`, `tipos` e `criado_em`, este em ISO com fuso America/Sao\_Paulo, como `periodo`), `acoes_pendentes`, `cobertura`, `enviar`, `mensagem` (texto simples, que começa com "(dados simulados)" e usa "o padrão sugere") e `link_painel`.
- "Algo a decidir" significa pelo menos um card, um crítico, uma avaliação `invalida`, um alerta `falhou` não reconhecido ou uma ação pendente. `enviar` é verdadeiro nesses casos e também em semana com menos de N avaliações (inclusive 0). O aviso curto com o total lido só sai quando nada além do volume pequeno motivou o envio; havendo qualquer outro motivo, a mensagem sai no formato normal, com os cards (se houver), as seções correspondentes e o total lido.
- O aviso curto de volume pequeno não leva o link do painel nem cria token (`link_painel` nulo); o formato normal continua com o link.
- O aviso curto traz também a linha de cobertura (fixa), depois do total lido, com "tendência não calculada: amostra pequena" quando for o caso.
- Cada crítico mostra o estado do alerta (`pendente`, `enviado`, `falhou` ou nulo, se não houver alerta) e a marca "reconhecido" quando o alerta tem `reconhecido_em`. Um alerta `falhou` reconhecido continua em `criticos` da sua semana, com o estado e a marca, e sai de `alertas_falhos`. Na `mensagem`, os críticos da semana mostram o estado do alerta, e a seção "Alertas que não chegaram" lista só os `falhou` não reconhecidos de outras semanas (o JSON pode repetir; a mensagem não repete). A recuperação de alertas `pendente` e `falhou` está na decisão sobre a recuperação de alertas.
- Ação pendente é a de estado `pendente` ou `em_andamento`, de qualquer semana.
- O link do painel é `RADAR_URL_BASE/painel/<token>`. `RADAR_URL_BASE` vem do ambiente (http ou https, sem barra final); ausente ou inválida, a rota responde 500 e não cria token.
- Efeito colateral: o GET cria um token novo em `tokens_painel` a cada chamada com `enviar` verdadeiro, exceto no aviso curto de volume pequeno, que não cria token, e nenhum quando é falso. É a única escrita da rota, e a resposta vai com `Cache-Control: no-store`.
- O histórico de execução do n8n guarda a resposta com o link em claro, válido por 7 dias e com escopo só de painel. O risco é aceito e documentado. O workflow do n8n envia a mensagem sem `parse_mode`, porque o token pode ter `_`.
- `?semana=` aceita a semana atual (ainda incompleta) e semanas futuras, que resultam em volume pequeno.
- O título do card é um nome curto por categoria (por exemplo, "Item ausente ou errado"), e não a descrição longa da tabela de categorias.

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
| 5 | Alerta imediato | Tempo medido entre a entrada no endpoint e a confirmação do Telegram, só os alertas com `tardio` = 0 (o envio inicial) | 3 |
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
| APIs sem backend profundo | FastAPI pequeno com 8 rotas e SQLite |
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
