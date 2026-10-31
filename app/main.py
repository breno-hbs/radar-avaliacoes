"""API do Radar de Avaliações. POST /avaliacoes (bloco 2.2), alerta crítico via Telegram (bloco 2.3)
e token do painel (bloco 2.5: dependência exigir_token, Referrer-Policy e filtro do log de acesso).

Fonte da verdade: docs/ESPECIFICACAO.md. Servidor: uvicorn app.main:app

Variáveis de ambiente (nunca no código nem no Git):
- RADAR_API_KEY: chave esperada no cabeçalho X-API-Key. Ausente ou vazia, toda requisição é recusada.
- RADAR_DB: caminho do arquivo SQLite.
- ANTHROPIC_API_KEY: lida por app/classificador.py (criar_cliente) a partir do .env.
- RADAR_TELEGRAM_TOKEN e RADAR_TELEGRAM_CHAT_ID: lidas por app/telegram.py a partir do .env.
"""

import hmac
import logging
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator

from app import alertas, banco, classificador, telegram, tokens
from app.classificador import ErroPermanenteDaAPI, ErroTecnicoEsgotado

VERSAO_PROMPT = "v1"  # prompt congelado (prompts/classificador/v1.md)

logger = logging.getLogger("radar.api")

# O token do painel vai no caminho e o uvicorn registra o caminho no log de acesso: mascarado aqui.
# Instalado na importação do app, que o uvicorn faz depois de configurar o próprio logging.
tokens.instalar_filtro_de_log()

LINK_INVALIDO = "link inválido ou expirado"


class ErroDeConfiguracao(Exception):
    """Variável de ambiente ou chave ausente. Vira 500 genérico; o detalhe fica fora da resposta."""


class NovaAvaliacao(BaseModel):
    id_externo: StrictStr = Field(min_length=1)
    texto: StrictStr
    nota: StrictInt
    recebida_em: StrictStr

    @field_validator("recebida_em")
    @classmethod
    def _data_iso_com_fuso(cls, valor):
        try:
            instante = datetime.fromisoformat(valor)
        except ValueError:
            raise ValueError("recebida_em deve ser uma data ISO 8601") from None
        if instante.utcoffset() is None:
            raise ValueError("recebida_em precisa ter fuso (por exemplo -03:00 ou Z)")
        try:  # o mesmo cálculo que a camada do banco fará, antes de gastar uma chamada ao modelo
            instante.astimezone(timezone.utc) + timedelta(days=banco.PRAZO_RESPOSTA_DIAS)
        except OverflowError:
            raise ValueError("recebida_em fora do intervalo aceito") from None
        return valor


def _nao_autorizado():
    return HTTPException(
        status_code=401,
        detail="não autorizado",
        headers={"WWW-Authenticate": "X-API-Key"},
    )


@lru_cache(maxsize=1)
def _avisar_chave_ausente():
    """Avisa uma única vez (por processo) que a chave não está configurada, sem inundar o log."""
    logger.warning("RADAR_API_KEY ausente ou vazia: toda requisição será recusada com 401")


def exigir_chave(x_api_key: Optional[str]) -> None:
    """Confere o X-API-Key. A chave esperada vem de RADAR_API_KEY, lida a cada requisição.

    Ausente ou vazia, a variável recusa tudo: uma chave vazia nunca é aceita. A comparação é feita
    em bytes e em tempo constante, o que também evita TypeError com caracteres não ASCII.
    """
    esperada = os.environ.get("RADAR_API_KEY", "")
    if not esperada:
        _avisar_chave_ausente()
        raise _nao_autorizado()
    if not x_api_key:
        raise _nao_autorizado()
    if not hmac.compare_digest(x_api_key.encode("utf-8"), esperada.encode("utf-8")):
        raise _nao_autorizado()


class RotaComChave(APIRoute):
    """Rota que exige o X-API-Key ANTES de ler o corpo.

    O FastAPI decodifica o JSON do corpo antes de resolver as dependências; uma dependência de
    autenticação deixaria um corpo malformado, sem chave, responder 422 em vez de 401. Conferir a
    chave aqui, antes de qualquer leitura do corpo, mantém a regra "sem chave, 401".
    """

    def get_route_handler(self):
        tratar = super().get_route_handler()

        async def tratar_com_chave(request: Request):
            exigir_chave(request.headers.get("x-api-key"))
            return await tratar(request)

        return tratar_com_chave


@lru_cache(maxsize=1)
def _recursos():
    """Cliente do modelo e prompt congelado, criados uma vez e só quando a primeira avaliação chega."""
    try:
        cliente = classificador.criar_cliente()
    except SystemExit:  # criar_cliente encerra o processo se a chave da Anthropic faltar
        raise ErroDeConfiguracao("chave da Anthropic ausente") from None
    return cliente, classificador.carregar_prompt(VERSAO_PROMPT)


def _classificar_com_o_modelo(texto):
    cliente, prompt = _recursos()
    return classificador.classificar(texto, cliente, prompt)


def obter_classificador():
    """Dependência substituível: os testes a trocam por um falso e nunca chamam a API real."""
    return _classificar_com_o_modelo


def obter_notificador():
    """Dependência substituível: o Telegram de produção, ou None se não estiver configurado."""
    return telegram.notificador_do_ambiente()


def _agora_utc():
    return datetime.now(timezone.utc)


def obter_relogio():
    """Dependência substituível: devolve a função que dá o instante atual em UTC."""
    return _agora_utc


def exigir_token(token: str, relogio=Depends(obter_relogio)) -> str:
    """Dependência para rotas com {token} no caminho.

    Inexistente, expirado, revogado ou malformado: todos dão o mesmo 404, sem revelar o motivo.
    """
    if not tokens.formato_valido(token):
        raise HTTPException(status_code=404, detail=LINK_INVALIDO)
    try:
        caminho = os.environ.get("RADAR_DB")
        if not caminho:
            raise ErroDeConfiguracao("RADAR_DB não definida")
        with closing(banco.abrir(caminho)) as conn:
            valido = tokens.validar_token(conn, token, relogio())
    except Exception as e:  # noqa: BLE001 - configuração ou banco: 500 genérico, só o tipo no log
        logger.error("falha ao validar o link do painel (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="falha interna") from None
    if not valido:
        raise HTTPException(status_code=404, detail=LINK_INVALIDO)
    return token


def _processar(nova: NovaAvaliacao, resposta: Response, classificar, entrada, agendar_alerta):
    caminho = os.environ.get("RADAR_DB")
    if not caminho:
        raise ErroDeConfiguracao("RADAR_DB não definida")

    with closing(banco.abrir(caminho)) as conn:
        existente = banco.buscar_avaliacao_por_id_externo(conn, nova.id_externo)
        if existente is not None:  # duplicata: ignora, sem gastar uma chamada ao modelo
            resposta.status_code = 200
            return {"avaliacao_id": existente["id"], "criada": False}

        saida = classificar(nova.texto)["saida"]
        if saida is None:  # conteúdo ruim depois da regra de retentativa
            status, categorias, criticidade = "invalida", [], []
        else:
            status, categorias, criticidade = "ok", saida["categorias"], saida["criticidade"]

        gravado = banco.gravar_avaliacao(
            conn,
            id_externo=nova.id_externo,
            texto=nova.texto,
            nota=nova.nota,
            recebida_em=nova.recebida_em,
            categorias=categorias,
            criticidade=criticidade,
            status_classificacao=status,
            alerta_criado_em=entrada.isoformat(),  # instante de entrada: base do critério 5
        )

    if not gravado.criada:  # outra requisição gravou o mesmo id_externo enquanto esta classificava
        resposta.status_code = 200
        return {"avaliacao_id": gravado.avaliacao_id, "criada": False}
    if gravado.alerta_id is not None:
        mensagem = alertas.montar_mensagem(gravado.avaliacao_id, criticidade, nova.texto)
        agendar_alerta(caminho, gravado.alerta_id, mensagem)
    return {"avaliacao_id": gravado.avaliacao_id, "criada": True, "alerta_id": gravado.alerta_id}


def criar_app() -> FastAPI:
    # Sem /docs, /redoc e /openapi.json: a documentação automática não precisa ficar aberta sem chave.
    app = FastAPI(title="Radar de Avaliações", docs_url=None, redoc_url=None, openapi_url=None)
    com_chave = APIRouter(route_class=RotaComChave)

    @app.middleware("http")
    async def sem_referer(request: Request, chamar_proxima):
        # O token do painel vai na URL: sem Referer, ele não vaza para links externos.
        resposta = await chamar_proxima(request)
        resposta.headers["Referrer-Policy"] = "no-referrer"
        return resposta

    @app.exception_handler(Exception)
    async def falha_inesperada(request: Request, erro: Exception):
        # Roda no ServerErrorMiddleware, por fora do middleware acima: o cabeçalho vai aqui também.
        logger.error("falha inesperada (%s)", type(erro).__name__)
        return JSONResponse(
            status_code=500, content={"detail": "falha interna"}, headers={"Referrer-Policy": "no-referrer"}
        )

    @com_chave.post("/avaliacoes", status_code=201)
    def receber_avaliacao(
        nova: NovaAvaliacao,
        resposta: Response,
        tarefas: BackgroundTasks,
        classificar=Depends(obter_classificador),
        notificador=Depends(obter_notificador),
        relogio=Depends(obter_relogio),
    ):
        entrada = relogio()  # lido uma vez, no início: é o criado_em do alerta

        def agendar_alerta(caminho, alerta_id, mensagem):
            # Roda depois da resposta: o código HTTP e o corpo não dependem do Telegram.
            tarefas.add_task(alertas.enviar_alerta, caminho, alerta_id, mensagem, notificador, relogio)

        # Nenhuma resposta nem log leva mensagem de exceção, corpo de erro da API ou chave:
        # só o tipo e, para erros da API do classificador, o status e o request_id.
        try:
            return _processar(nova, resposta, classificar, entrada, agendar_alerta)
        except ErroTecnicoEsgotado as e:
            logger.warning(
                "classificador indisponível depois das retentativas (status=%s, request_id=%s)",
                e.status, e.request_id,
            )
            raise HTTPException(
                status_code=503,
                detail="classificador indisponível; reenvie a avaliação mais tarde",
            ) from None
        except ErroPermanenteDaAPI as e:
            logger.error(
                "erro permanente da API do classificador (status=%s, request_id=%s)",
                e.status, e.request_id,
            )
            raise HTTPException(status_code=500, detail="falha interna") from None
        except Exception as e:  # noqa: BLE001 - qualquer outra falha vira 500 genérico
            logger.error("falha inesperada ao receber a avaliação (%s)", type(e).__name__)
            raise HTTPException(status_code=500, detail="falha interna") from None

    app.include_router(com_chave)
    return app


app = criar_app()
