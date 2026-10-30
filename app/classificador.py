"""Classificador de avaliações: uma chamada ao modelo por avaliação.

Dados simulados. Projeto independente, sem vínculo com o iFood.

REGRA DE RETENTATIVA (v1; vale para o ajuste e para a avaliação):
- Erro técnico REPETÍVEL é:
  (a) exceção anthropic.APIError sem resposta HTTP (tempo esgotado, falha de conexão) ou com
      status HTTP 408, 409, 429 ou 5xx;
  (b) stop_reason "max_tokens" ou "refusal";
  (c) resposta sem nenhum bloco do tipo "text";
  (d) texto que não é JSON (json.loads falha);
  (e) JSON fora do contrato (contrato.SaidaInvalida: categoria inventada, chave extra,
      item repetido etc.).
- Em erro repetível, há até 2 NOVAS tentativas (MAX_TENTATIVAS = 3 chamadas no total),
  com exatamente os mesmos argumentos. Nada é alterado entre as tentativas.
- Esgotadas as 3, a saída é None (conta como saída inválida nas métricas).
- Qualquer OUTRO status HTTP (400, 401, 403, 404, 422...) é erro permanente: NÃO é repetido e
  levanta ErroPermanenteDaAPI, que aborta a rodada inteira. O chamador não deve gravar
  resultados parciais nem tratá-los como saídas inválidas.
- Não há troca de modelo nem fallback em caso de recusa.
- Qualquer outra exceção (erro de programação) sobe sem nova tentativa.
- O SDK é criado com max_retries=0: as únicas novas tentativas são as daqui.

Registro de erro: só "tentativa", "tipo", "status", "request_id" e "stop_reason",
conforme o caso. Nunca a mensagem da exceção, cabeçalhos ou o texto bruto da resposta.

O modelo recebe só o texto da avaliação (nada de nota, id, gabarito ou justificativa).
"""
import json
import os
import re
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from app import contrato

RAIZ = Path(__file__).resolve().parent.parent
PASTA_PROMPTS = RAIZ / "prompts" / "classificador"

MAX_TENTATIVAS = 3
STATUS_REPETIVEIS = (408, 409, 429)  # além de qualquer 5xx


class ErroPermanenteDaAPI(Exception):
    """Status HTTP que repetir não resolve (chave inválida, pedido malformado, modelo inexistente).

    Carrega só o status e o request_id; nunca a mensagem nem o corpo do erro da API.
    """

    def __init__(self, status, request_id):
        super().__init__(f"erro permanente da API (status {status}, request_id {request_id})")
        self.status = status
        self.request_id = request_id


def _repetivel(e):
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code in STATUS_REPETIVEIS or e.status_code >= 500
    return True  # tempo esgotado e falha de conexão

SCHEMA = {
    "type": "object",
    "properties": {
        "categorias": {
            "type": "array",
            "items": {"type": "string", "enum": list(contrato.CATEGORIAS)},
        },
        "criticidade": {
            "type": "array",
            "items": {"type": "string", "enum": list(contrato.CRITICIDADES)},
        },
    },
    "required": ["categorias", "criticidade"],
    "additionalProperties": False,
}

# Parâmetros fixos de toda chamada. temperature, top_p e top_k NÃO são enviados:
# este modelo os rejeita (e o SDK 2.3.0 nem os aceita em messages.create).
# A variação é medida repetindo as rodadas, não controlada por amostragem.
PARAMETROS = {
    "model": "claude-sonnet-5-5",
    "max_tokens": 400,
    "thinking": {"type": "between_tools"},
    "output_config": {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": SCHEMA},
    },
}

_VERSAO_VALIDA = re.compile(r"v\d+")


def carregar_prompt(versao):
    """Lê prompts/classificador/{versao}.md, o texto exato do system prompt."""
    if not isinstance(versao, str) or not _VERSAO_VALIDA.fullmatch(versao):
        raise ValueError("versão do prompt deve ter a forma v0, v1, v2...")
    return (PASTA_PROMPTS / f"{versao}.md").read_text(encoding="utf-8")


def criar_cliente():
    """Cliente real. A chave vem só do .env (ANTHROPIC_API_KEY) e nunca é exibida."""
    load_dotenv(RAIZ / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY ausente. Defina-a no .env (o valor nunca é exibido).")
    return anthropic.Anthropic(max_retries=0, timeout=60)


def _erro_de_excecao(tentativa, e):
    if isinstance(e, anthropic.APIStatusError):
        return {"tentativa": tentativa, "tipo": type(e).__name__,
                "status": e.status_code, "request_id": e.request_id}
    # Sem resposta HTTP (conexão, tempo esgotado): só o tipo da exceção.
    return {"tentativa": tentativa, "tipo": type(e).__name__, "status": None, "request_id": None}


def _erro_de_resposta(tentativa, tipo, resposta):
    return {"tentativa": tentativa, "tipo": tipo,
            "request_id": getattr(resposta, "_request_id", None),
            "stop_reason": getattr(resposta, "stop_reason", None)}


def _uso(tentativa, resposta):
    uso = getattr(resposta, "usage", None)
    return {"tentativa": tentativa,
            "input_tokens": getattr(uso, "input_tokens", None),
            "output_tokens": getattr(uso, "output_tokens", None)}


def _interpretar(resposta):
    """Devolve (saida, None) ou (None, tipo_do_erro)."""
    if resposta.stop_reason in ("max_tokens", "refusal"):
        return None, "stop_reason"
    textos = [bloco.text for bloco in resposta.content if bloco.type == "text"]
    if not textos:
        return None, "sem_bloco_texto"
    try:
        obj = json.loads("".join(textos))
    except json.JSONDecodeError:
        return None, "json_invalido"
    try:
        return contrato.validar_saida(obj), None
    except contrato.SaidaInvalida:
        return None, "saida_invalida"


def classificar(texto, cliente, prompt):
    """Classifica UMA avaliação.

    Devolve {"saida": dict validado ou None, "tentativas": int, "erros": [registros],
    "usos": [{"tentativa", "input_tokens", "output_tokens"}]}.
    "usos" guarda response.usage de cada tentativa que recebeu resposta (inclusive as
    respostas ruins, que também gastam tokens); tentativas que levantaram exceção não têm
    resposta e ficam fora. Valor ausente no usage vira None.
    `cliente` é injetado: precisa só de .messages.create(**kwargs).
    """
    if contrato.texto_vazio(texto):
        return {"saida": contrato.saida_texto_vazio(), "tentativas": 0, "erros": [], "usos": []}

    argumentos = {
        **PARAMETROS,
        "system": prompt,
        "messages": [{"role": "user", "content": texto}],
    }
    erros = []
    usos = []
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        permanente = None
        try:
            resposta = cliente.messages.create(**argumentos)
        except anthropic.APIError as e:
            if _repetivel(e):
                erros.append(_erro_de_excecao(tentativa, e))
                continue
            permanente = (e.status_code, e.request_id)
        if permanente is not None:
            # Fora do except: a exceção não herda __context__ com a mensagem e o corpo originais.
            raise ErroPermanenteDaAPI(*permanente)
        usos.append(_uso(tentativa, resposta))
        saida, tipo = _interpretar(resposta)
        if saida is not None:
            return {"saida": saida, "tentativas": tentativa, "erros": erros, "usos": usos}
        erros.append(_erro_de_resposta(tentativa, tipo, resposta))
    return {"saida": None, "tentativas": MAX_TENTATIVAS, "erros": erros, "usos": usos}
