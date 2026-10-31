"""Token do link do painel (bloco 2.5).

Fonte da verdade: docs/ESPECIFICACAO.md (regras de segurança, tabela tokens_painel e a decisão de
31/10/2026 sobre o token). O token é aleatório, só o hash SHA-256 é guardado, vale 7 dias e pode ser
revogado. O token em claro sai uma única vez, para quem o criou, e nunca vai para log nem para o banco.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timezone

from app import banco

ESCOPO_PADRAO = "painel"

_FORMATO = re.compile(r"[A-Za-z0-9_-]{43}")  # secrets.token_urlsafe(32): 32 bytes em base64 url, sem "="
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# O trecho logo depois de /painel/ ou /api/painel/, até a próxima barra, "?", "#", espaço ou aspas.
# Sem distinção de maiúsculas e aceitando barras repetidas (o uvicorn não normaliza o caminho).
_CAMINHO_COM_TOKEN = re.compile(r"(?i)(/(?:api/+)?painel/+)[^/?#\s\"']+")
# Defesa extra: qualquer sequência isolada com o formato do token, fora de caminho. Um hash (64) não casa.
_TOKEN_SOLTO = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")


def gerar_token() -> str:
    return secrets.token_urlsafe(32)


def hash_do_token(token: str) -> str:
    """SHA-256 em hexadecimal minúsculo do token codificado em UTF-8."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def formato_valido(token) -> bool:
    return isinstance(token, str) and _FORMATO.fullmatch(token) is not None


def _utc(agora: datetime) -> datetime:
    if not isinstance(agora, datetime) or agora.utcoffset() is None:
        raise ValueError("agora precisa ser um datetime com fuso")
    return agora.astimezone(timezone.utc)


def criar_token_painel(conn, escopo: str = ESCOPO_PADRAO, *, agora: datetime) -> str:
    """Grava SÓ o hash e devolve o token em claro, uma única vez, a quem chamou."""
    instante = _utc(agora)
    token = gerar_token()
    banco.registrar_token_painel(conn, hash_do_token(token), escopo, instante.isoformat())
    return token


def validar_token(conn, token, agora: datetime) -> bool:
    """Válido se existe pelo hash, não foi revogado e agora < expira_em (no vencimento já é inválido).

    Um token fora do formato é recusado antes de qualquer consulta. A busca usa o hash (índice único).
    """
    instante = _utc(agora)
    if not formato_valido(token):
        return False
    linha = conn.execute(
        "SELECT expira_em, revogado_em FROM tokens_painel WHERE token_hash = ?", (hash_do_token(token),)
    ).fetchone()
    if linha is None:
        return False
    expira_em, revogado_em = linha
    if revogado_em is not None:
        return False
    return instante < _utc(datetime.fromisoformat(expira_em))


def revogar_token(conn, token_ou_hash, agora: datetime) -> bool:
    """Grava revogado_em. Revogar de novo não muda a data original. Devolve se o token existe.

    Aceita o token ou o hash em hexadecimal minúsculo (o formato que o banco guarda).
    """
    instante = _utc(agora)
    if formato_valido(token_ou_hash):
        token_hash = hash_do_token(token_ou_hash)
    elif isinstance(token_ou_hash, str) and _SHA256_HEX.fullmatch(token_ou_hash):
        token_hash = token_ou_hash
    else:
        return False
    cur = conn.execute(
        "UPDATE tokens_painel SET revogado_em = COALESCE(revogado_em, ?) WHERE token_hash = ?",
        (instante.isoformat(), token_hash),
    )
    return cur.rowcount == 1


def mascarar_caminho(texto: str) -> str:
    """Troca o trecho do token por *** em /painel/<token> e /api/painel/<token>/...

    Também mascara qualquer sequência isolada de 43 caracteres no formato do token.
    """
    return _TOKEN_SOLTO.sub("***", _CAMINHO_COM_TOKEN.sub(r"\1***", texto))


def _mascarar_valor(valor):
    """Strings são mascaradas; objetos (URL, bytes...) viram texto só se o texto tiver um token."""
    if isinstance(valor, str):
        return mascarar_caminho(valor)
    if isinstance(valor, (int, float, bool)) or valor is None:
        return valor
    texto = valor.decode("utf-8", "replace") if isinstance(valor, (bytes, bytearray)) else str(valor)
    mascarado = mascarar_caminho(texto)
    return mascarado if mascarado != texto else valor


class FiltroDeToken(logging.Filter):
    """Mascara o token na mensagem, nos argumentos, no traceback e na pilha, antes do formatter.

    O log de acesso do uvicorn guarda o caminho completo em record.args; o AccessFormatter lê de lá,
    por isso args continua uma tupla com os mesmos 5 campos.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _mascarar_valor(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_mascarar_valor(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _mascarar_valor(v) for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:  # o Formatter usa exc_text se já estiver pronto
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = mascarar_caminho(record.exc_text)
        if record.stack_info:
            record.stack_info = mascarar_caminho(record.stack_info)
        return True


LOGGERS_FILTRADOS = ("uvicorn.access", "uvicorn.error", "radar.api", "radar.alertas")
_FILTRO = FiltroDeToken()


def instalar_filtro_de_log(nomes=LOGGERS_FILTRADOS) -> None:
    """Instala o filtro nos loggers indicados, nos handlers deles e nos do logger raiz. Idempotente.

    Filtro de logger não vale para registros que sobem de loggers filhos; filtro de handler vale para
    tudo o que passa por ele. Um logger novo do app deve entrar em LOGGERS_FILTRADOS.
    """
    alvos = []
    for nome in nomes:
        logger = logging.getLogger(nome)
        alvos.append(logger)
        alvos.extend(logger.handlers)
    alvos.extend(logging.getLogger().handlers)
    for alvo in alvos:
        if _FILTRO not in alvo.filters:
            alvo.addFilter(_FILTRO)
