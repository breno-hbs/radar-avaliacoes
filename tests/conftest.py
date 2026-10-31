"""Proteções comuns a toda a suíte.

- Nenhum teste lê o .env real: o load_dotenv do Telegram e o do classificador viram no-op, e as
  variáveis RADAR_TELEGRAM_* são removidas. Assim um token real do .env nunca chega a um teste.
- O transporte HTTP real do httpx (usado pelo Telegram) é bloqueado. O SDK da Anthropic usa outro
  pacote (httpx2) e não é coberto aqui; os testes do classificador já usam clientes falsos.
Os testes que precisam do Telegram usam um notificador falso ou httpx.MockTransport.
"""

import httpx
import pytest

from app import classificador, telegram


@pytest.fixture(autouse=True)
def _sem_env_real_nem_rede_do_telegram(monkeypatch):
    monkeypatch.setattr(telegram, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(classificador, "load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("RADAR_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("RADAR_TELEGRAM_CHAT_ID", raising=False)

    def bloqueado(self, request):
        raise RuntimeError("rede real bloqueada nos testes")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", bloqueado)
