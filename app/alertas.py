"""Envio do alerta crítico (bloco 2.3).

Fonte da verdade: docs/ESPECIFICACAO.md ("Ordem do alerta" e a decisão de 31/10/2026 sobre o envio).
O alerta já foi gravado como `pendente` na mesma transação da avaliação; aqui ele é enviado em tarefa
de fundo, com uma nova tentativa se a primeira falhar, e o estado é atualizado no banco.
"""

import logging
from contextlib import closing

from app import banco

MAX_TENTATIVAS = 2  # a primeira e UMA nova tentativa
LIMITE_TEXTO = 300

logger = logging.getLogger("radar.alertas")


def montar_mensagem(avaliacao_id: int, tipos: list, texto: str) -> str:
    """Texto puro, uma mensagem por avaliação, com todos os tipos como "relato a verificar"."""
    if not texto:
        trecho = "(sem texto)"
    elif len(texto) > LIMITE_TEXTO:
        trecho = texto[: LIMITE_TEXTO - 1] + "…"
    else:
        trecho = texto
    linhas = [
        "🔴 Alerta crítico: relato a verificar",
        f"Avaliação {avaliacao_id}",
        "Tipos identificados:",
        *(f"- {tipo}: relato a verificar" for tipo in tipos),
        "Texto da avaliação:",
        trecho,
    ]
    return "\n".join(linhas)


def enviar_alerta(caminho_banco, alerta_id: int, mensagem: str, notificador, relogio) -> None:
    """Tarefa de fundo. Nunca levanta exceção: falha vira estado no banco e registro no log.

    Abre a própria conexão SQLite (a da requisição já foi fechada) e a fecha ao terminar. Os logs
    levam só o alerta_id e o tipo da falha, nunca a mensagem da exceção nem a URL do Telegram.
    """
    if notificador is None:
        logger.warning(
            "Telegram não configurado (RADAR_TELEGRAM_TOKEN ou RADAR_TELEGRAM_CHAT_ID ausente): "
            "alerta %s fica pendente",
            alerta_id,
        )
        return
    try:
        with closing(banco.abrir(caminho_banco)) as conn:
            for tentativa in range(1, MAX_TENTATIVAS + 1):
                banco.mudar_estado_alerta(conn, alerta_id, "pendente", incrementar_tentativas=True)
                try:
                    notificador(mensagem)
                except Exception as e:  # noqa: BLE001 - qualquer erro conta como falha
                    logger.warning(
                        "falha no envio do alerta %s ao Telegram (tentativa %s de %s, %s)",
                        alerta_id, tentativa, MAX_TENTATIVAS, getattr(e, "motivo", type(e).__name__),
                    )
                    continue
                try:
                    banco.mudar_estado_alerta(
                        conn, alerta_id, "enviado", enviado_em=relogio().isoformat()
                    )
                except Exception as e:  # noqa: BLE001 - a mensagem já saiu: não tentar de novo
                    logger.error(
                        "alerta %s entregue ao Telegram, mas o estado enviado não foi gravado (%s); "
                        "ele continua pendente no banco",
                        alerta_id, type(e).__name__,
                    )
                return
            banco.mudar_estado_alerta(conn, alerta_id, "falhou")
            logger.error("alerta %s ficou falhou depois de %s tentativas", alerta_id, MAX_TENTATIVAS)
    except Exception as e:  # noqa: BLE001 - a tarefa de fundo não pode derrubar o app
        logger.error("falha ao processar o envio do alerta %s (%s)", alerta_id, type(e).__name__)
