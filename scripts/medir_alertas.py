"""Mede os alertas gravados no banco (blocos 2.4 e 2.8). Dados SIMULADOS.

Conta os alertas por estado e os tardios e, para os `enviado` do envio inicial (`tardio` = 0), mostra
mínimo, mediana e máximo de enviado_em - criado_em, em segundos: é o tempo do critério 5. Os tardios
(enviados por reenvio ou reprocessamento) ficam fora. O banco é aberto SOMENTE para leitura. Não
imprime token nem texto de avaliação.

Um banco criado antes da coluna `tardio` não é migrado aqui: todos os alertas contam como `tardio` = 0
(antes da coluna, todo envio era inicial) e a saída avisa uma vez.

Uso: python scripts/medir_alertas.py [--banco caminho.db]   (padrão: variável RADAR_DB)
"""

import argparse
import os
import sqlite3
import statistics
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ESTADOS = ("pendente", "enviado", "falhou")


def abrir_somente_leitura(caminho) -> sqlite3.Connection:
    """Conexão mode=ro: não cria o arquivo e recusa qualquer escrita."""
    return sqlite3.connect(f"{Path(caminho).resolve().as_uri()}?mode=ro", uri=True)


def medir(caminho) -> dict:
    with closing(abrir_somente_leitura(caminho)) as conn:
        tem_tardio = "tardio" in {linha[1] for linha in conn.execute("PRAGMA table_info(alertas)")}
        tardio = "tardio" if tem_tardio else "0"
        contagem = dict(conn.execute("SELECT estado, COUNT(*) FROM alertas GROUP BY estado"))
        tardios = conn.execute(f"SELECT COUNT(*) FROM alertas WHERE {tardio} = 1").fetchone()[0]
        enviados = conn.execute(
            "SELECT criado_em, enviado_em FROM alertas "
            f"WHERE estado = 'enviado' AND enviado_em IS NOT NULL AND {tardio} = 0"
        ).fetchall()
    estados = {e: contagem.get(e, 0) for e in ESTADOS}
    segundos = [
        (datetime.fromisoformat(fim) - datetime.fromisoformat(ini)).total_seconds()
        for ini, fim in enviados
    ]
    tempos = None
    if segundos:
        tempos = {
            "n": len(segundos),
            "minimo": min(segundos),
            "mediana": statistics.median(segundos),
            "maximo": max(segundos),
        }
    return {"estados": estados, "tardios": tardios, "tempos": tempos, "sem_coluna_tardio": not tem_tardio}


def imprimir(resultado: dict):
    print("Medição dos alertas (dados simulados)")
    if resultado["sem_coluna_tardio"]:
        print("Aviso: banco sem a coluna tardio; todos os alertas contam como envio inicial (tardio = 0).")
    print("Atenção: o tempo inclui a classificação, porque criado_em é a entrada no endpoint")
    print("(enviado_em - criado_em = classificação + gravação + envio ao Telegram; em alertas")
    print("com nova tentativa, o tempo inclui também o reenvio).")
    print("Só entram os alertas do envio inicial (tardio = 0); os tardios ficam fora do critério 5.")
    print("Alertas por estado:")
    for estado, n in resultado["estados"].items():
        print(f"  {estado}: {n}")
    print(f"Alertas tardios (reenvio ou reprocessamento): {resultado['tardios']}")
    tempos = resultado["tempos"]
    if tempos is None:
        print("Sem alertas enviados no envio inicial: não há tempos para medir.")
        return
    print(f"Tempo dos {tempos['n']} alertas enviados no envio inicial (segundos):")
    print(f"  mínimo:  {tempos['minimo']:.1f}")
    print(f"  mediana: {tempos['mediana']:.1f}")
    print(f"  máximo:  {tempos['maximo']:.1f}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Mede os alertas do banco, somente leitura.")
    p.add_argument("--banco", help="caminho do SQLite (padrão: RADAR_DB)")
    args = p.parse_args(argv)
    caminho = args.banco or os.environ.get("RADAR_DB")
    if not caminho:
        print("erro: informe --banco ou defina RADAR_DB.", file=sys.stderr)
        return 2
    if not Path(caminho).is_file():
        print("erro: banco não encontrado no caminho informado.", file=sys.stderr)
        return 2
    try:
        resultado = medir(caminho)
    except (sqlite3.Error, ValueError, TypeError) as e:  # só o tipo; sem traceback
        print(f"erro ao ler o banco ({type(e).__name__}).", file=sys.stderr)
        return 1
    imprimir(resultado)
    return 0


if __name__ == "__main__":
    sys.exit(main())
