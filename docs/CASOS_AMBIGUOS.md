# Casos ambíguos

Registre aqui apenas casos com ambiguidade real de interpretação. Uma avaliação sem conteúdo
suficiente para atribuir uma categoria não é, por si só, um caso ambíguo.

Todo caso aqui deve ser resolvido, com a regra atualizada em docs/GUIA_ROTULACAO.md, antes de usar
os conjuntos de avaliação (50 gerais e 15 críticos).

| id_externo | Texto | Dúvida | Decisão | Regra afetada | Data |
|---|---|---|---|---|---|
| ger-050 | "colocaram o pote da sopa numa sacola de papel que ficou toda mole com a umidade. fiquei com medo de rasgar quando levantei, o pote tava intacto" | A perda de firmeza da sacola por umidade já é problema de embalagem, ou apenas risco percebido? O cliente não relata rompimento, vazamento nem dano. | categorias = []; fora_do_escopo = false | Guia, seção embalagem ("Ambiguidade": sacola molhada, sem vazamento ou dano → sem categoria). Resolvido pela regra existente; o guia não foi alterado. | 2026-10-28 |
| crit-003 | "o pastel tava bem servido, mas minha mãe comeu um pedaço e começou a vomitar logo depois. fiquei preocupado" | A marcação `saude` se aplica quando quem teve os sintomas não é o autor da avaliação? | criticidade = ["saude"] | Guia, critérios de `saude` ("relato de mal-estar após consumo"). O guia não limita a marcação a quem escreveu a avaliação. Resolvido pela regra existente; o guia não foi alterado. | 2026-10-28 |
| crit-007 | "comi um pedaço da sobremesa e comecei a ficar com placas vermelhas na pele e muita coceira. sou alérgico a pistache e depois vi que tinha no recheio" | Reação alérgica recebe também `saude`, ou apenas `alergia`? O guia v1.0 não dizia se as marcações se acumulam nesse caso. | criticidade = ["alergia"] | Guia v1.1: regra de acúmulo na seção "Marcação crítica". Definição de critério antes de qualquer execução do classificador. Guia alterado. | 2026-10-28 |
| crit-015 | "veio sem a coca que fazia parte do combo. a loja reconheceu e disse que devolveria o valor da bebida até ontem, mas não caiu nada e continuo pagando por ela" | Estorno prometido e não realizado é `cobranca_indevida` ou apenas um problema de processo de reembolso (fora do escopo)? | categorias = ["item_faltando"]; criticidade = ["cobranca_indevida"]; fora_do_escopo = false | Guia, `cobranca_indevida` ("cobrança relatada como incorreta"). O texto afirma que o cliente continua pagando por item que a loja reconheceu não entregue. Resolvido pela regra existente; o guia não foi alterado. | 2026-10-28 |
