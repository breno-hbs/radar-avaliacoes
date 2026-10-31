"""Cliente do Telegram para o alerta crítico (bloco 2.3).

Configuração, lida do ambiente ou do .env pelo mesmo padrão de app/classificador.py:
- RADAR_TELEGRAM_TOKEN: token do bot.
- RADAR_TELEGRAM_CHAT_ID: chat que recebe os alertas.

SEGREDO: a URL da API do Telegram contém o token. Por isso os loggers "httpx" e "httpcore" ficam
em WARNING (o httpx registra a URL de cada requisição em INFO), nenhuma exceção do cliente HTTP é
registrada nem encadeada, e o token nunca aparece em repr, str ou mensagem de erro.
"""

import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from app.classificador import RAIZ

TIMEOUT_SEGUNDOS = 5.0
_URL = "https://api.telegram.org/bot{token}/sendMessage"

for _nome in ("httpx", "httpcore"):
    logging.getLogger(_nome).setLevel(logging.WARNING)


class FalhaNoEnvio(Exception):
    """O Telegram não confirmou a mensagem. `motivo` é só o tipo da falha, sem URL nem token."""

    def __init__(self, motivo: str):
        super().__init__(f"falha no envio ao Telegram ({motivo})")
        self.motivo = motivo


class NotificadorTelegram:
    """Envia uma mensagem de texto puro (sem parse_mode) ao chat configurado.

    `transporte` existe para os testes (httpx.MockTransport); em produção é o transporte padrão.
    """

    def __init__(self, token: str, chat_id: str, transporte: Optional[httpx.BaseTransport] = None):
        self._token = token
        self._chat_id = chat_id
        self._transporte = transporte

    def __repr__(self):
        return "NotificadorTelegram(configurado)"

    __str__ = __repr__

    def __call__(self, texto: str) -> None:
        motivo = None
        resposta = None
        try:
            with httpx.Client(transport=self._transporte, timeout=TIMEOUT_SEGUNDOS) as cliente:
                resposta = cliente.post(
                    _URL.format(token=self._token),
                    json={"chat_id": self._chat_id, "text": texto},
                )
        except Exception as e:  # noqa: BLE001 - qualquer erro de rede ou de cliente conta como falha
            motivo = type(e).__name__
        # Levantada fora do except: a exceção do httpx, que traz a URL com o token, não fica encadeada.
        if motivo is not None:
            raise FalhaNoEnvio(motivo)
        if resposta.status_code != 200:
            raise FalhaNoEnvio(f"status_{resposta.status_code}")
        try:
            corpo = resposta.json()
        except ValueError:
            corpo = None
            motivo = "resposta_nao_json"
        if motivo is not None:
            raise FalhaNoEnvio(motivo)
        if not isinstance(corpo, dict) or corpo.get("ok") is not True:
            raise FalhaNoEnvio("ok_false")


def ler_configuracao() -> Optional[tuple[str, str]]:
    """Devolve (token, chat_id), ou None se algum estiver ausente ou vazio."""
    load_dotenv(RAIZ / ".env")
    token = os.environ.get("RADAR_TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("RADAR_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def notificador_do_ambiente() -> Optional[NotificadorTelegram]:
    configuracao = ler_configuracao()
    return None if configuracao is None else NotificadorTelegram(*configuracao)
