# Workflows do n8n (Fase 3)

Dados simulados. Projeto independente, sem vínculo com o iFood.

`docs/ESPECIFICACAO.md` é a fonte da verdade. Os dois workflows ficam em `n8n/` e só chamam rotas do FastAPI
(`GET /resumo-semanal` e `POST /alertas/reenviar`); o n8n não tem regra de negócio.

| Arquivo | Quando roda | O que faz |
| --- | --- | --- |
| `n8n/semanal.json` | Segunda-feira, 8h (America/Sao_Paulo) | Busca o resumo; se `enviar` for `true`, manda o campo `mensagem` pelo Telegram. Se for `false`, termina sem enviar. |
| `n8n/reenvio.json` | De hora em hora | Chama `POST /alertas/reenviar`. Nenhum outro efeito. |

## 1. Subir o n8n e o app

```bash
npx n8n
```

O n8n abre em `http://localhost:5678` (na primeira vez ele pede para criar um usuário local). Em outro terminal,
suba o app como no README (`uvicorn app.main:app --env-file .env`), na porta 8000, com `RADAR_API_KEY`,
`RADAR_DB`, `RADAR_URL_BASE`, `RADAR_TELEGRAM_TOKEN` e `RADAR_TELEGRAM_CHAT_ID` definidos. Sem as duas do
Telegram, o reenvio responde `configurado: false`.

Se o n8n mostrar o assistente de configuração na primeira abertura, pode pular tudo: não é preciso ligar
assistente, MCP nem sandbox.

**`RADAR_URL_BASE`** é a base do link do painel: o link da mensagem é `RADAR_URL_BASE/painel/<token>`. Aceita
`http` ou `https`, sem barra final. Se estiver ausente ou inválida, o `GET /resumo-semanal` responde 500 e não
cria token (especificação, decisão do `GET /resumo-semanal`). Para o teste local use
`http://127.0.0.1:8000`; com esse valor, o link na mensagem só abre no computador onde o app roda.

## 2. Importar os dois workflows

No n8n: **Workflows** → menu ao lado de "Create workflow" (ou o menu `⋯` de um workflow novo) →
**Import from file** → escolha `n8n/semanal.json`. Repita com `n8n/reenvio.json`.

Os dois entram **desativados**. Só ative (chave **Active**/**Publish**, conforme a versão) depois de configurar
tudo abaixo.

**Fuso.** Depois de importar, abra **Settings** do workflow (em cada um) e confira que **Timezone** é
`America/Sao_Paulo`.

## 3. Credenciais: só dentro do n8n

Nenhum valor real está no repositório nem nos JSON. Os JSON trazem só placeholders e **não trazem o bloco
`credentials`**: você escolhe as credenciais dentro do n8n.

**Segredo da API (`X-API-Key`).** Em **Credentials** → **Create credential** → **Header Auth**:

- **Name** é o **nome do cabeçalho**: escreva `X-API-Key` (não o nome da credencial nem o valor da chave).
- **Value** é o valor de `RADAR_API_KEY` do app.

Abra o nó **Buscar resumo semanal** (em `semanal`) e o nó **Reenviar alertas** (em `reenvio`) e, em
*Credential for Header Auth*, selecione essa credencial.

**Token do bot e chat id do Telegram.** Abra o nó **Enviar no Telegram** (em `semanal`) e troque os dois
placeholders:

- na URL, `<TOKEN_DO_BOT>` pelo token do bot (o mesmo de `RADAR_TELEGRAM_TOKEN`), mantendo o prefixo `bot`;
- no corpo (JSON), `<CHAT_ID>` pelo id do chat (o mesmo de `RADAR_TELEGRAM_CHAT_ID`), mantendo as aspas.

> **Por que o envio é um HTTP Request e não o nó "Telegram"?** O nó Telegram do n8n manda
> `parse_mode=Markdown` quando o campo está vazio (conferido em 01/11/2026 no `Telegram/GenericFunctions.ts` da
> branch `master` do n8n; a versão que você roda pode ser outra), e a
> especificação exige a mensagem sem `parse_mode`, porque o token do painel pode ter `_`. O HTTP Request
> chama `sendMessage` da API do Telegram com `chat_id`, `text` (campo `mensagem`) e
> `disable_web_page_preview: true`, sem `parse_mode`. O preview fica desligado para os servidores do Telegram
> não abrirem o link do painel.

> **Cuidado ao exportar.** O Telegram só aceita o token do bot no caminho da URL, então ele fica no próprio nó.
> **Se você exportar o workflow depois de colar o token, o export leva o token junto.** Não commite esse
> export, não o cole em chat nem em issue. Se isso acontecer, revogue o token no BotFather e gere outro. Fora do
> export, o token também fica em texto claro nos parâmetros do workflow, no banco do n8n e no histórico de versões
> do workflow; só as credenciais do n8n são criptografadas. Se alguém mais tiver acesso a esse n8n, trate o token
> como exposto. Exportar depois de executar também pode levar dados de execução fixados (com o link do painel). Um teste
> (`tests/test_n8n_workflows.py`) falha se algum JSON de `n8n/` tiver algo no formato de token do Telegram.

**Onde trocar o endereço do app.** A URL está fixa em `http://127.0.0.1:8000` nos nós **Buscar resumo semanal**
(`semanal`) e **Reenviar alertas** (`reenvio`). Para mudar a porta ou o endereço, edite o campo *URL* desses dois
nós. Usamos `127.0.0.1` em vez de `localhost` porque, no Windows, `localhost` pode resolver para IPv6 (`::1`)
enquanto o uvicorn escuta em IPv4; com `localhost`, o n8n recebeu "The service refused the connection". Um teste
falha se algum JSON de `n8n/` voltar a usar `localhost`.

## 4. Executar manualmente

Abra o workflow e clique em **Execute workflow** (o botão de teste). Ele roda a partir do gatilho de
agenda, sem esperar o horário.

- **`semanal`**: se `enviar` for `true`, **manda a mensagem de verdade no Telegram**. Na semana normal o app
  também **cria um token novo do painel** (válido por 7 dias); no aviso curto de volume pequeno, que tem
  `enviar` verdadeiro, não há link nem token. Se `enviar` for `false`, o fluxo para no nó **Enviar?** sem enviar
  nada, e isso é o comportamento esperado.
- **Telegram e 429.** Clicar várias vezes seguidas em executar pode devolver 429 (Too Many Requests, "retry
  after N"). Espere o tempo indicado e execute uma vez.
- **`reenvio`**: a aceitação (especificação, decisão de recuperação de alertas) é a execução manual devolver
  200 com `configurado` verdadeiro. Abra a saída do nó **Reenviar alertas** e confira
  `{elegiveis, enviados, falharam, configurado}`. Com o Telegram não configurado no app, `configurado` vem `false`.

**Histórico de execuções:** aba **Executions** dentro do workflow, ou **Executions** no menu lateral para
todos. Cada linha mostra o estado (sucesso/erro), o horário e, ao abrir, a entrada e a saída de cada nó. Esse
histórico é a evidência do critério 6.

## 5. Ressalvas

**O histórico guarda o link do painel em texto claro.** A resposta de `GET /resumo-semanal` traz `mensagem` e
`link_painel` com o token em claro, e o n8n guarda a entrada e a saída dos nós. O link vale **7 dias**, e o
padrão do n8n (`EXECUTIONS_DATA_MAX_AGE`, 336 horas) mantém o histórico por **14 dias**. Na prática, o link
fica legível no histórico por mais tempo do que vale, mas depois dos 7 dias ele já expirou. Quem abrir o n8n
local nesse período lê um link ainda válido, com acesso só ao painel. **Risco aceito e documentado**
(especificação, decisão do `GET /resumo-semanal`).

**Agendamento local.** Com o n8n local, as agendas só disparam com o **n8n e o app ligados** e com o workflow
**ativo**. Se o computador estiver desligado às 8h de segunda, o resumo daquela semana não sai; não conte com
recuperação automática de execuções perdidas. O reenvio de alertas sofre a mesma limitação.

**Efeito de cada chamada.** O `GET /resumo-semanal` com `enviar` verdadeiro cria um token novo a cada chamada,
inclusive nas execuções manuais, exceto no aviso curto de volume pequeno (sem link e sem token). Executar a
mesma semana duas vezes gera dois links válidos. Por isso os nós **não** têm nova tentativa automática
(`Retry On Fail`): não ligue essa opção no nó **Buscar resumo semanal**.

**Falhas não avisam ninguém.** Se o app estiver fora, a chave estiver errada (401) ou o Telegram recusar a
mensagem, a execução só fica com erro na aba **Executions**. Se o `GET` já criou o token e o envio ao Telegram
falhou, o link existe mas não chegou; rode o `semanal` de novo (gera outro link). O texto da `mensagem` não é
limitado em tamanho pelo app e o Telegram recusa mais de 4096 caracteres; uma semana com muitos críticos pode
estourar esse limite (risco registrado aqui, sem mudança na especificação).

## 6. O que foi provado e o que não foi

**Provado em 01/11/2026** (n8n local no Windows, execução manual):

- O `semanal` executado manualmente, com a mensagem chegando ao Telegram.
- O `reenvio` executado manualmente, com `configurado` verdadeiro.

**Não provado:**

- **O disparo pela agenda.** Os workflows ficaram inativos; nenhuma execução foi disparada pelo Schedule
  Trigger. Isso inclui o funcionamento de `0 8 * * 1` e de `0 * * * *` no fuso do workflow.
- **Se o histórico de execuções do n8n guarda a URL do nó "Enviar no Telegram"** (não verificado). Se guardar, o
  token do bot ficaria legível no histórico e em quem abre o n8n local. Para conferir, force uma falha
  controlada (por exemplo, `<CHAT_ID>` errado), abra a execução com erro e veja se a URL com o token aparece. Se
  aparecer, trate o token como exposto a quem abre aquele n8n.
