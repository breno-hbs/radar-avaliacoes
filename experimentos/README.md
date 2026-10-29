# Experimentos do Post 1

Três experimentos pequenos para observar, com resultados reproduzíveis, como contexto,
variação de respostas e clareza da instrução influenciam um modelo de IA.
Dados fictícios. Projeto independente, sem vínculo com o iFood.

Este README foi escrito **antes** de qualquer chamada paga. Título, gancho e conclusão do
post não foram escolhidos e só serão definidos depois dos resultados.

## Configuração fixa (as 8 chamadas)

| Item | Valor |
|---|---|
| Provedor / modelo | Anthropic / `claude-sonnet-5-5` |
| Cliente | `anthropic.Anthropic(max_retries=0)` |
| `max_tokens` | 400 |
| `thinking` | `{"type": "between_tools"}` |
| `output_config` | `{"effort": "medium"}` |
| `temperature`, `top_p`, `top_k` | **omitidos** (o modelo os rejeita); nenhum valor padrão é presumido |

Total planejado: **8 chamadas** (3 + 3 + 2). Nenhuma é repetida automaticamente.

## Regras de validade

- Erro de API, resposta cortada (`stop_reason` diferente de `end_turn`) ou sem bloco de texto
  **não conta como resultado válido** e não é repetida sozinha.
- Erro 400: o experimento para, sem nova tentativa nem troca de parâmetros.
- Só blocos de texto entram na comparação do experimento 1 e na avaliação do experimento 3.
  O tipo de cada bloco é registrado, incluindo se apareceu bloco de pensamento.
- Erros gravam só tipo, status e identificador da requisição; nunca a mensagem bruta nem cabeçalhos.
- A chave fica só no `.env` (fora do Git) e nunca é exibida nem gravada.

## Experimento 1: mesma pergunta, três chamadas

Pergunta curta sobre avaliações de restaurante, enviada 3 vezes em chamadas independentes
(mesmo modelo, mesmos parâmetros, mesmo contexto: uma única mensagem, sem histórico).
Os textos são comparados por **igualdade exata**, sem strip, lower ou outra normalização.

Limitação declarada: 3 chamadas são exploratórias, não uma medida robusta de variação.

## Experimento 2: transmissão de contexto (3 chamadas)

Nome fictício: **Cantina Lua de Papel**.

1. Chamada inicial informa o nome.
2. **Sem histórico:** chamada independente que pergunta o nome. A API de mensagens da
   Anthropic não guarda estado entre chamadas; o código usa uma lista de mensagens nova só
   com a pergunta, sem sessão, thread, `previous_response_id`, objeto de chat ou qualquer
   identificador de contexto, e um `assert` confere que o nome não está nessa lista.
3. **Com histórico:** o contexto anterior é enviado explicitamente na lista de mensagens
   (mensagem da chamada 1, texto da resposta 1, nova pergunta).

O teste investiga **transmissão de contexto**, não memória em sentido geral.
São 3 chamadas, portanto exploratório.

## Experimento 3: instrução vaga × clara

### Avaliação sintética (igual nas duas condições)

> Nota 2. Pedi uma lasagem e um refrigerante. O pedido chegou mais de uma hora depois do
> horário combinado, a lasagem veio fria e o refrigerante não veio na sacola.
> O entregador foi educado.

### Gabarito (escrito antes da execução)

Categorias do projeto (`docs/GUIA_ROTULACAO.md` v1.1). Problemas existentes na avaliação:

| # | Problema | Categoria | Trecho que o sustenta |
|---|---|---|---|
| P1 | Entrega muito depois do combinado | `atraso` | "mais de uma hora depois do horário combinado" |
| P2 | Prato chegou frio | `qualidade_temperatura` | "a lasagem veio fria" |
| P3 | Item pedido não veio | `item_faltando` | "o refrigerante não veio na sacola" |

Não são problemas: o entregador foi educado (elogio, não deve virar `entregador` como
problema). Não há problema de `embalagem` nem de `atendimento`. A avaliação não tem
relato crítico (saúde, alergia, cobrança indevida).

### Critérios objetivos (idênticos para as duas respostas)

Para cada problema P1 a P3: a resposta **identificou** o problema se afirma o fato
correspondente, em qualquer formulação, sem exigir o nome da categoria. Conta como não
identificado se omite o fato ou o contradiz. Registro por resposta: P1, P2, P3 como
sim/não, total de 0 a 3, e se apontou como problema algo que o gabarito exclui
(por exemplo, o entregador). A conferência é feita por leitura contra esta tabela, não por
palavra-chave.

### Obediência ao formato (observação à parte)

Só se aplica ao prompt claro, o único que pede formato. Há 2 verificações objetivas,
automáticas, em `exp3_vaga_x_clara.py`:

- no máximo 5 linhas não vazias;
- toda linha no padrão `categoria: motivo`, com a categoria entre as seis do projeto.

A terceira exigência do prompt, nenhum texto antes ou depois da lista, decorre da segunda:
se toda linha não vazia está no padrão, não há texto extra.

A resposta ao prompt vago não é avaliada quanto a formato, porque nenhum foi pedido.
Formato e identificação dos problemas são reportados separadamente.

### Limitação declarada

É um único exemplo, portanto **anedótico**. Nenhuma resposta é declarada "melhor" fora dos
critérios acima.

## Como rodar

Pré-requisito: `.env` na raiz com `ANTHROPIC_API_KEY`. Cada experimento recusa rodar se a
saída em `experimentos/saidas/` já existe, para evitar chamada paga repetida sem querer.

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r experimentos/requirements.txt
python experimentos/rodar_tudo.py
```

Ou um por vez: `python experimentos/exp1_mesma_pergunta.py` (idem `exp2_contexto.py` e
`exp3_vaga_x_clara.py`). Após uma falha parcial, rode os scripts um a um, porque o
`rodar_tudo.py` para no primeiro experimento que já tem saída. As respostas completas ficam em
`experimentos/saidas/*.json`.
