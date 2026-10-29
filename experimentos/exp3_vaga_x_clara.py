"""Experimento 3: instrução vaga x instrução clara sobre a mesma avaliação sintética.

O gabarito (problemas existentes na avaliação) está no README, escrito antes da execução
e independente dos dois prompts. Este script só chama o modelo e confere o FORMATO da
resposta clara; a identificação dos problemas é conferida à parte, contra o gabarito.

Dados fictícios. Projeto independente, sem vínculo com o iFood.
"""
import re
import sys

import comum

AVALIACAO = (
    "Nota 2. Pedi uma lasagem e um refrigerante. O pedido chegou mais de uma hora depois "
    "do horário combinado, a lasagem veio fria e o refrigerante não veio na sacola. "
    "O entregador foi educado."
)

CATEGORIAS = ["atraso", "item_faltando", "qualidade_temperatura", "embalagem", "atendimento", "entregador"]

PROMPT_VAGO = f"O que você acha desta avaliação de restaurante? Seja breve.\n\nAvaliação: {AVALIACAO}"

PROMPT_CLARO = (
    "Liste os problemas relatados nesta avaliação de restaurante. "
    f"Use somente estas categorias: {', '.join(CATEGORIAS)}. "
    "Formato: uma linha por problema, no padrão 'categoria: motivo em poucas palavras'. "
    "No máximo 5 linhas, sem texto antes ou depois. Se não houver problema, escreva 'nenhum'.\n\n"
    f"Avaliação: {AVALIACAO}"
)

MAX_LINHAS = 5


def checar_formato_claro(texto):
    """Obediência ao formato pedido no prompt claro (critérios objetivos).

    Aplica-se só ao prompt claro: o vago não pede formato nenhum.
    """
    linhas = [l for l in texto.split("\n") if l.strip()]
    padrao = re.compile(rf"^({'|'.join(CATEGORIAS)}): \S")
    return {
        "linhas_nao_vazias": len(linhas),
        "ate_5_linhas": len(linhas) <= MAX_LINHAS,
        "todas_no_padrao_categoria_dois_pontos_motivo": bool(linhas) and all(padrao.match(l) for l in linhas),
    }


def main():
    caminho = comum.preparar_saida("exp3")
    cliente = comum.criar_cliente()

    registros = []
    for rotulo, prompt in (("exp3-vaga", PROMPT_VAGO), ("exp3-clara", PROMPT_CLARO)):
        registro = comum.chamar(cliente, rotulo, [{"role": "user", "content": prompt}])
        registros.append(registro)
        if not registro["valido"]:
            break

    saida = {"avaliacao": AVALIACAO, "prompt_vago": PROMPT_VAGO, "prompt_claro": PROMPT_CLARO, "chamadas": registros}
    completo = len(registros) == 2 and all(r["valido"] for r in registros)
    if completo:
        saida["formato_prompt_claro"] = checar_formato_claro(registros[1]["texto"])
        print(f"Formato (prompt claro): {saida['formato_prompt_claro']}")
    else:
        comum.exigir_validas(registros)
        saida["observacao"] = "Experimento incompleto: não conta como resultado válido."

    comum.salvar(caminho, saida)
    return 0 if completo else 1


if __name__ == "__main__":
    sys.exit(main())
