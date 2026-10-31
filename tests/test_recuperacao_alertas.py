"""Testes da recuperação de alertas (bloco 2.8). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (estados do alerta, decisões de 31/10/2026 sobre o envio ao
Telegram, a mensagem do alerta e a recuperação de alertas, e a tabela de contratos). Nenhum teste chama
o Telegram real: o notificador é falso ou usa httpx.MockTransport. A chave e o token são fictícios.
"""

import logging
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app import alertas, banco, main, telegram

SP = ZoneInfo("America/Sao_Paulo")
CHAVE = "chave-ficticia-da-recuperacao"
TOKEN = "123456789:TOKEN-FALSO-DA-RECUPERACAO-nao-e-real"
AGORA = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)

# DDL da tabela alertas ANTES do bloco 2.8 (sem tardio nem reconhecido_em), copiado de app/banco.py.
DDL_ANTIGO = """
CREATE TABLE avaliacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_externo TEXT NOT NULL UNIQUE,
    texto TEXT NOT NULL,
    nota INTEGER NOT NULL,
    recebida_em TEXT NOT NULL,
    prazo_resposta TEXT NOT NULL,
    categorias TEXT NOT NULL,
    criticidade TEXT NOT NULL,
    status_classificacao TEXT NOT NULL CHECK (status_classificacao IN ('ok', 'invalida'))
);
CREATE TABLE alertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    avaliacao_id INTEGER NOT NULL UNIQUE REFERENCES avaliacoes (id) ON DELETE RESTRICT,
    tipos TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendente' CHECK (estado IN ('pendente', 'enviado', 'falhou')),
    tentativas INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    enviado_em TEXT
);
"""


class Relogio:
    def __init__(self, instante):
        self.instante = instante

    def __call__(self):
        return self.instante


class TelegramFalso:
    def __init__(self, falhas=0):
        self.falhas = falhas
        self.mensagens = []

    def __call__(self, texto):
        self.mensagens.append(texto)
        if self.falhas > 0:
            self.falhas -= 1
            raise telegram.FalhaNoEnvio("status_502")


@pytest.fixture
def caminho(tmp_path):
    caminho = tmp_path / "radar.db"
    with closing(banco.abrir(caminho)):
        pass
    return caminho


_n = [0]


def _alerta(caminho, *, estado="pendente", tentativas=0, criado_em=None, tardio=0, reconhecido_em=None,
            texto="relato de teste", tipos=("saude",)):
    """Grava uma avaliação crítica e ajusta o alerta direto no banco. Devolve o alerta_id."""
    _n[0] += 1
    criado = criado_em or (AGORA - timedelta(hours=1))
    with closing(banco.abrir(caminho)) as conn:
        r = banco.gravar_avaliacao(
            conn, id_externo=f"ext-{_n[0]}", texto=texto, nota=1, recebida_em=criado.isoformat(),
            categorias=[], criticidade=list(tipos), status_classificacao="ok", alerta_criado_em=criado.isoformat(),
        )
        conn.execute(
            "UPDATE alertas SET estado = ?, tentativas = ?, tardio = ?, reconhecido_em = ? WHERE id = ?",
            (estado, tentativas, tardio, reconhecido_em, r.alerta_id),
        )
    return r.alerta_id


def _linha(caminho, alerta_id):
    with closing(banco.abrir(caminho)) as conn:
        return dict(conn.execute("SELECT * FROM alertas WHERE id = ?", (alerta_id,)).fetchone())


def _reenviar(caminho, notificador, agora=AGORA, estados=("pendente", "falhou")):
    return alertas.reenviar_elegiveis(caminho, notificador, Relogio(agora), estados=estados)


# ---------- migração ----------

def _banco_antigo(caminho):
    with closing(sqlite3.connect(caminho)) as conn:
        conn.executescript(DDL_ANTIGO)
        conn.execute(
            "INSERT INTO avaliacoes (id_externo, texto, nota, recebida_em, prazo_resposta, categorias, "
            "criticidade, status_classificacao) VALUES ('antiga', 't', 1, '2026-10-01T12:00:00+00:00', "
            "'2026-10-06T12:00:00+00:00', '[]', '[\"saude\"]', 'ok')"
        )
        conn.execute(
            "INSERT INTO alertas (avaliacao_id, tipos, estado, tentativas, criado_em, enviado_em) "
            "VALUES (1, '[\"saude\"]', 'enviado', 1, '2026-10-01T12:00:00+00:00', '2026-10-01T12:00:03+00:00')"
        )
        conn.commit()


def test_migracao_de_banco_antigo_preserva_dados_e_cria_as_colunas(tmp_path):
    caminho = tmp_path / "antigo.db"
    _banco_antigo(caminho)
    with closing(banco.abrir(caminho)) as conn:
        linha = dict(conn.execute("SELECT * FROM alertas").fetchone())
        colunas = {c[1]: c for c in conn.execute("PRAGMA table_info(alertas)")}
    assert linha["estado"] == "enviado" and linha["tentativas"] == 1
    assert linha["enviado_em"] == "2026-10-01T12:00:03+00:00"
    assert linha["tardio"] == 0 and linha["reconhecido_em"] is None
    assert colunas["tardio"][3] == 1  # NOT NULL
    assert colunas["reconhecido_em"][3] == 0  # aceita nulo


def test_migracao_e_idempotente(tmp_path):
    caminho = tmp_path / "antigo.db"
    _banco_antigo(caminho)
    for _ in range(3):
        with closing(banco.abrir(caminho)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM alertas").fetchone()[0] == 1


def test_coluna_tardio_migrada_so_aceita_0_ou_1(tmp_path):
    caminho = tmp_path / "antigo.db"
    _banco_antigo(caminho)
    with closing(banco.abrir(caminho)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE alertas SET tardio = 2")


def test_esquema_novo_ja_nasce_com_as_colunas(caminho):
    with closing(banco.abrir(caminho)) as conn:
        nomes = {c[1] for c in conn.execute("PRAGMA table_info(alertas)")}
        assert {"tardio", "reconhecido_em"} <= nomes
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO avaliacoes (id_externo, texto, nota, recebida_em, prazo_resposta, categorias, "
                "criticidade, status_classificacao) VALUES ('x', 't', 1, 'a', 'b', '[]', '[]', 'ok')"
            )
            conn.execute("INSERT INTO alertas (avaliacao_id, tipos, criado_em, tardio) VALUES (1, '[]', 'c', 5)")


def test_alerta_novo_nasce_com_tardio_0_e_sem_reconhecimento(caminho):
    a = _alerta(caminho)
    linha = _linha(caminho, a)
    assert linha["tardio"] == 0 and linha["reconhecido_em"] is None


# ---------- envio inicial inalterado ----------

def test_envio_inicial_com_duas_falhas_fica_falhou_e_nunca_toca_tardio(caminho):
    a = _alerta(caminho)
    tg = TelegramFalso(falhas=2)
    alertas.enviar_alerta(caminho, a, "msg", tg, Relogio(AGORA))
    linha = _linha(caminho, a)
    assert (linha["estado"], linha["tentativas"], linha["tardio"]) == ("falhou", 2, 0)
    assert len(tg.mensagens) == 2


def test_envio_inicial_fica_pendente_entre_as_duas_tentativas(caminho):
    a = _alerta(caminho)
    vistos = []

    def notificador(texto):
        vistos.append(_linha(caminho, a)["estado"])
        if len(vistos) == 1:
            raise telegram.FalhaNoEnvio("status_502")

    alertas.enviar_alerta(caminho, a, "msg", notificador, Relogio(AGORA))
    assert vistos == ["pendente", "pendente"]
    linha = _linha(caminho, a)
    assert (linha["estado"], linha["tentativas"], linha["tardio"]) == ("enviado", 2, 0)


# ---------- reenvio ----------

@pytest.mark.parametrize("estado", ["pendente", "falhou"])
def test_reenvio_com_sucesso(caminho, estado):
    a = _alerta(caminho, estado=estado, tentativas=2)
    tg = TelegramFalso()
    r = _reenviar(caminho, tg)
    linha = _linha(caminho, a)
    assert r == {"elegiveis": 1, "enviados": 1, "falharam": 0, "configurado": True}
    assert (linha["estado"], linha["tentativas"], linha["tardio"]) == ("enviado", 3, 1)
    assert linha["enviado_em"] == AGORA.isoformat()
    assert len(tg.mensagens) == 1  # UMA tentativa


@pytest.mark.parametrize("estado", ["pendente", "falhou"])
def test_reenvio_com_falha_deixa_falhou_e_marca_tardio(caminho, estado):
    a = _alerta(caminho, estado=estado, tentativas=1)
    tg = TelegramFalso(falhas=5)
    r = _reenviar(caminho, tg)
    linha = _linha(caminho, a)
    assert r == {"elegiveis": 1, "enviados": 0, "falharam": 1, "configurado": True}
    assert (linha["estado"], linha["tentativas"], linha["tardio"]) == ("falhou", 2, 1)
    assert linha["enviado_em"] is None
    assert len(tg.mensagens) == 1  # sem retentativa imediata


def test_tentativas_e_tardio_sao_gravados_antes_do_envio(caminho):
    a = _alerta(caminho, estado="falhou", tentativas=2)
    vistos = []

    def notificador(texto):
        linha = _linha(caminho, a)
        vistos.append((linha["tentativas"], linha["tardio"]))

    _reenviar(caminho, notificador)
    assert vistos == [(3, 1)]


def test_teto_de_5_tentativas(caminho):
    quatro = _alerta(caminho, estado="falhou", tentativas=4)
    cinco = _alerta(caminho, estado="falhou", tentativas=5)
    tg = TelegramFalso(falhas=1)
    r = _reenviar(caminho, tg)
    assert r["elegiveis"] == 1
    assert _linha(caminho, quatro)["tentativas"] == 5 and _linha(caminho, quatro)["estado"] == "falhou"
    linha5 = _linha(caminho, cinco)
    assert (linha5["tentativas"], linha5["tardio"]) == (5, 0)
    # a 5ª falhou: não é mais elegível
    assert _reenviar(caminho, TelegramFalso())["elegiveis"] == 0


@pytest.mark.parametrize("tentativas", [5, 7])
def test_pendente_com_5_ou_mais_vira_falhou_sem_envio_e_sem_tardio(caminho, tentativas):
    a = _alerta(caminho, estado="pendente", tentativas=tentativas)
    tg = TelegramFalso()
    r = _reenviar(caminho, tg)
    linha = _linha(caminho, a)
    assert (linha["estado"], linha["tentativas"], linha["tardio"]) == ("falhou", tentativas, 0)
    assert tg.mensagens == [] and r["elegiveis"] == 0


def test_varredura_isolada(caminho):
    esgotado = _alerta(caminho, estado="pendente", tentativas=5)
    normal = _alerta(caminho, estado="pendente", tentativas=1)
    enviado = _alerta(caminho, estado="enviado", tentativas=5)
    with closing(banco.abrir(caminho)) as conn:
        assert alertas.varrer_alertas(conn, AGORA) == 1
    assert _linha(caminho, esgotado)["estado"] == "falhou"
    assert _linha(caminho, normal)["estado"] == "pendente"
    assert _linha(caminho, enviado)["estado"] == "enviado"


@pytest.mark.parametrize("idade,elegivel", [
    (timedelta(minutes=4, seconds=59), False),
    (timedelta(minutes=5), False),  # "há mais de 5 minutos": exatamente 5 ainda não
    (timedelta(minutes=5, seconds=1), True),
    (timedelta(days=3), True),
])
def test_janela_de_5_minutos(caminho, idade, elegivel):
    a = _alerta(caminho, estado="pendente", tentativas=0, criado_em=AGORA - idade)
    r = _reenviar(caminho, TelegramFalso())
    assert r["elegiveis"] == (1 if elegivel else 0)
    assert _linha(caminho, a)["tardio"] == (1 if elegivel else 0)


def test_reconhecido_nao_e_elegivel(caminho):
    a = _alerta(caminho, estado="falhou", tentativas=2, reconhecido_em="2026-11-01T10:00:00+00:00")
    tg = TelegramFalso()
    assert _reenviar(caminho, tg)["elegiveis"] == 0
    assert tg.mensagens == [] and _linha(caminho, a)["tentativas"] == 2


def test_enviado_nao_e_elegivel(caminho):
    _alerta(caminho, estado="enviado", tentativas=1)
    assert _reenviar(caminho, TelegramFalso())["elegiveis"] == 0


def test_telegram_nao_configurado_nao_muda_nada(caminho, caplog):
    caplog.set_level(logging.WARNING)
    a = _alerta(caminho, estado="falhou", tentativas=2)
    esgotado = _alerta(caminho, estado="pendente", tentativas=5)
    r = _reenviar(caminho, None)
    assert r == {"elegiveis": 0, "enviados": 0, "falharam": 0, "configurado": False}
    linha = _linha(caminho, a)
    assert (linha["estado"], linha["tentativas"], linha["tardio"]) == ("falhou", 2, 0)
    assert _linha(caminho, esgotado)["estado"] == "falhou"  # a varredura de estado ainda roda
    assert any("não configurado" in r.getMessage() for r in caplog.records)


def test_erro_inesperado_do_notificador_conta_como_falha(caminho):
    a = _alerta(caminho, estado="falhou", tentativas=1)

    def explode(texto):
        raise RuntimeError("detalhe interno")

    assert _reenviar(caminho, explode)["falharam"] == 1
    assert _linha(caminho, a)["estado"] == "falhou"


def test_so_estados_pedidos(caminho):
    pendente = _alerta(caminho, estado="pendente", tentativas=0)
    falhou = _alerta(caminho, estado="falhou", tentativas=2)
    r = _reenviar(caminho, TelegramFalso(), estados=("pendente",))
    assert r["elegiveis"] == 1
    assert _linha(caminho, pendente)["estado"] == "enviado"
    assert _linha(caminho, falhou)["estado"] == "falhou" and _linha(caminho, falhou)["tardio"] == 0


# ---------- concorrência ----------

def test_reivindicacao_e_atomica_e_condicionada(caminho):
    a = _alerta(caminho, estado="falhou", tentativas=2)
    with closing(banco.abrir(caminho)) as c1, closing(banco.abrir(caminho)) as c2:
        e1 = alertas.listar_elegiveis(c1, AGORA, ("pendente", "falhou"))
        e2 = alertas.listar_elegiveis(c2, AGORA, ("pendente", "falhou"))
        assert [x["id"] for x in e1] == [x["id"] for x in e2] == [a]
        assert alertas.reivindicar(c1, e1[0], AGORA, ("pendente", "falhou")) is True
        assert alertas.reivindicar(c2, e2[0], AGORA, ("pendente", "falhou")) is False
    assert _linha(caminho, a)["tentativas"] == 3


def test_dois_reenvios_simultaneos_enviam_uma_vez(caminho):
    """Cobre o mesmo processo (trava + reivindicação). Entre processos, só a reivindicação, que protege
    quem leu antes da primeira; o risco restante é o aceito na especificação."""
    a = _alerta(caminho, estado="falhou", tentativas=2)
    enviados = []
    barreira = threading.Barrier(2, timeout=10)

    def notificador(texto):
        enviados.append(texto)
        time.sleep(0.2)

    resultados = []

    def rodar():
        barreira.wait()
        resultados.append(_reenviar(caminho, notificador))

    threads = [threading.Thread(target=rodar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(enviados) == 1
    assert sum(r["enviados"] for r in resultados) == 1
    assert _linha(caminho, a)["tentativas"] == 3


# ---------- mensagem ----------

def test_mensagem_do_envio_inicial_nao_tem_linha_de_criacao():
    m = alertas.montar_mensagem(7, ["saude"], "texto")
    linhas = m.split("\n")
    assert linhas[0] == "(dados simulados)"
    assert linhas[1] == "🔴 Alerta crítico: relato a verificar"
    assert "criado em" not in m.lower()


def test_mensagem_do_reenvio_tem_linha_de_criacao_no_fuso_de_sao_paulo():
    # 02/11 01:30 UTC = 01/11 22:30 em São Paulo (dia local anterior)
    m = alertas.montar_mensagem(7, ["saude"], "texto", criado_em="2026-11-02T01:30:00+00:00")
    assert m.split("\n")[-1] == "Alerta criado em 01/11 22:30 (horário de Brasília)"
    assert m.split("\n")[0] == "(dados simulados)"


def test_reenvio_manda_a_linha_de_criacao(caminho):
    _alerta(caminho, estado="falhou", tentativas=1, criado_em=datetime(2026, 11, 2, 1, 30, tzinfo=timezone.utc))
    tg = TelegramFalso()
    _reenviar(caminho, tg, agora=datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc))
    assert "Alerta criado em 01/11 22:30" in tg.mensagens[0]
    assert tg.mensagens[0].startswith("(dados simulados)\n🔴 Alerta crítico: relato a verificar")


def test_mensagem_do_reenvio_usa_tipos_e_texto_do_banco(caminho):
    _alerta(caminho, estado="falhou", tentativas=1, tipos=("alergia", "saude"), texto="b" * 400)
    tg = TelegramFalso()
    _reenviar(caminho, tg)
    m = tg.mensagens[0]
    assert "- alergia: relato a verificar" in m and "- saude: relato a verificar" in m
    assert "b" * 299 + "…" in m and "b" * 300 not in m


# ---------- rota POST /alertas/reenviar ----------

@pytest.fixture
def app(caminho, monkeypatch):
    monkeypatch.setenv("RADAR_DB", str(caminho))
    monkeypatch.setenv("RADAR_API_KEY", CHAVE)
    aplicacao = main.criar_app()
    aplicacao.dependency_overrides[main.obter_relogio] = lambda: Relogio(AGORA)
    return aplicacao


def _cliente(app, notificador):
    app.dependency_overrides[main.obter_notificador] = lambda: notificador
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("chave", [None, "", "errada"])
def test_rota_401_antes_de_tudo(app, caminho, chave):
    a = _alerta(caminho, estado="falhou", tentativas=1)
    tg = TelegramFalso()
    with _cliente(app, tg) as c:
        r = c.post("/alertas/reenviar", headers={} if chave is None else {"X-API-Key": chave})
    assert r.status_code == 401
    assert tg.mensagens == [] and _linha(caminho, a)["tentativas"] == 1


def test_rota_devolve_a_contagem_so_dos_elegiveis(app, caminho):
    _alerta(caminho, estado="falhou", tentativas=1)  # elegível, vai dar certo
    _alerta(caminho, estado="pendente", tentativas=0)  # elegível, vai falhar
    _alerta(caminho, estado="falhou", tentativas=5)  # teto
    _alerta(caminho, estado="falhou", tentativas=1, reconhecido_em="2026-11-01T10:00:00+00:00")
    _alerta(caminho, estado="pendente", criado_em=AGORA - timedelta(minutes=1))  # janela
    _alerta(caminho, estado="enviado", tentativas=1)
    chamadas = []

    def notificador(texto):
        chamadas.append(texto)
        if len(chamadas) == 2:
            raise telegram.FalhaNoEnvio("status_500")

    with _cliente(app, notificador) as c:
        r = c.post("/alertas/reenviar", headers={"X-API-Key": CHAVE})
    assert r.status_code == 200
    assert r.json() == {"elegiveis": 2, "enviados": 1, "falharam": 1, "configurado": True}


def test_rota_sem_telegram_configurado(app, caminho):
    a = _alerta(caminho, estado="falhou", tentativas=1)
    esgotado = _alerta(caminho, estado="pendente", tentativas=6)
    with _cliente(app, None) as c:
        r = c.post("/alertas/reenviar", headers={"X-API-Key": CHAVE})
    assert r.status_code == 200
    assert r.json() == {"elegiveis": 0, "enviados": 0, "falharam": 0, "configurado": False}
    assert _linha(caminho, a)["tentativas"] == 1 and _linha(caminho, a)["tardio"] == 0
    assert _linha(caminho, esgotado)["estado"] == "falhou"


def test_rota_sem_radar_db_da_500_generico(app, monkeypatch):
    monkeypatch.delenv("RADAR_DB")
    with _cliente(app, TelegramFalso()) as c:
        r = c.post("/alertas/reenviar", headers={"X-API-Key": CHAVE})
    assert r.status_code == 500 and r.json() == {"detail": "falha interna"}


def test_rota_com_radar_db_inexistente_da_500_e_nao_cria_o_arquivo(app, monkeypatch, tmp_path):
    ausente = tmp_path / "nao_existe.db"
    monkeypatch.setenv("RADAR_DB", str(ausente))
    with _cliente(app, TelegramFalso()) as c:
        assert c.post("/alertas/reenviar", headers={"X-API-Key": CHAVE}).status_code == 500
    assert not ausente.exists()


def test_token_do_telegram_e_chave_fora_dos_logs(app, caminho, caplog):
    caplog.set_level(logging.DEBUG)
    _alerta(caminho, estado="falhou", tentativas=1)
    _alerta(caminho, estado="falhou", tentativas=1)
    respostas = iter([httpx.Response(200, json={"ok": True}), httpx.Response(502, json={"ok": False})])
    notificador = telegram.NotificadorTelegram(TOKEN, "-1001", transporte=httpx.MockTransport(lambda r: next(respostas)))
    with _cliente(app, notificador) as c:
        r = c.post("/alertas/reenviar", headers={"X-API-Key": CHAVE})
    assert r.json()["enviados"] == 1 and r.json()["falharam"] == 1
    # Todos os registros, inclusive os do httpx, onde a URL com o token apareceria em INFO.
    todos = "\n".join(x.getMessage() for x in caplog.records)
    assert TOKEN not in todos and CHAVE not in todos


# ---------- subida do servidor ----------

def test_subida_reenvia_so_pendente_elegivel(caminho):
    pendente = _alerta(caminho, estado="pendente", tentativas=0)
    falhou = _alerta(caminho, estado="falhou", tentativas=2)
    recente = _alerta(caminho, estado="pendente", criado_em=AGORA - timedelta(minutes=2))
    tg = TelegramFalso()
    alertas.recuperar_na_subida(caminho, tg, Relogio(AGORA))
    assert _linha(caminho, pendente)["estado"] == "enviado" and _linha(caminho, pendente)["tardio"] == 1
    assert _linha(caminho, falhou)["estado"] == "falhou" and _linha(caminho, falhou)["tentativas"] == 2
    assert _linha(caminho, recente)["estado"] == "pendente" and _linha(caminho, recente)["tardio"] == 0
    assert len(tg.mensagens) == 1


def test_subida_nunca_levanta_excecao(caminho, caplog, monkeypatch):
    caplog.set_level(logging.ERROR)

    def quebra(*a, **k):
        raise sqlite3.OperationalError("falha simulada")

    monkeypatch.setattr(alertas, "reenviar_elegiveis", quebra)
    alertas.recuperar_na_subida(caminho, TelegramFalso(), Relogio(AGORA))  # não levanta
    assert any("subida" in r.getMessage() for r in caplog.records)


def test_lifespan_nao_bloqueia_a_subida(caminho, monkeypatch):
    monkeypatch.setenv("RADAR_DB", str(caminho))
    liberar = threading.Event()
    comecou = threading.Event()

    def recuperacao_lenta():
        comecou.set()
        liberar.wait(timeout=10)

    aplicacao = main.criar_app(recuperar_na_subida=recuperacao_lenta)
    inicio = time.monotonic()
    with TestClient(aplicacao) as c:
        assert time.monotonic() - inicio < 5
        assert comecou.wait(timeout=5)
        assert c.get("/rota-qualquer").status_code == 404  # o app responde com a recuperação em curso
    liberar.set()


def test_lifespan_com_falha_nao_derruba_a_subida(caminho, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)

    terminou = threading.Event()

    def explode():
        try:
            raise RuntimeError("detalhe que não pode ir para o log")
        finally:
            terminou.set()

    aplicacao = main.criar_app(recuperar_na_subida=explode)
    with TestClient(aplicacao) as c:
        assert c.get("/rota-qualquer").status_code == 404
        assert terminou.wait(timeout=5)
        for _ in range(50):  # o registro sai logo depois do finally, na mesma thread
            if any("falha na recuperação" in r.getMessage() for r in caplog.records):
                break
            time.sleep(0.05)
    mensagens = [r.getMessage() for r in caplog.records]
    assert any("falha na recuperação de alertas na subida" in m and "RuntimeError" in m for m in mensagens)
    assert not any("detalhe que não pode" in m for m in mensagens)


def test_criar_app_sem_parametro_nao_dispara_recuperacao(caminho, monkeypatch):
    chamado = []
    monkeypatch.setattr(main, "_recuperar_na_subida_padrao", lambda: chamado.append(1))
    with TestClient(main.criar_app()):
        pass
    assert chamado == []


def test_recuperacao_padrao_sem_radar_db_so_registra(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.delenv("RADAR_DB", raising=False)
    main._recuperar_na_subida_padrao()  # não levanta
    assert any("RADAR_DB" in r.getMessage() for r in caplog.records)


def test_recuperacao_padrao_sem_telegram_so_registra(caminho, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("RADAR_DB", str(caminho))
    # relativo ao relógio real: elegível pela janela, então só a falta do Telegram impede o envio
    a = _alerta(caminho, estado="pendente", tentativas=0,
                criado_em=datetime.now(timezone.utc) - timedelta(hours=1))
    main._recuperar_na_subida_padrao()
    assert _linha(caminho, a)["tentativas"] == 0
    assert any("não configurado" in r.getMessage() for r in caplog.records)


def test_app_do_modulo_liga_a_recuperacao_na_subida():
    assert main.app.state.recuperar_na_subida is main._recuperar_na_subida_padrao


# ---------- achados do revisor ----------

def test_falha_no_reenvio_nao_sobrescreve_enviado_gravado_por_outra_execucao(caminho):
    a = _alerta(caminho, estado="falhou", tentativas=1)

    def notificador(texto):
        # outra execução (por exemplo o envio inicial atrasado) entregou e gravou enviado no meio
        with closing(banco.abrir(caminho)) as conn:
            banco.mudar_estado_alerta(conn, a, "enviado", enviado_em=AGORA.isoformat())
        raise telegram.FalhaNoEnvio("status_502")

    _reenviar(caminho, notificador)
    assert _linha(caminho, a)["estado"] == "enviado"


def test_erro_do_banco_em_um_alerta_nao_interrompe_os_outros(caminho, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    primeiro = _alerta(caminho, estado="falhou", tentativas=1, criado_em=AGORA - timedelta(hours=3))
    segundo = _alerta(caminho, estado="falhou", tentativas=1, criado_em=AGORA - timedelta(hours=2))
    original = alertas.reivindicar

    def instavel(conn, alerta, agora, estados):
        if alerta["id"] == primeiro:
            raise sqlite3.OperationalError("database is locked")
        return original(conn, alerta, agora, estados)

    monkeypatch.setattr(alertas, "reivindicar", instavel)
    r = _reenviar(caminho, TelegramFalso())
    assert r["elegiveis"] == 2 and r["falharam"] == 1 and r["enviados"] == 1
    assert _linha(caminho, segundo)["estado"] == "enviado"
    assert any("OperationalError" in x.getMessage() for x in caplog.records)
    assert not any("database is locked" in x.getMessage() for x in caplog.records)
