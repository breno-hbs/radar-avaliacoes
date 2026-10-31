"""Manda UMA mensagem fixa de teste ao Telegram, com RADAR_TELEGRAM_TOKEN e RADAR_TELEGRAM_CHAT_ID do .env.

Fora da suíte de testes: faz uma chamada de rede real. Imprime só "enviado" ou "falhou: <tipo do erro>",
nunca a URL nem o token.

Uso: python scripts/enviar_teste_telegram.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import telegram  # noqa: E402

MENSAGEM = "teste do Radar"


def main() -> int:
    notificador = telegram.notificador_do_ambiente()
    if notificador is None:
        print("falhou: TelegramNaoConfigurado")
        return 1
    try:
        notificador(MENSAGEM)
    except Exception as e:  # noqa: BLE001 - só o tipo da falha, nunca a mensagem da exceção
        print(f"falhou: {getattr(e, 'motivo', type(e).__name__)}")
        return 1
    print("enviado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
