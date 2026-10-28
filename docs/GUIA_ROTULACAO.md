# Guia de rotulação — Radar de Avaliações

Dados simulados. Projeto independente, sem vínculo com o iFood.
A versão 1.0 foi congelada antes de rotular os conjuntos de avaliação.
Versão 1.1 (2026-10-28): acrescenta a regra de acúmulo entre `alergia` e `saude` (seção "Marcação
crítica"), surgida ao rotular o crit-007. É uma definição de critério feita antes de qualquer execução
do classificador, não um ajuste motivado por previsões.

## Princípio central

Classifique o problema explicitamente descrito, não o responsável presumido nem a causa
imaginada. A classificação identifica o relato; a priorização avalia sua importância;
o diagnóstico investiga possíveis causas.

## Regras gerais

1. Rotule o que o texto afirma. Não invente informação ausente.
2. O conteúdo manda, não a nota. Avaliação sem relato de problema tem zero categorias,
   mesmo com nota baixa. Avaliação com nota alta que relate um problema recebe o rótulo.
3. Uma avaliação pode ter zero, um ou vários rótulos.
4. Qualquer relato explícito de um problema recebe o rótulo, mesmo que o cliente esteja satisfeito.
5. Em dúvida entre categorias, aplique as definições, as regras de inclusão e exclusão e a regra
   do objeto. Não use a recomendação operacional para decidir o rótulo. Se a ambiguidade persistir,
   registre o caso em docs/CASOS_AMBIGUOS.md.
6. Todo "Exclui" diz para onde o caso vai.
7. Regra do objeto: o rótulo segue o que foi descrito como problemático (a embalagem ou a comida).
8. Mencionar uma pessoa ou parte não é relatar um problema dela. Quem demorou não define a categoria.
9. Casos ambíguos são resolvidos ANTES de usar os conjuntos de avaliação (50 gerais e 15 críticos).

## Formato do dataset

```json
{
  "id_externo": "dev-001",
  "conjunto": "desenvolvimento",
  "nota": 3,
  "texto": "Meu lanche demorou e chegou frio.",
  "categorias": ["atraso", "qualidade_temperatura"],
  "criticidade": [],
  "fora_do_escopo": false,
  "justificativa": "Relata demora e temperatura inadequada."
}
```

- O classificador recebe só `texto` e devolve previsões. O script compara com `categorias` e `criticidade`.
- `justificativa` e `fora_do_escopo` são só para auditoria. Nunca entram no prompt nem na saída
  do classificador.
- `fora_do_escopo` = true quando a avaliação relata pelo menos um problema que não pertence às seis
  categorias operacionais nem às três marcações críticas, mesmo que outros problemas da mesma
  avaliação sejam classificados. O campo é utilizado exclusivamente para auditoria e não integra
  a entrada nem a saída do classificador. `fora_do_escopo` = true não significa que a avaliação
  não tenha rótulos: significa que pelo menos um problema ficou fora das classificações previstas.
- Avaliação sem problema identificável no texto: categorias vazias e `fora_do_escopo` = false,
  salvo reclamação explícita fora das seis categorias e das marcações críticas. Registrar em
  CASOS_AMBIGUOS.md somente quando existir ambiguidade real de interpretação.

## atraso

Definição: o cliente relata demora, no preparo ou na entrega (o pedido chegou tarde, ainda não
chegou ou nunca chegou).

Inclui:
- "Meu pedido já passou da previsão de entrega"
- "Não entregaram meu lanche ainda"
- "Pedi meu lanche 12:00 e foi chegar somente 13:30"
- "Meu pedido nunca chegou"
- "Demorou um pouco, mas estava ótimo"
- "O entregador demorou 40 minutos"

Exclui: "Veio frio", sem menção de demora → qualidade_temperatura.

Regras: demora de qualquer origem é atraso; conduta do entregador é outra categoria.
"Nunca chegou" é atraso na v1 (limitação registrada no README: não há categoria para entrega que falhou).

## item_faltando

Definição: o cliente relata que um item pedido não veio ou veio errado, inclusive de tamanho,
variante ou composição pedida.

Inclui:
- "Pedi 2 lanches e veio faltando um"
- "Meu pedido veio incompleto"
- "Veio um produto que eu não pedi"
- "Pedi pizza grande e veio média"
- "Faltou guardanapo"
- "Pedi sem cebola e veio com cebola"

Exclui:
- "Veio frio" → qualidade_temperatura
- "A embalagem abriu e o molho vazou" → embalagem
- Brinde ou cortesia declarada ("Recebi um refrigerante de brinde") → sem categoria

Caso com várias categorias: "O pedido veio faltando, o lanche estava frio e ainda atrasaram na entrega"
→ ["item_faltando", "qualidade_temperatura", "atraso"]

## qualidade_temperatura

Definição: o cliente relata problema de sabor, textura, qualidade dos ingredientes, preparo,
frescor, temperatura, porção ou apresentação da comida.

Inclui:
- "Meu hambúrguer chegou frio"
- "A batata estava murcha e sem gosto"
- "A carne veio crua por dentro"
- "A comida estava muito salgada"
- "Não gostei do tempero"
- "Veio muito menos comida do que eu esperava"
- "O hambúrguer chegou todo desmontado"
- "O lanche veio todo amassado"
- "O refrigerante veio quente"
- "O hambúrguer veio morno, mas delicioso"

Exclui:
- "Demorou muito", sem reclamação da comida → atraso
- "Pedi pizza grande e veio média" → item_faltando
- "Pedi sem cebola e veio com cebola" → item_faltando
- "A tampa abriu e o molho vazou" → embalagem
- "A comida chegou quentinha" → sem categoria

Regra de temperatura: se o texto descreve uma temperatura inadequada para aquele alimento, recebe
o rótulo, independentemente da nota. Considere a temperatura esperada (sorvete gelado não é problema).

Caso com várias categorias: "Meu pedido demorou uma hora e a comida chegou fria"
→ ["atraso", "qualidade_temperatura"]

## embalagem

Definição: o cliente relata problema com a embalagem: fechamento, resistência, conservação ou
capacidade de proteger e conter os alimentos.

Inclui:
- "A sacola chegou rasgada"
- "O molho vazou dentro da embalagem"
- "A tampa do copo estava mal fechada"
- "A caixa da pizza veio toda amassada"
- "A embalagem abriu durante a entrega"
- "Não gostei da embalagem"

Exclui:
- "A comida chegou fria", sem problema na embalagem → qualidade_temperatura
- "Faltou a bebida" → item_faltando
- "O entregador foi grosseiro" → entregador
- "A embalagem era simples, mas chegou tudo perfeito" → sem categoria

Regra do objeto: caixa amassada → embalagem; hambúrguer desmontado → qualidade_temperatura;
"A caixa abriu e o lanche ficou destruído" → ambas; "A caixa estava intacta, mas o lanche desmontado"
→ só qualidade_temperatura.

Ambiguidade: "A sacola estava molhada", sem indicar vazamento ou dano → sem categoria, com registro
em CASOS_AMBIGUOS.md.

## atendimento

Definição: o cliente relata problema no tratamento, comunicação ou suporte prestado pelo restaurante.

Inclui:
- "O funcionário foi grosseiro comigo"
- "Mandei mensagem para a loja e ninguém respondeu"
- "Liguei para o restaurante e fui mal atendido"
- "O restaurante ignorou minha reclamação"
- "O restaurante demorou para responder no chat" (é atendimento, não atraso)
- "Péssimo atendimento"

Exclui:
- "O entregador foi mal-educado" → entregador
- "Meu pedido atrasou", sem reclamação de comunicação → atraso
- "O lanche veio errado", sem falha de atendimento → item_faltando
- Reclamação do suporte da plataforma → sem categoria, com `fora_do_escopo` = true

Regra: "Péssimo atendimento" recebe o rótulo mesmo sem responsável identificado. A responsabilidade
é hipótese do diagnóstico.

Caso: "O atendente foi educado, mas meu pedido veio errado" → só item_faltando.

Caso com várias categorias: "O pedido veio errado e o restaurante ignorou minhas mensagens"
→ ["item_faltando", "atendimento"]

## entregador

Definição: o cliente relata problema de conduta ou comportamento do entregador, identificado no texto.

Inclui:
- "O entregador foi muito grosseiro"
- "O motoboy jogou meu pedido no chão"
- "O entregador deixou o pedido na chuva"
- "O entregador discutiu comigo"
- "O motoboy se recusou a entregar no local combinado"
- "O entregador marcou como entregue, mas não entregou" → ["entregador", "atraso"]

Exclui:
- "O entregador demorou 40 minutos" → só atraso (citar o entregador não é relatar conduta)
- "A embalagem abriu durante o transporte" → embalagem
- "O funcionário da loja foi grosseiro" → atendimento

Casos:
- "O motoboy demorou, mas foi muito educado" → atraso
- "O entregador se perdeu e o pedido chegou atrasado" → atraso
- "O entregador não entregou na minha porta" → entregador somente se o texto indicar descumprimento
  do combinado ou comportamento inadequado

Caso com várias categorias: "O entregador foi grosseiro e demorou 40 minutos" → ["entregador", "atraso"]

## Marcação crítica (seção própria, separada das categorias)

A criticidade não compete com as categorias: uma avaliação pode ter as duas, ou ter marcação
crítica sem receber nenhuma das seis categorias operacionais. Toda marcação crítica é tratada
como "relato a verificar", nunca como fato comprovado.

Regra: é crítica a avaliação que relata um possível evento ou risco grave concreto, sem exigir
confirmação de que o restaurante o causou. Negação ou dúvida sem indício concreto não é crítica.

Acúmulo (v1.1): reação alérgica relatada recebe somente `alergia`. A marcação `saude` só é acrescentada
quando o mesmo texto relata outro evento de saúde sem natureza alérgica, como a presença de um objeto
estranho no alimento.

| Marcação | Inclui | Exclui |
|---|---|---|
| `saude` | Relato de mal-estar após consumo, suspeita de intoxicação ou objeto estranho no alimento | Sabor estranho sem relato de dano ou risco concreto; negação explícita de mal-estar |
| `alergia` | Reação alérgica relatada ou presença de alérgeno que representa risco concreto ao cliente | Menção genérica a ingredientes, sem indicação de alergia ou risco |
| `cobranca_indevida` | Cobrança duplicada, valor não autorizado ou cobrança relatada como incorreta | Preço considerado caro, sem alegação de cobrança incorreta |

Exemplos:
- "Comi e passei mal depois" → criticidade: saude
- "Encontrei um pedaço de vidro na comida" → criticidade: saude (risco concreto, mesmo sem lesão)
- "Sou alérgico a amendoim e meu pedido veio com amendoim" → criticidade: alergia
- "Pedi sem amendoim porque sou alérgico, mas veio com amendoim" → categorias: item_faltando; criticidade: alergia
- "Meu pedido atrasou e fui cobrado duas vezes" → categorias: atraso; criticidade: cobranca_indevida
- "Fui cobrado duas vezes" → categorias: []; criticidade: cobranca_indevida; fora_do_escopo: false
- "Fui cobrado duas vezes e o suporte do aplicativo me ignorou" → categorias: []; criticidade: cobranca_indevida; fora_do_escopo: true
- "O suporte do aplicativo não respondeu" → categorias: []; criticidade: []; fora_do_escopo: true
- "Tudo perfeito!" → categorias: []; criticidade: []; fora_do_escopo: false
- "Não passei mal, mas achei o gosto estranho" → não crítica; categorias: qualidade_temperatura
- "Achei muito caro, mas estava gostoso" → não crítica; categorias: []; fora_do_escopo: true

## Pendências para o diagnóstico (fora das regras de rótulo)

- "Nunca chegou": a recomendação deve investigar a não entrega, não só o horário de pico.
- Atendimento: não afirmar que o restaurante é o responsável sem evidência.
- Entregador: sem ação interna; classificar como problema de entrega e registrar ocorrência.
