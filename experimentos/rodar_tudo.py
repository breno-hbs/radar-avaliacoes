"""Roda os três experimentos em sequência (8 chamadas pagas). Para no primeiro que falhar.

Dados fictícios. Projeto independente, sem vínculo com o iFood.
"""
import sys

import exp1_mesma_pergunta
import exp2_contexto
import exp3_vaga_x_clara

for modulo in (exp1_mesma_pergunta, exp2_contexto, exp3_vaga_x_clara):
    if modulo.main() != 0:
        sys.exit(f"{modulo.__name__} não terminou com resultado válido. Nada foi repetido.")
