"""Envio do alerta crítico (bloco 2.3) e recuperação de alertas (bloco 2.8).

Fonte da verdade: docs/ESPECIFICACAO.md ("Estados", "Ordem do alerta" e as decisões de 31/10/2026 sobre
o envio, a mensagem do alerta e a recuperação de alertas).

- Envio inicial (enviar_alerta): o alerta já foi gravado como `pendente` na mesma transação da
  avaliação; ele é enviado em tarefa de fundo, com uma nova tentativa se a primeira falhar. Fica
  `pendente` entre as duas tentativas e nunca toca em `tardio`.
- Reenvio (reenviar_elegiveis): UMA tentativa por alerta elegível. Antes de enviar, o alerta é
  reivindicado por um UPDATE condicionado que soma a tentativa e marca `tardio` = 1; quem não consegue
  reivindicar (outra execução chegou antes) pula o alerta.
"""

import json
import logging
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone

from app import banco, semana

MAX_TENTATIVAS = 2  # envio inicial: a primeira e UMA nova tentativa
MAX_TENTATIVAS_TOTAL = 5  # teto de tentativas, contando as do envio inicial
JANELA_REENVIO = timedelta(minutes=5)  # o reenvio só considera alertas criados há MAIS de 5 minutos
LIMITE_TEXTO = 300
PREFIXO = "(dados simulados)"

logger = logging.getLogger("radar.alertas")


def montar_mensagem(avaliacao_id: int, tipos: list, texto: str, *, criado_em=None) -> str:
    """Texto puro, uma mensagem por avaliação, com todos os tipos como "relato a verificar".

    `criado_em` (ISO com fuso) só é passado no reenvio: acrescenta a linha com o instante de criação do
    alerta, em dd/mm HH:MM no fuso America/Sao_Paulo.
    """
    if not texto:
        trecho = "(sem texto)"
    elif len(texto) > LIMITE_TEXTO:
        trecho = texto[: LIMITE_TEXTO - 1] + "…"
    else:
        trecho = texto
    linhas = [
        PREFIXO,
        "🔴 Alerta crítico: relato a verificar",
        f"Avaliação {avaliacao_id}",
        "Tipos identificados:",
        *(f"- {tipo}: relato a verificar" for tipo in tipos),
        "Texto da avaliação:",
        trecho,
    ]
    if criado_em is not None:
        local = datetime.fromisoformat(criado_em).astimezone(semana.FUSO)
        linhas.append(f"Alerta criado em {local.strftime('%d/%m %H:%M')} (horário de Brasília)")
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


# ---------- recuperação de alertas (bloco 2.8) ----------

ESTADOS_REENVIO = ("pendente", "falhou")  # POST /alertas/reenviar
ESTADOS_SUBIDA = ("pendente",)  # subida do servidor: recupera envios interrompidos

# A reivindicação condicionada impede que duas execuções que leram o mesmo alerta o enviem as duas. Mas
# um alerta já reivindicado continua `falhou` (ou `pendente`) com menos de 5 tentativas enquanto a
# mensagem sai, e uma execução que o leia nesse intervalo o veria elegível: o esquema não tem um estado
# "em envio". Esta trava serializa as varreduras do mesmo processo (rota e subida do servidor). Entre
# processos diferentes o risco de duplicar continua, como registra a especificação.
_TRAVA_REENVIO = threading.Lock()


def _utc_texto(agora: datetime) -> str:
    if not isinstance(agora, datetime) or agora.utcoffset() is None:
        raise ValueError("agora precisa ser um datetime com fuso")
    return agora.astimezone(timezone.utc).isoformat()


def _limite_janela(agora: datetime) -> str:
    return _utc_texto(agora - JANELA_REENVIO)


def varrer_alertas(conn, agora: datetime) -> int:
    """Todo `pendente` com `tentativas` >= 5 passa a `falhou`, sem envio e sem marcar `tardio`."""
    _utc_texto(agora)
    cur = conn.execute(
        "UPDATE alertas SET estado = 'falhou' WHERE estado = 'pendente' AND tentativas >= ?",
        (MAX_TENTATIVAS_TOTAL,),
    )
    return cur.rowcount


def _condicao_elegivel(estados) -> str:
    marcas = ", ".join("?" for _ in estados)
    # criado_em e o limite estão em UTC com isoformat(): a comparação por texto segue a dos instantes.
    return (
        f"a.estado IN ({marcas}) AND a.tentativas < ? AND a.reconhecido_em IS NULL AND a.criado_em < ?"
    )


def listar_elegiveis(conn, agora: datetime, estados) -> list:
    """Alertas em `estados`, com menos de 5 tentativas, sem reconhecimento e criados há mais de 5 min."""
    linhas = conn.execute(
        "SELECT a.id, a.avaliacao_id, a.tipos, a.tentativas, a.criado_em, v.texto "
        "FROM alertas a JOIN avaliacoes v ON v.id = a.avaliacao_id "
        f"WHERE {_condicao_elegivel(estados)} ORDER BY a.criado_em, a.id",
        (*estados, MAX_TENTATIVAS_TOTAL, _limite_janela(agora)),
    ).fetchall()
    return [dict(linha) for linha in linhas]


def reivindicar(conn, alerta: dict, agora: datetime, estados) -> bool:
    """Soma a tentativa e marca `tardio` = 1 num único UPDATE, só se o alerta ainda estiver como foi lido.

    A condição repete a elegibilidade e confere `tentativas` igual à lida: se outra execução já o
    reivindicou, nenhuma linha muda e este reenvio pula o alerta.
    """
    cur = conn.execute(
        "UPDATE alertas AS a SET tentativas = a.tentativas + 1, tardio = 1 "
        f"WHERE a.id = ? AND a.tentativas = ? AND {_condicao_elegivel(estados)}",
        (alerta["id"], alerta["tentativas"], *estados, MAX_TENTATIVAS_TOTAL, _limite_janela(agora)),
    )
    return cur.rowcount == 1


def _reenviar_um(conn, alerta: dict, notificador, relogio, estados) -> str:
    """'enviado', 'falhou' ou 'pulado'. Nunca registra a mensagem da exceção do notificador."""
    if not reivindicar(conn, alerta, relogio(), estados):
        return "pulado"
    mensagem = montar_mensagem(
        alerta["avaliacao_id"], json.loads(alerta["tipos"]), alerta["texto"], criado_em=alerta["criado_em"]
    )
    try:
        notificador(mensagem)
    except Exception as e:  # noqa: BLE001 - qualquer erro conta como falha
        logger.warning(
            "falha no reenvio do alerta %s ao Telegram (%s)", alerta["id"], getattr(e, "motivo", type(e).__name__)
        )
        # Condicionado: se outra execução entregou e gravou `enviado` enquanto esta tentava, ele fica.
        conn.execute("UPDATE alertas SET estado = 'falhou' WHERE id = ? AND estado <> 'enviado'", (alerta["id"],))
        return "falhou"
    try:
        banco.mudar_estado_alerta(conn, alerta["id"], "enviado", enviado_em=relogio().isoformat())
    except Exception as e:  # noqa: BLE001 - a mensagem já saiu: não tentar de novo
        logger.error(
            "alerta %s reenviado ao Telegram, mas o estado enviado não foi gravado (%s)",
            alerta["id"], type(e).__name__,
        )
    return "enviado"


def reenviar_elegiveis(caminho_banco, notificador, relogio, *, estados=ESTADOS_REENVIO) -> dict:
    """Varre e tenta uma vez cada alerta elegível. Devolve {elegiveis, enviados, falharam, configurado}.

    Com o Telegram não configurado, só a varredura de estado roda: nada é tentado (não é tentativa),
    `tentativas` e `tardio` não mudam e o resultado traz `configurado` falso e zeros.
    """
    resultado = {"elegiveis": 0, "enviados": 0, "falharam": 0, "configurado": notificador is not None}
    with _TRAVA_REENVIO, closing(banco.abrir(caminho_banco)) as conn:
        varrer_alertas(conn, relogio())
        if notificador is None:
            logger.warning(
                "Telegram não configurado (RADAR_TELEGRAM_TOKEN ou RADAR_TELEGRAM_CHAT_ID ausente): "
                "nenhum alerta reenviado"
            )
            return resultado
        elegiveis = listar_elegiveis(conn, relogio(), estados)
        resultado["elegiveis"] = len(elegiveis)
        for alerta in elegiveis:
            try:
                situacao = _reenviar_um(conn, alerta, notificador, relogio, estados)
            except Exception as e:  # noqa: BLE001 - banco travado etc.: registra o tipo e segue para o próximo
                logger.error("falha ao reenviar o alerta %s (%s)", alerta["id"], type(e).__name__)
                situacao = "falhou"
            if situacao == "enviado":
                resultado["enviados"] += 1
            elif situacao == "falhou":
                resultado["falharam"] += 1
    return resultado


def recuperar_na_subida(caminho_banco, notificador, relogio) -> None:
    """Subida do servidor: reenvia só os `pendente` elegíveis. Nunca levanta exceção (só registra)."""
    try:
        r = reenviar_elegiveis(caminho_banco, notificador, relogio, estados=ESTADOS_SUBIDA)
        logger.info(
            "recuperação na subida: %s elegíveis, %s enviados, %s falharam",
            r["elegiveis"], r["enviados"], r["falharam"],
        )
    except Exception as e:  # noqa: BLE001 - a recuperação não pode derrubar a subida
        logger.error("falha na recuperação de alertas na subida do servidor (%s)", type(e).__name__)
