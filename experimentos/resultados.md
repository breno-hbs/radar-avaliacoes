# Resultados dos experimentos do Post 1

Dados fictícios. Projeto independente, sem vínculo com o iFood.
Este arquivo registra só observações, limitações, configuração e erros. Não contém
interpretação, gancho nem roteiro. Respostas completas e metadados: `experimentos/saidas/*.json`.

## Configuração usada (as 8 chamadas)

| Item | Valor |
|---|---|
| Provedor / modelo | Anthropic / `claude-sonnet-5-5` |
| Cliente | `anthropic.Anthropic(max_retries=0)` |
| `max_tokens` | 400 |
| `thinking` | `{"type": "between_tools"}` |
| `output_config` | `{"effort": "medium"}` |
| `temperature`, `top_p`, `top_k` | omitidos (o modelo os rejeita); nenhum valor padrão presumido |
| SDK | `anthropic==2.3.0`, `python-dotenv==1.2.4`, Python 3.13 |
| Data e hora | 2026-10-29, entre 03:45:55 e 03:46:09 UTC |

## Execução

- Chamadas realizadas: **8** de 8 planejadas (3 + 3 + 2).
- Erros de API: **nenhum**. Erro 400: nenhum. Nada foi repetido.
- Respostas cortadas: **nenhuma** (as 8 terminaram com `stop_reason = end_turn`).
- Tipos de bloco: as 8 respostas tiveram **somente bloco `text`**. Não apareceu bloco de pensamento.
- Todas as 8 chamadas contam como resultado válido.

Tokens (entrada / saída), conforme informado pela API:

| Chamada | Entrada | Saída |
|---|---|---|
| exp1-chamada-1 | 43 | 123 |
| exp1-chamada-2 | 43 | 129 |
| exp1-chamada-3 | 43 | 94 |
| exp2-1-informa | 50 | 28 |
| exp2-2-sem-historico | 32 | 32 |
| exp2-3-com-historico | 108 | 25 |
| exp3-vaga | 116 | 178 |
| exp3-clara | 235 | 61 |

## Experimento 1: mesma pergunta, três chamadas

Pergunta enviada nas três chamadas, sem histórico: "Em até duas frases curtas: por que um
restaurante deve ler as avaliações dos clientes toda semana?"

**Chamada 1:** Ler as avaliações toda semana permite identificar rapidamente problemas recorrentes, como demora no atendimento ou falhas na comida, e corrigi-los antes que afastem mais clientes. Também mostra o que os clientes mais valorizam, ajudando a reforçar os pontos fortes e a decidir melhor sobre cardápio e serviço.

**Chamada 2:** Ler as avaliações semanalmente permite identificar rapidamente problemas recorrentes, como demora no atendimento ou falhas na cozinha, e corrigi-los antes que afastem mais clientes. Também mostra o que o público mais valoriza, ajudando a reforçar os pontos fortes e a responder com agilidade, o que melhora a reputação do restaurante.

**Chamada 3:** Ler as avaliações toda semana permite identificar problemas de atendimento, comida ou higiene antes que se repitam e afastem clientes. Também mostra o que está agradando, ajudando a manter os acertos e a responder rapidamente, o que fortalece a reputação.

Observações:
- Textos distintos por comparação exata (sem strip, lower ou normalização): **3 de 3**.
- Cada resposta tem duas frases, como pedido.

## Experimento 2: transmissão de contexto

Nome fictício: Cantina Lua de Papel.

| Chamada | Mensagens enviadas | Resposta |
|---|---|---|
| 1. Informa | 1 mensagem: o nome e o pedido de confirmação curta | Anotado: seu restaurante se chama Cantina Lua de Papel. |
| 2. Sem histórico | 1 mensagem: "Qual é o nome do meu restaurante? Responda em uma frase curta." | Não sei o nome do seu restaurante, pois você ainda não me contou — qual é? |
| 3. Com histórico | 3 mensagens: a da chamada 1, a resposta 1 como turno do assistente e a pergunta | O nome do seu restaurante é Cantina Lua de Papel. |

Como foi garantida a ausência de contexto na chamada 2: a API de mensagens não guarda estado
entre chamadas; a lista enviada tinha só a pergunta (conferido pelo `assert` no código e
visível em `mensagens_enviadas` no JSON), sem sessão, thread, `previous_response_id`,
objeto de chat ou identificador de contexto.

Como foi transmitido na chamada 3: reenvio explícito, na lista de mensagens, da mensagem
original, do texto da resposta 1 e da nova pergunta.

Observações:
- A resposta sem histórico não contém o nome (busca exata por "Cantina Lua de Papel": `False`).
- A resposta com histórico contém o nome (`True`).
- A entrada da chamada 3 foi 108 tokens, contra 32 da chamada 2.

## Experimento 3: instrução vaga × clara

Avaliação sintética, igual nas duas condições: "Nota 2. Pedi uma lasagem e um refrigerante.
O pedido chegou mais de uma hora depois do horário combinado, a lasagem veio fria e o
refrigerante não veio na sacola. O entregador foi educado."

**Prompt vago:** "O que você acha desta avaliação de restaurante? Seja breve." + avaliação.

**Prompt claro:** "Liste os problemas relatados nesta avaliação de restaurante. Use somente
estas categorias: atraso, item_faltando, qualidade_temperatura, embalagem, atendimento,
entregador. Formato: uma linha por problema, no padrão 'categoria: motivo em poucas palavras'.
No máximo 5 linhas, sem texto antes ou depois. Se não houver problema, escreva 'nenhum'." + avaliação.

### Respostas completas, lado a lado

| Prompt vago | Prompt claro |
|---|---|
| Avaliação clara e justa. Ela traz os três problemas concretos (atraso de mais de uma hora, comida fria, item faltando) e ainda reconhece o ponto positivo (entregador educado), o que dá credibilidade.<br><br>Um detalhe: atraso e item faltante costumam ser falha da operação de entrega ou da embalagem, não necessariamente da cozinha, então vale deixar claro o que foi culpa do restaurante. Mesmo assim, para quem lê, ela é útil e equilibrada. | atraso: chegou mais de uma hora depois do combinado<br>qualidade_temperatura: lasagem chegou fria<br>item_faltando: refrigerante não veio na sacola |

Tokens de saída: vago 178, claro 61.

### Identificação dos problemas do gabarito (preenchida por Breno em 29/10/2026, lendo as respostas completas)

A coluna "Categoria" é só referência. Critérios no [README](README.md).

| # | Problema | Categoria | Vago | Claro |
|---|---|---|---|---|
| P1 | Entrega mais de uma hora depois do combinado | `atraso` | SIM | SIM |
| P2 | Lasagem chegou fria | `qualidade_temperatura` | SIM | SIM |
| P3 | Refrigerante não veio | `item_faltando` | SIM | SIM |
| | Apontou como problema algo que o gabarito exclui (ex.: entregador) | | NÃO | NÃO |
| | Total (0 a 3) | | 3/3 | 3/3 |

A pontuação P1-P3 foi feita por Breno, lendo as respostas. Nenhum modelo foi usado como juiz.

### Obediência ao formato (observação à parte, só prompt claro)

Verificação automática em `exp3_vaga_x_clara.py`:

| Verificação | Resultado |
|---|---|
| No máximo 5 linhas não vazias | sim (3 linhas) |
| Toda linha no padrão `categoria: motivo`, categoria entre as seis | sim |
| Nenhum texto antes ou depois da lista | sim (consequência da verificação anterior) |

O prompt vago não pede formato, então não há obediência a avaliar nele. Observações
descritivas: a resposta ao vago tem dois parágrafos; a resposta ao claro tem três linhas.

## Limitações

- **Experimento 1:** 3 chamadas são exploratórias, não uma medida robusta de variação.
  Com outras chamadas o número de textos distintos poderia ser diferente.
- **Experimento 2:** 3 chamadas, também exploratório. Investiga transmissão de contexto
  entre chamadas, não memória em sentido geral. A checagem do nome é uma busca exata de
  texto: não detecta uma forma abreviada ou escrita de outro modo.
- **Experimento 3:** um único exemplo, portanto anedótico. Nenhuma resposta é declarada
  melhor fora dos critérios do README.
- **O prompt claro difere do vago em mais do que "clareza". Diferenças que mudam junto:**
  1. **Categorias:** o claro nomeia as seis categorias do projeto; o vago não menciona nenhuma.
  2. **Formato:** o claro exige uma linha por problema no padrão `categoria: motivo`; o vago não pede formato.
  3. **Limite de linhas:** o claro limita a 5 linhas e proíbe texto antes ou depois; o vago só pede "seja breve".
  4. Além dessas três, a **tarefa pedida também é diferente**: o vago pergunta o que se acha da avaliação (um julgamento sobre a avaliação), o claro pede a lista de problemas relatados. Por isso as duas respostas não respondem exatamente à mesma pergunta, e o experimento não isola qual dessas diferenças pesa.
- O gabarito do experimento 3 foi escrito por mim (assistente) antes da execução, a partir das
  categorias do projeto; a avaliação sintética também foi escrita por mim, com os problemas
  inseridos de forma explícita. É um caso fácil, sem ambiguidade.
- Os tokens de saída informados pela API podem incluir raciocínio interno não devolvido
  como bloco; as respostas tiveram só blocos `text`, e este registro não permite distinguir.
- Uma execução por condição, sem repetições, em um modelo e uma data. Os resultados não
  generalizam para outros modelos, parâmetros ou textos.

## Comando para rodar tudo

Pré-requisito: `.env` na raiz com `ANTHROPIC_API_KEY`. Cada experimento recusa rodar se a
saída já existe em `experimentos/saidas/` (apague-a manualmente; isso implica nova chamada paga).

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r experimentos/requirements.txt
python experimentos/rodar_tudo.py
```
