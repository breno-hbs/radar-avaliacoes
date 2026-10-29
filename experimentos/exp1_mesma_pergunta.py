"""Experimento 1: a mesma pergunta em 3 chamadas independentes.

Dados fictícios. Projeto independente, sem vínculo com o iFood.
"""
import sys

import comum

PERGUNTA = (
    "Em até duas frases curtas: por que um restaurante deve ler as avaliações "
    "dos clientes toda semana?"
)
CHAMADAS = 3


def main():
    caminho = comum.preparar_saida("exp1")
    cliente = comum.criar_cliente()

    registros = []
    for i in range(1, CHAMADAS + 1):
        # Cada chamada tem uma lista de mensagens nova, com só a pergunta.
        registro = comum.chamar(cliente, f"exp1-chamada-{i}", [{"role": "user", "content": PERGUNTA}])
        registros.append(registro)
        if not registro["valido"]:
            break  # não segue e não repete

    saida = {"pergunta": PERGUNTA, "chamadas": registros}
    todas_validas = len(registros) == CHAMADAS and all(r["valido"] for r in registros)
    if todas_validas:
        # Comparação exata: sem strip, lower ou qualquer normalização.
        textos = [r["texto"] for r in registros]
        saida["textos_distintos_comparacao_exata"] = len(set(textos))
        print(f"Textos distintos (comparação exata): {len(set(textos))} de {CHAMADAS}")
    else:
        comum.exigir_validas(registros)  # imprime o motivo
        saida["observacao"] = "Experimento incompleto: não conta como resultado válido."

    comum.salvar(caminho, saida)
    return 0 if todas_validas else 1


if __name__ == "__main__":
    sys.exit(main())
