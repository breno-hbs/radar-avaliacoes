"""Reprocessa as avaliações `invalida` (bloco 2.9). Dados SIMULADOS. Manual, só local.

Fonte da verdade: docs/ESPECIFICACAO.md (decisão de 31/10/2026 sobre o reprocessamento das avaliações
`invalida`). Cada avaliação `invalida`, em ordem de id, é reclassificada com o classificador e o prompt
congelados e a regra de retentativa congelada (app/classificador.py, sem alteração).

- Sucesso: numa única transação, a avaliação vira `ok` com categorias e criticidade e, se houver
  marcação crítica, nasce o alerta `pendente` com `tardio` = 1 e `criado_em` = agora (UTC). Depois da
  transação, o script envia o alerta UMA vez (não é reenvio: sem a linha de criação).
- Saída inválida de novo: nada muda.
- Falha técnica esgotada: pula a avaliação; 3 seguidas param o script (código de saída 4).
- Erro permanente da API (chave inválida etc.): para na hora (código de saída 3).

RADAR_DB e ANTHROPIC_API_KEY vêm do ambiente (a chave também do .env, como no classificador). Nada de
segredo nem de texto de avaliação na saída: só ids e contagens.

Atenção: contra a API real, isto reclassifica avaliações com o modelo e pode disparar alertas reais.

Uso: python scripts/reprocessar_invalidas.py --dry-run
     python scripts/reprocessar_invalidas.py [--limite N]
"""

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import alertas, banco, classificador, telegram  # noqa: E402
from app.classificador import ErroPermanenteDaAPI, ErroTecnicoEsgotado  # noqa: E402

VERSAO_PROMPT = "v1"  # o mesmo prompt congelado do app (app/main.py, VERSAO_PROMPT)
MAX_FALHAS_TECNICAS_SEGUIDAS = 3

SEM_CONFIGURACAO = 2
ERRO_PERMANENTE = 3
FALHAS_SEGUIDAS = 4

_PADRAO = object()


def _agora_utc():
    return datetime.now(timezone.utc)


def _inteiro_positivo(texto):
    try:
        valor = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError("deve ser um inteiro") from None
    if valor < 1:
        raise argparse.ArgumentTypeError("deve ser pelo menos 1")
    return valor


def ler_argumentos(argv=None):
    p = argparse.ArgumentParser(description="Reprocessa as avaliações invalida com o classificador congelado.")
    p.add_argument("--dry-run", action="store_true", help="lista o que seria reprocessado, sem chamar a API")
    p.add_argument("--limite", type=_inteiro_positivo, help="reprocessa no máximo N avaliações")
    return p.parse_args(argv)


def _classificador_padrao():
    """Cliente e prompt reais. criar_cliente encerra (SystemExit) se a chave faltar."""
    cliente = classificador.criar_cliente()
    prompt = classificador.carregar_prompt(VERSAO_PROMPT)
    return lambda texto: classificador.classificar(texto, cliente, prompt)


def main(argv=None, *, classificar=None, notificador=_PADRAO, relogio=None) -> int:
    args = ler_argumentos(argv)
    caminho = os.environ.get("RADAR_DB")
    if not caminho:
        print("erro: defina RADAR_DB com o caminho do banco.", file=sys.stderr)
        return SEM_CONFIGURACAO
    if not os.path.isfile(caminho):
        print("erro: RADAR_DB aponta para um arquivo que não existe.", file=sys.stderr)
        return SEM_CONFIGURACAO
    relogio = relogio or _agora_utc

    # Somente leitura para listar: o dry-run não pode alterar o banco (banco.abrir migraria um banco antigo).
    with closing(sqlite3.connect(f"{Path(caminho).resolve().as_uri()}?mode=ro", uri=True)) as conn:
        consulta = "SELECT id, texto FROM avaliacoes WHERE status_classificacao = 'invalida' ORDER BY id"
        parametros = ()
        if args.limite:
            consulta += " LIMIT ?"
            parametros = (args.limite,)
        pendentes = list(conn.execute(consulta, parametros))

    if args.dry_run:
        print("Dry-run: nada será reclassificado nem gravado. Dados simulados.")
        print(f"{len(pendentes)} avaliações seriam reprocessadas (ids: "
              f"{', '.join(str(i) for i, _ in pendentes) or 'nenhuma'}).")
        print(f"Custo estimado: até {len(pendentes) * classificador.MAX_TENTATIVAS} chamadas à API "
              f"({classificador.MAX_TENTATIVAS} por avaliação na regra de retentativa).")
        return 0

    if classificar is None and pendentes:
        try:
            classificar = _classificador_padrao()
        except SystemExit as e:  # chave da Anthropic ausente: a mensagem não traz o valor
            print(f"erro: {e.code}", file=sys.stderr)
            return SEM_CONFIGURACAO
    if notificador is _PADRAO:
        notificador = telegram.notificador_do_ambiente()

    contagem = {"reprocessadas": 0, "voltaram a invalida": 0, "falhas técnicas": 0, "erros de banco": 0,
                "alertas criados": 0, "alertas enviados": 0, "alertas que falharam": 0,
                "alertas pendentes (Telegram não configurado)": 0}
    seguidas = 0
    codigo = 0
    for avaliacao_id, texto in pendentes:
        falha_tecnica = None
        try:
            resultado = classificar(texto)
        except ErroPermanenteDaAPI as e:
            print(f"erro permanente da API (status {e.status}, request_id {e.request_id}): parado.",
                  file=sys.stderr)
            codigo = ERRO_PERMANENTE
            break
        except ErroTecnicoEsgotado as e:
            falha_tecnica = f"{e.tipo_excecao}, status {e.status}"
        except Exception as e:  # noqa: BLE001 - inesperado: conta como técnica, só o tipo (sem a mensagem)
            falha_tecnica = type(e).__name__
        if falha_tecnica is not None:
            contagem["falhas técnicas"] += 1
            seguidas += 1
            print(f"avaliação {avaliacao_id}: falha técnica ({falha_tecnica}); pulada.")
            if seguidas >= MAX_FALHAS_TECNICAS_SEGUIDAS:
                print(f"PARADO: {MAX_FALHAS_TECNICAS_SEGUIDAS} falhas técnicas seguidas; confira a rede e a API.",
                      file=sys.stderr)
                codigo = FALHAS_SEGUIDAS
                break
            continue
        seguidas = 0  # a API respondeu (com saída válida ou inválida)
        saida = resultado["saida"]
        if saida is None:
            contagem["voltaram a invalida"] += 1
            continue
        try:
            with closing(banco.abrir(caminho)) as conn:
                alerta_id = banco.reprocessar_avaliacao(
                    conn, avaliacao_id, categorias=saida["categorias"], criticidade=saida["criticidade"],
                    alerta_criado_em=relogio().isoformat(),
                )
        except (sqlite3.Error, LookupError, ValueError) as e:  # a transação foi desfeita: nada gravado
            contagem["erros de banco"] += 1
            print(f"avaliação {avaliacao_id}: não gravada ({type(e).__name__}); continua invalida.")
            continue
        contagem["reprocessadas"] += 1
        if alerta_id is None:
            continue
        contagem["alertas criados"] += 1
        mensagem = alertas.montar_mensagem(avaliacao_id, saida["criticidade"], texto)
        situacao = alertas.enviar_alerta_reprocessado(caminho, alerta_id, mensagem, notificador, relogio)
        if situacao == "enviado":
            contagem["alertas enviados"] += 1
        elif situacao == "falhou":
            contagem["alertas que falharam"] += 1
        elif notificador is None:
            contagem["alertas pendentes (Telegram não configurado)"] += 1

    print("Resumo do reprocessamento (dados simulados)")
    for campo, valor in contagem.items():
        print(f"  {campo}: {valor}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
