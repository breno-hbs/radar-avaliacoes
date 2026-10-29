"""Experimento 2: transmissão de contexto (sequência de 3 chamadas).

Investiga se o modelo responde com um dado informado antes quando esse dado NÃO é
reenviado e quando É reenviado. Não investiga memória em sentido geral.

Dados fictícios. Projeto independente, sem vínculo com o iFood.
"""
import json
import sys

import comum

NOME_FICTICIO = "Cantina Lua de Papel"
INFORMA = (
    f"O nome (fictício) do meu restaurante é {NOME_FICTICIO}. "
    "Responda só com uma frase curta confirmando."
)
PERGUNTA = "Qual é o nome do meu restaurante? Responda em uma frase curta."


def main():
    caminho = comum.preparar_saida("exp2")
    cliente = comum.criar_cliente()
    registros = []
    saida = {"nome_ficticio": NOME_FICTICIO, "chamadas": registros}

    # Chamada 1: informa o nome.
    msg_informa = {"role": "user", "content": INFORMA}
    r1 = comum.chamar(cliente, "exp2-1-informa", [msg_informa])
    registros.append(r1)

    r2 = r3 = None
    if r1["valido"]:
        # Chamada 2 (sem histórico): lista de mensagens nova, só com a pergunta.
        # A API de mensagens não guarda estado entre chamadas; nenhuma sessão, thread,
        # previous_response_id, objeto de chat ou identificador de contexto é usado.
        msgs2 = [{"role": "user", "content": PERGUNTA}]
        assert NOME_FICTICIO not in json.dumps(msgs2, ensure_ascii=False)
        r2 = comum.chamar(cliente, "exp2-2-sem-historico", msgs2)
        registros.append(r2)

    if r2 and r2["valido"]:
        # Chamada 3 (com histórico): o contexto anterior é reenviado explicitamente,
        # como turnos da lista de mensagens (pergunta 1 + resposta 1 + pergunta nova).
        msgs3 = [
            msg_informa,
            {"role": "assistant", "content": r1["texto"]},
            {"role": "user", "content": PERGUNTA},
        ]
        r3 = comum.chamar(cliente, "exp2-3-com-historico", msgs3)
        registros.append(r3)

    completo = len(registros) == 3 and all(r["valido"] for r in registros)
    if completo:
        saida["sem_historico_citou_o_nome"] = NOME_FICTICIO in r2["texto"]
        saida["com_historico_citou_o_nome"] = NOME_FICTICIO in r3["texto"]
        print(f"Sem histórico citou o nome: {saida['sem_historico_citou_o_nome']}")
        print(f"Com histórico citou o nome: {saida['com_historico_citou_o_nome']}")
    else:
        comum.exigir_validas(registros)
        saida["observacao"] = "Experimento incompleto: não conta como resultado válido."

    comum.salvar(caminho, saida)
    return 0 if completo else 1


if __name__ == "__main__":
    sys.exit(main())
