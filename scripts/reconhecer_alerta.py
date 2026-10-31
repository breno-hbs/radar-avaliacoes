"""Reconhece um alerta `falhou` (bloco 2.8). Dados SIMULADOS. Só roda localmente.

Fonte da verdade: docs/ESPECIFICACAO.md (decisão de 31/10/2026 sobre a recuperação de alertas,
"Reconhecimento"). Reconhecer registra que o dono verificou o relato por fora: grava `reconhecido_em`
com o instante atual (UTC) e NÃO muda o estado, que continua `falhou`. Um alerta reconhecido deixa de
ser elegível ao reenvio. A saída dele de `alertas_falhos` no resumo semanal é do bloco seguinte (2.9).

- Só aceita alerta em `falhou`; os outros estados são recusados com código de saída diferente de zero.
- É idempotente: se já foi reconhecido, não sobrescreve o primeiro instante.
- O banco é o de RADAR_DB, aberto por app/banco.abrir (que aplica a migração das colunas novas).

Uso: python scripts/reconhecer_alerta.py --id 12
     python scripts/reconhecer_alerta.py --id-externo crit-004
"""

import argparse
import os
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import banco  # noqa: E402

NAO_ENCONTRADO = 1
ESTADO_RECUSADO = 3
SEM_BANCO = 2


def ler_argumentos(argv=None):
    p = argparse.ArgumentParser(description="Reconhece um alerta falhou (grava reconhecido_em).")
    grupo = p.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--id", type=int, help="id do alerta")
    grupo.add_argument("--id-externo", help="id_externo da avaliação do alerta")
    return p.parse_args(argv)


def main(argv=None, *, agora=None) -> int:
    args = ler_argumentos(argv)
    caminho = os.environ.get("RADAR_DB")
    if not caminho:
        print("erro: defina RADAR_DB com o caminho do banco.", file=sys.stderr)
        return SEM_BANCO
    if not os.path.isfile(caminho):
        print("erro: RADAR_DB aponta para um arquivo que não existe.", file=sys.stderr)
        return SEM_BANCO
    instante = (agora or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()

    with closing(banco.abrir(caminho)) as conn:
        if args.id is not None:
            linha = conn.execute(
                "SELECT a.id, a.estado, a.reconhecido_em, v.id_externo FROM alertas a "
                "JOIN avaliacoes v ON v.id = a.avaliacao_id WHERE a.id = ?", (args.id,),
            ).fetchone()
            descricao = f"alerta {args.id}"
        else:
            linha = conn.execute(
                "SELECT a.id, a.estado, a.reconhecido_em, v.id_externo FROM alertas a "
                "JOIN avaliacoes v ON v.id = a.avaliacao_id WHERE v.id_externo = ?", (args.id_externo,),
            ).fetchone()
            descricao = f"alerta da avaliação {args.id_externo}"
        if linha is None:
            print(f"erro: {descricao} não encontrado.", file=sys.stderr)
            return NAO_ENCONTRADO
        if linha["estado"] != "falhou":
            print(
                f"erro: o alerta {linha['id']} está em {linha['estado']}; só um alerta em falhou pode ser "
                "reconhecido.",
                file=sys.stderr,
            )
            return ESTADO_RECUSADO
        if linha["reconhecido_em"] is not None:
            print(f"alerta {linha['id']} já reconhecido em {linha['reconhecido_em']}; nada mudou.")
            return 0
        # Condicionado: se outra execução reconheceu entre a leitura e aqui, o primeiro instante fica.
        cur = conn.execute(
            "UPDATE alertas SET reconhecido_em = ? WHERE id = ? AND estado = 'falhou' AND reconhecido_em IS NULL",
            (instante, linha["id"]),
        )
        if cur.rowcount == 0:
            atual = conn.execute("SELECT reconhecido_em FROM alertas WHERE id = ?", (linha["id"],)).fetchone()
            print(f"alerta {linha['id']} já reconhecido em {atual['reconhecido_em']}; nada mudou.")
            return 0
    print(f"alerta {linha['id']} (avaliação {linha['id_externo']}) reconhecido em {instante}; continua falhou.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
