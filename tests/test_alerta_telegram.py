"""Testes do alerta crítico via Telegram (bloco 2.3).

Fonte da verdade: docs/ESPECIFICACAO.md ("Ordem do alerta", "Limite da unicidade" e a decisão de
31/10/2026 sobre o envio). Nenhum teste faz chamada de rede real: o notificador é falso ou usa
httpx.MockTransport. O token abaixo é fictício e existe só nestes testes.
"""

import json
import logging
import sqlite3
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import alertas, banco, classificador, main, telegram

CHAVE = "chave-ficticia-de-teste"
TOKEN = "123456789:TOKEN-FALSO-CONHECIDO-nao-e-real"
CHAT = "-100987654321"

CORPO = {
    "id_externo": "ext-1",
    "texto": "Passei mal depois de comer e tenho alergia a amendoim.",
    "nota": 1,
    "recebida_em": "2026-10-26T12:00:00-03:00",
}

T0 = datetime(2026, 10, 26, 15, 0, 0, tzinfo=timezone.utc)


class ClassificadorFalso:
    def __init__(self, categorias=("qualidade_temperatura",), criticidade=("saude", "alergia"), saida=True):
        self.saida = (
            {"categorias": list(categorias), "criticidade": list(criticidade)} if saida else None
        )

    def __call__(self, texto):
        return {"saida": self.saida, "tentativas": 1, "erros": [], "usos": []}


class TelegramFalso:
    """Notificador falso: falha nas primeiras `falhas` chamadas e registra cada mensagem."""

    def __init__(self, falhas=0, erro=None):
        self.falhas = falhas
        self.erro = erro or RuntimeError("falha simulada")
        self.mensagens = []

    def __call__(self, texto):
        self.mensagens.append(texto)
        if len(self.mensagens) <= self.falhas:
            raise self.erro


class RelogioFalso:
    """Devolve, a cada chamada, o próximo instante da lista (em UTC)."""

    def __init__(self, *instantes):
        self.instantes = list(instantes)
        self.chamadas = 0

    def __call__(self):
        instante = self.instantes[min(self.chamadas, len(self.instantes) - 1)]
        self.chamadas += 1
        return instante


@pytest.fixture
def caminho(tmp_path, monkeypatch):
    destino = tmp_path / "radar.db"
    monkeypatch.setenv("RADAR_API_KEY", CHAVE)
    monkeypatch.setenv("RADAR_DB", str(destino))
    return destino


@pytest.fixture
def cliente(caminho):
    with ExitStack() as pilha:

        def criar(notificador, classificar=None, relogio=None):
            app = main.criar_app()
            app.dependency_overrides[main.obter_classificador] = lambda: classificar or ClassificadorFalso()
            app.dependency_overrides[main.obter_notificador] = lambda: notificador
            if relogio is not None:
                app.dependency_overrides[main.obter_relogio] = lambda: relogio
            return pilha.enter_context(TestClient(app))

        yield criar


def _post(c, corpo=None):
    return c.post("/avaliacoes", json=corpo or CORPO, headers={"X-API-Key": CHAVE})


def _alerta(caminho, avaliacao_id=1):
    with closing(banco.abrir(caminho)) as c:
        return banco.buscar_alerta_por_avaliacao(c, avaliacao_id)


def _conta_alertas(caminho):
    with closing(banco.abrir(caminho)) as c:
        return c.execute("SELECT COUNT(*) FROM alertas").fetchone()[0]


# Envio e estados ---------------------------------------------------------------------


def test_sucesso_na_primeira_tentativa(cliente, caminho):
    tg = TelegramFalso()
    r = _post(cliente(tg, relogio=RelogioFalso(T0, T0 + timedelta(seconds=2))))

    assert r.status_code == 201
    alerta = _alerta(caminho)
    assert alerta["estado"] == "enviado"
    assert alerta["tentativas"] == 1
    assert alerta["enviado_em"] == "2026-10-26T15:00:02+00:00"
    assert len(tg.mensagens) == 1


def test_falha_e_sucesso_na_segunda_tentativa(cliente, caminho):
    tg = TelegramFalso(falhas=1)
    r = _post(cliente(tg))

    assert r.status_code == 201
    alerta = _alerta(caminho)
    assert alerta["estado"] == "enviado"
    assert alerta["tentativas"] == 2
    assert alerta["enviado_em"] is not None
    assert len(tg.mensagens) == 2
    assert tg.mensagens[0] == tg.mensagens[1]


def test_duas_falhas_deixam_falhou_e_o_post_continua_201(cliente, caminho):
    tg = TelegramFalso(falhas=2)
    r = _post(cliente(tg))

    assert r.status_code == 201
    assert r.json()["criada"] is True
    alerta = _alerta(caminho)
    assert alerta["estado"] == "falhou"
    assert alerta["tentativas"] == 2
    assert alerta["enviado_em"] is None
    assert len(tg.mensagens) == 2  # uma nova tentativa só, nunca uma terceira


@pytest.mark.parametrize(
    "erro",
    [
        RuntimeError("x"),
        TimeoutError("x"),
        telegram.FalhaNoEnvio("status_500"),
        telegram.FalhaNoEnvio("ok_false"),
        httpx.ConnectError("x"),
    ],
)
def test_qualquer_erro_conta_como_falha(cliente, caminho, erro):
    _post(cliente(TelegramFalso(falhas=2, erro=erro)))
    assert _alerta(caminho)["estado"] == "falhou"


def test_resposta_nao_depende_do_telegram(cliente):
    corpos = []
    for i, tg in enumerate([TelegramFalso(), TelegramFalso(falhas=2), None]):
        r = _post(cliente(tg), corpo={**CORPO, "id_externo": f"ext-{i}"})
        corpos.append((r.status_code, frozenset(r.json())))
    assert set(corpos) == {(201, frozenset({"avaliacao_id", "criada", "alerta_id"}))}


def test_envio_e_agendado_em_tarefa_de_fundo_e_nao_feito_durante_a_requisicao(cliente, caminho, monkeypatch):
    agendadas = []
    from starlette.background import BackgroundTasks

    def captura(self, funcao, *args, **kwargs):
        agendadas.append((funcao, args))  # não executa: prova que a requisição não envia nada

    monkeypatch.setattr(BackgroundTasks, "add_task", captura)
    tg = TelegramFalso()
    r = _post(cliente(tg))

    assert r.status_code == 201
    assert tg.mensagens == []  # nada foi enviado durante a requisição
    assert len(agendadas) == 1
    funcao, args = agendadas[0]
    assert funcao is alertas.enviar_alerta
    assert args[1] == r.json()["alerta_id"]
    assert _alerta(caminho)["estado"] == "pendente"
    assert _alerta(caminho)["tentativas"] == 0


def test_entregue_mas_estado_nao_gravado_tem_log_proprio(cliente, caminho, monkeypatch, caplog):
    original = banco.mudar_estado_alerta

    def falha_ao_marcar_enviado(conn, alerta_id, estado, **kw):
        if estado == "enviado":
            raise sqlite3.OperationalError("database is locked")
        return original(conn, alerta_id, estado, **kw)

    monkeypatch.setattr(banco, "mudar_estado_alerta", falha_ao_marcar_enviado)
    caplog.set_level(logging.WARNING)
    tg = TelegramFalso()
    r = _post(cliente(tg))

    assert r.status_code == 201
    assert len(tg.mensagens) == 1  # entregue uma vez, sem nova tentativa
    alerta = _alerta(caminho)
    assert alerta["estado"] == "pendente"
    assert alerta["tentativas"] == 1
    mensagens = [x.getMessage() for x in caplog.records]
    assert any("entregue" in m and "OperationalError" in m and str(r.json()["alerta_id"]) in m for m in mensagens)


# Telegram não configurado ----------------------------------------------------------------


def test_nao_configurado_fica_pendente_sem_tentativas_e_avisa(cliente, caminho, caplog):
    caplog.set_level(logging.WARNING)
    r = _post(cliente(None))

    assert r.status_code == 201
    alerta = _alerta(caminho)
    assert alerta["estado"] == "pendente"
    assert alerta["tentativas"] == 0
    assert alerta["enviado_em"] is None
    avisos = [x for x in caplog.records if x.levelno == logging.WARNING and "Telegram" in x.getMessage()]
    assert len(avisos) == 1
    assert str(r.json()["alerta_id"]) in avisos[0].getMessage()


@pytest.mark.parametrize(
    "token, chat",
    [(None, None), (TOKEN, None), (None, CHAT), ("", CHAT), (TOKEN, ""), ("  ", CHAT), (TOKEN, " ")],
)
def test_notificador_de_producao_e_none_sem_configuracao(monkeypatch, token, chat):
    for nome, valor in (("RADAR_TELEGRAM_TOKEN", token), ("RADAR_TELEGRAM_CHAT_ID", chat)):
        if valor is None:
            monkeypatch.delenv(nome, raising=False)
        else:
            monkeypatch.setenv(nome, valor)
    assert telegram.notificador_do_ambiente() is None
    assert main.obter_notificador() is None


def test_notificador_de_producao_configurado(monkeypatch):
    monkeypatch.setenv("RADAR_TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("RADAR_TELEGRAM_CHAT_ID", CHAT)
    notificador = main.obter_notificador()
    assert isinstance(notificador, telegram.NotificadorTelegram)
    assert TOKEN not in repr(notificador)
    assert TOKEN not in str(notificador)


def test_configuracao_vem_do_env_pelo_mesmo_padrao_do_classificador(monkeypatch):
    lidos = []
    monkeypatch.setattr(telegram, "load_dotenv", lambda caminho, **k: lidos.append(caminho))
    telegram.ler_configuracao()
    assert lidos == [classificador.RAIZ / ".env"]


def test_app_nao_cai_sem_telegram_configurado(caminho):
    # Sem nenhum override do notificador: o de produção lê o ambiente (vazio nos testes).
    app = main.criar_app()
    app.dependency_overrides[main.obter_classificador] = lambda: ClassificadorFalso()
    with TestClient(app) as c:
        r1 = _post(c)
        r2 = _post(c, corpo={**CORPO, "id_externo": "ext-2"})
    assert (r1.status_code, r2.status_code) == (201, 201)
    assert _alerta(caminho)["estado"] == "pendente"


# Quando NÃO enviar -----------------------------------------------------------------------


def test_avaliacao_sem_criticidade_nao_envia(cliente, caminho):
    tg = TelegramFalso()
    r = _post(cliente(tg, classificar=ClassificadorFalso(criticidade=())))
    assert r.status_code == 201
    assert r.json()["alerta_id"] is None
    assert tg.mensagens == []
    assert _conta_alertas(caminho) == 0


def test_avaliacao_invalida_nao_envia(cliente, caminho):
    tg = TelegramFalso()
    r = _post(cliente(tg, classificar=ClassificadorFalso(saida=False)))
    assert r.status_code == 201
    assert tg.mensagens == []
    assert _conta_alertas(caminho) == 0


def test_duplicata_nao_reenvia(cliente, caminho):
    tg = TelegramFalso()
    c = cliente(tg)
    primeira = _post(c)
    segunda = _post(c)
    assert (primeira.status_code, segunda.status_code) == (201, 200)
    assert len(tg.mensagens) == 1
    assert _alerta(caminho)["tentativas"] == 1


def test_duplicata_de_alerta_que_falhou_nao_reenvia(cliente, caminho):
    tg = TelegramFalso(falhas=99)
    c = cliente(tg)
    _post(c)
    _post(c)
    assert len(tg.mensagens) == 2  # só as duas tentativas da primeira requisição
    assert _alerta(caminho)["estado"] == "falhou"


def test_gravacao_concorrente_nao_envia_de_novo(cliente, caminho):
    # Outra requisição grava o mesmo id_externo enquanto esta classifica: esta não envia nada.
    class Concorrente(ClassificadorFalso):
        def __call__(self, texto):
            with closing(banco.abrir(caminho)) as conn:
                banco.gravar_avaliacao(
                    conn, id_externo="ext-1", texto="outra", nota=1,
                    recebida_em="2026-10-26T12:00:00-03:00", categorias=[],
                    criticidade=["saude"], status_classificacao="ok",
                )
            return super().__call__(texto)

    tg = TelegramFalso()
    r = _post(cliente(tg, classificar=Concorrente()))
    assert r.status_code == 200
    assert tg.mensagens == []


# Mensagem ----------------------------------------------------------------------------------


def test_mensagem_lista_todos_os_tipos_como_relato_a_verificar(cliente):
    tg = TelegramFalso()
    r = _post(cliente(tg, classificar=ClassificadorFalso(criticidade=("saude", "alergia", "cobranca_indevida"))))
    mensagem = tg.mensagens[0]
    prefixo, primeira, *resto = mensagem.split("\n")

    assert prefixo == "(dados simulados)"  # decisão de 31/10/2026 sobre a mensagem do alerta
    assert "🔴" in primeira
    assert "Alerta crítico" in primeira
    for tipo in ("saude", "alergia", "cobranca_indevida"):
        linhas = [l for l in resto if l.startswith("- ") and tipo in l]
        assert linhas and all("relato a verificar" in l for l in linhas)
    assert f"Avaliação {r.json()['avaliacao_id']}" in mensagem
    assert CORPO["texto"] in mensagem


def test_texto_longo_e_truncado_em_300_caracteres():
    texto = "a" * 1000
    mensagem = alertas.montar_mensagem(7, ["saude"], texto)
    trecho = mensagem.split("\n")[-1]
    assert len(trecho) == 300
    assert trecho.endswith("…")
    assert "a" * 301 not in mensagem


def test_texto_de_ate_300_caracteres_nao_e_cortado():
    texto = "b" * 300
    assert alertas.montar_mensagem(7, ["saude"], texto).split("\n")[-1] == texto


def test_texto_vazio_aparece_como_sem_texto():
    assert alertas.montar_mensagem(7, ["saude"], "").split("\n")[-1] == "(sem texto)"


def test_mensagem_e_texto_puro_sem_parse_mode():
    capturado = {}

    def responder(request):
        capturado["url"] = str(request.url)
        capturado["corpo"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {}})

    telegram.NotificadorTelegram(TOKEN, CHAT, transporte=httpx.MockTransport(responder))("olá <b>x</b>")

    assert capturado["corpo"] == {"chat_id": CHAT, "text": "olá <b>x</b>"}
    assert "parse_mode" not in capturado["corpo"]
    assert capturado["url"].endswith("/sendMessage")


# Cliente HTTP do Telegram ---------------------------------------------------------------------


def _notificador(responder):
    return telegram.NotificadorTelegram(TOKEN, CHAT, transporte=httpx.MockTransport(responder))


@pytest.mark.parametrize(
    "resposta, motivo",
    [
        (httpx.Response(500, json={"ok": False}), "status_500"),
        (httpx.Response(401, json={"ok": False, "description": f"bot {TOKEN} inválido"}), "status_401"),
        (httpx.Response(429, json={"ok": False}), "status_429"),
        (httpx.Response(200, json={"ok": False, "description": "x"}), "ok_false"),
        (httpx.Response(200, json={"result": {}}), "ok_false"),
        (httpx.Response(200, json=[1, 2]), "ok_false"),
        (httpx.Response(200, content=b"nao e json"), "resposta_nao_json"),
    ],
)
def test_resposta_que_nao_e_sucesso_conta_como_falha(resposta, motivo):
    with pytest.raises(telegram.FalhaNoEnvio) as erro:
        _notificador(lambda request: resposta)("oi")
    assert erro.value.motivo == motivo
    assert TOKEN not in str(erro.value)


@pytest.mark.parametrize("excecao", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout])
def test_erro_de_rede_vira_falha_sem_url_nem_token(excecao):
    def responder(request):
        raise excecao(f"falhou ao acessar {request.url}", request=request)

    with pytest.raises(telegram.FalhaNoEnvio) as erro:
        _notificador(responder)("oi")
    assert erro.value.motivo == excecao.__name__
    assert TOKEN not in str(erro.value)
    assert erro.value.__cause__ is None
    assert erro.value.__context__ is None  # a exceção do httpx (com a URL) não fica encadeada


def test_timeout_de_5_segundos_por_tentativa():
    vistos = []

    def responder(request):
        vistos.append(request.extensions["timeout"])
        return httpx.Response(200, json={"ok": True})

    _notificador(responder)("oi")
    assert vistos == [{"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}]


# Segredo: token nunca nos logs -----------------------------------------------------------------


def test_loggers_do_httpx_e_httpcore_ficam_em_warning_ou_acima():
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


@pytest.mark.parametrize(
    "responder",
    [
        lambda request: httpx.Response(200, json={"ok": True}),
        lambda request: httpx.Response(401, json={"ok": False, "description": TOKEN}),
        lambda request: httpx.Response(200, json={"ok": False}),
    ],
)
def test_token_nao_aparece_em_nenhum_registro_de_log(cliente, caminho, caplog, responder):
    caplog.set_level(logging.DEBUG)
    r = _post(cliente(_notificador(responder)))
    assert r.status_code == 201
    assert TOKEN not in caplog.text
    assert TOKEN not in r.text
    for registro in caplog.records:
        assert TOKEN not in registro.getMessage()
        assert registro.exc_info is None


@pytest.mark.parametrize("excecao", [httpx.ConnectError, httpx.ReadTimeout])
def test_token_nao_aparece_no_log_quando_a_rede_falha(cliente, caminho, caplog, excecao):
    def responder(request):
        raise excecao(f"falhou ao acessar {request.url}", request=request)

    caplog.set_level(logging.DEBUG)
    r = _post(cliente(_notificador(responder)))
    assert r.status_code == 201
    assert _alerta(caminho)["estado"] == "falhou"
    assert TOKEN not in caplog.text
    assert "api.telegram.org" not in caplog.text
    falhas = [x.getMessage() for x in caplog.records if "alerta" in x.getMessage()]
    assert any(excecao.__name__ in m for m in falhas)


def test_log_de_falha_traz_tipo_e_alerta_id(cliente, caplog):
    caplog.set_level(logging.WARNING)
    r = _post(cliente(TelegramFalso(falhas=2, erro=RuntimeError("segredo-no-texto-da-excecao"))))
    alerta_id = r.json()["alerta_id"]
    assert "segredo-no-texto-da-excecao" not in caplog.text
    assert any("RuntimeError" in x.getMessage() and str(alerta_id) in x.getMessage() for x in caplog.records)


# Critério 5: tempo = enviado_em − criado_em ----------------------------------------------------------


def test_tempo_do_criterio_5_com_relogio_falso(cliente, caminho):
    relogio = RelogioFalso(T0, T0 + timedelta(seconds=2, milliseconds=500))
    _post(cliente(TelegramFalso(), relogio=relogio))

    alerta = _alerta(caminho)
    assert alerta["criado_em"] == "2026-10-26T15:00:00+00:00"
    criado = datetime.fromisoformat(alerta["criado_em"])
    enviado = datetime.fromisoformat(alerta["enviado_em"])
    assert enviado - criado == timedelta(seconds=2.5)


def test_criado_em_e_o_instante_de_entrada_e_nao_o_da_gravacao(cliente, caminho):
    # O relógio é lido uma vez na entrada; a classificação demora, mas criado_em não muda.
    class Lento(ClassificadorFalso):
        def __call__(self, texto):
            relogio.instantes[0] = T0 + timedelta(minutes=10)  # se lido de novo, mudaria
            return super().__call__(texto)

    relogio = RelogioFalso(T0, T0 + timedelta(seconds=1))
    _post(cliente(TelegramFalso(), classificar=Lento(), relogio=relogio))
    assert _alerta(caminho)["criado_em"] == "2026-10-26T15:00:00+00:00"


def test_relogio_de_producao_devolve_utc():
    agora = main.obter_relogio()()
    assert agora.utcoffset() == timedelta(0)


# Conexão SQLite da tarefa de fundo -------------------------------------------------------------------


def test_tarefa_de_fundo_abre_e_fecha_a_propria_conexao(cliente, caminho, monkeypatch):
    abertas = []
    original = banco.abrir

    def espia(c):
        conn = original(c)
        abertas.append(conn)
        return conn

    monkeypatch.setattr(banco, "abrir", espia)
    _post(cliente(TelegramFalso()))

    assert len(abertas) == 2  # a da requisição e a da tarefa de fundo
    assert abertas[0] is not abertas[1]
    for conn in abertas:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")  # fechada


def test_falha_no_banco_dentro_da_tarefa_nao_derruba_o_app(cliente, caminho, monkeypatch, caplog):
    def quebra(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    original = banco.mudar_estado_alerta
    monkeypatch.setattr(banco, "mudar_estado_alerta", quebra)
    caplog.set_level(logging.WARNING)
    c = cliente(TelegramFalso())
    r = _post(c)
    assert r.status_code == 201
    assert any("OperationalError" in x.getMessage() for x in caplog.records)
    monkeypatch.setattr(banco, "mudar_estado_alerta", original)
    assert _post(c, corpo={**CORPO, "id_externo": "ext-2"}).status_code == 201


def test_cada_tentativa_soma_um_antes_de_chamar_o_telegram(cliente, caminho):
    vistos = []

    class Espiao(TelegramFalso):
        def __call__(self, texto):
            vistos.append(_alerta(caminho)["tentativas"])
            return super().__call__(texto)

    _post(cliente(Espiao(falhas=1)))
    assert vistos == [1, 2]
