"""Testes de scripts/reprocessar_invalidas.py e da atualização atômica em app/banco.py (bloco 2.9).

Fonte da verdade: docs/ESPECIFICACAO.md (decisões de 31/10/2026 sobre o reprocessamento das avaliações
`invalida` e a recuperação de alertas). Nenhum teste chama a API da Anthropic nem o Telegram real: o
classificador e o notificador são falsos. A chave e o token abaixo são fictícios.
"""

import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import reprocessar_invalidas  # noqa: E402

from app import banco, classificador, telegram  # noqa: E402
from app.classificador import ErroPermanenteDaAPI, ErroTecnicoEsgotado  # noqa: E402

AGORA = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
CHAVE_ANTHROPIC = "sk-ant-chave-ficticia-do-teste-nao-e-real"
TOKEN_TELEGRAM = "123456789:TOKEN-FALSO-DO-REPROCESSAMENTO-nao-e-real"
TEXTO_CLIENTE = "texto do cliente que nunca sai na saida do script"


class Relogio:
    def __call__(self):
        return AGORA


class Classificador:
    """Responde, em ordem, com a fila de respostas: dict de saída, None (inválida) ou exceção."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.textos = []

    def __call__(self, texto):
        self.textos.append(texto)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"saida": r, "tentativas": 1, "erros": [], "usos": []}


class TelegramFalso:
    def __init__(self, falhar=False):
        self.falhar = falhar
        self.mensagens = []

    def __call__(self, texto):
        self.mensagens.append(texto)
        if self.falhar:
            raise telegram.FalhaNoEnvio("status_502")


OK = {"categorias": ["atraso"], "criticidade": []}
CRITICO = {"categorias": ["item_faltando"], "criticidade": ["alergia", "saude"]}


def tecnica():
    return ErroTecnicoEsgotado("APITimeoutError", None, None)


@pytest.fixture
def caminho(tmp_path, monkeypatch):
    caminho = tmp_path / "radar.db"
    monkeypatch.setenv("RADAR_DB", str(caminho))
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHAVE_ANTHROPIC)
    with closing(banco.abrir(caminho)):
        pass
    return caminho


_n = [0]


def _avaliacao(caminho, status="invalida", texto=TEXTO_CLIENTE, criticidade=(), categorias=()):
    _n[0] += 1
    with closing(banco.abrir(caminho)) as conn:
        r = banco.gravar_avaliacao(
            conn, id_externo=f"ext-{_n[0]}", texto=texto, nota=1, recebida_em="2026-10-28T12:00:00+00:00",
            categorias=list(categorias), criticidade=list(criticidade), status_classificacao=status,
            alerta_criado_em="2026-10-28T12:00:00+00:00",
        )
    return r.avaliacao_id


def _linha(caminho, tabela, coluna, valor):
    with closing(banco.abrir(caminho)) as conn:
        linha = conn.execute(f"SELECT * FROM {tabela} WHERE {coluna} = ?", (valor,)).fetchone()
        return None if linha is None else dict(linha)


def _rodar(argv=(), classificar=None, notificador=None, **k):
    return reprocessar_invalidas.main(
        list(argv), classificar=classificar, notificador=notificador, relogio=Relogio(), **k
    )


# ---------- banco: atualização atômica ----------

def test_reprocessar_avaliacao_atualiza_e_cria_alerta_tardio(caminho):
    a = _avaliacao(caminho)
    with closing(banco.abrir(caminho)) as conn:
        alerta_id = banco.reprocessar_avaliacao(
            conn, a, categorias=["item_faltando"], criticidade=["saude"], alerta_criado_em=AGORA.isoformat()
        )
    av = _linha(caminho, "avaliacoes", "id", a)
    al = _linha(caminho, "alertas", "id", alerta_id)
    assert av["status_classificacao"] == "ok"
    assert av["categorias"] == '["item_faltando"]' and av["criticidade"] == '["saude"]'
    assert (al["estado"], al["tentativas"], al["tardio"], al["criado_em"]) == (
        "pendente", 0, 1, "2026-11-02T15:00:00+00:00")


def test_reprocessar_avaliacao_sem_critico_nao_cria_alerta(caminho):
    a = _avaliacao(caminho)
    with closing(banco.abrir(caminho)) as conn:
        assert banco.reprocessar_avaliacao(conn, a, categorias=["atraso"], criticidade=[],
                                           alerta_criado_em=AGORA.isoformat()) is None
    assert _linha(caminho, "alertas", "avaliacao_id", a) is None


def test_erro_no_meio_desfaz_tudo(caminho):
    a = _avaliacao(caminho)
    with closing(banco.abrir(caminho)) as conn:
        # um alerta já existente para a avaliação faz o INSERT violar o único por avaliacao_id
        conn.execute("INSERT INTO alertas (avaliacao_id, tipos, criado_em) VALUES (?, '[]', 'x')", (a,))
        with pytest.raises(sqlite3.IntegrityError):
            banco.reprocessar_avaliacao(conn, a, categorias=["atraso"], criticidade=["saude"],
                                        alerta_criado_em=AGORA.isoformat())
    av = _linha(caminho, "avaliacoes", "id", a)
    assert av["status_classificacao"] == "invalida"  # o UPDATE foi desfeito
    assert av["categorias"] == "[]"


def test_reprocessar_so_avaliacao_invalida(caminho):
    a = _avaliacao(caminho, status="ok")
    with closing(banco.abrir(caminho)) as conn:
        with pytest.raises(LookupError):
            banco.reprocessar_avaliacao(conn, a, categorias=[], criticidade=[], alerta_criado_em=AGORA.isoformat())


def test_reprocessar_valida_categorias(caminho):
    a = _avaliacao(caminho)
    with closing(banco.abrir(caminho)) as conn:
        with pytest.raises(ValueError):
            banco.reprocessar_avaliacao(conn, a, categorias=["nao_existe"], criticidade=[],
                                        alerta_criado_em=AGORA.isoformat())
    assert _linha(caminho, "avaliacoes", "id", a)["status_classificacao"] == "invalida"


# ---------- script ----------

def test_sucesso_sem_critico(caminho, capsys):
    a = _avaliacao(caminho)
    tg = TelegramFalso()
    assert _rodar(classificar=Classificador(OK), notificador=tg) == 0
    assert _linha(caminho, "avaliacoes", "id", a)["status_classificacao"] == "ok"
    assert _linha(caminho, "alertas", "avaliacao_id", a) is None
    assert tg.mensagens == []
    saida = capsys.readouterr().out
    assert "reprocessadas: 1" in saida and "alertas criados: 0" in saida


def test_sucesso_com_critico_cria_alerta_tardio_e_envia_uma_vez(caminho, capsys):
    a = _avaliacao(caminho)
    tg = TelegramFalso()
    assert _rodar(classificar=Classificador(CRITICO), notificador=tg) == 0
    al = _linha(caminho, "alertas", "avaliacao_id", a)
    assert (al["estado"], al["tentativas"], al["tardio"]) == ("enviado", 1, 1)
    assert al["criado_em"] == "2026-11-02T15:00:00+00:00"  # instante em que o script grava
    assert al["enviado_em"] == "2026-11-02T15:00:00+00:00"
    assert len(tg.mensagens) == 1
    m = tg.mensagens[0]
    assert m.startswith("(dados simulados)\n🔴 Alerta crítico: relato a verificar")
    assert "Alerta criado em" not in m  # o envio do script não é reenvio
    assert "- alergia: relato a verificar" in m
    saida = capsys.readouterr().out
    assert "alertas criados: 1" in saida and "alertas enviados: 1" in saida


def test_envio_do_reprocessamento_que_falha_deixa_falhou(caminho):
    a = _avaliacao(caminho)
    tg = TelegramFalso(falhar=True)
    assert _rodar(classificar=Classificador(CRITICO), notificador=tg) == 0
    al = _linha(caminho, "alertas", "avaliacao_id", a)
    assert (al["estado"], al["tentativas"], al["tardio"]) == ("falhou", 1, 1)
    assert len(tg.mensagens) == 1  # UMA tentativa, sem retentativa imediata


def test_tentativa_e_somada_antes_do_envio(caminho):
    a = _avaliacao(caminho)
    vistos = []

    def notificador(texto):
        vistos.append(_linha(caminho, "alertas", "avaliacao_id", a)["tentativas"])

    _rodar(classificar=Classificador(CRITICO), notificador=notificador)
    assert vistos == [1]


def test_telegram_nao_configurado_deixa_pendente_sem_tentativa(caminho, caplog):
    caplog.set_level(logging.WARNING)
    a = _avaliacao(caminho)
    assert _rodar(classificar=Classificador(CRITICO), notificador=None) == 0
    al = _linha(caminho, "alertas", "avaliacao_id", a)
    assert (al["estado"], al["tentativas"], al["tardio"]) == ("pendente", 0, 1)
    assert any("não configurado" in r.getMessage() for r in caplog.records)


def test_invalida_de_novo_mantem(caminho, capsys):
    a = _avaliacao(caminho)
    antes = _linha(caminho, "avaliacoes", "id", a)
    assert _rodar(classificar=Classificador(None), notificador=TelegramFalso()) == 0
    assert _linha(caminho, "avaliacoes", "id", a) == antes
    assert "voltaram a invalida: 1" in capsys.readouterr().out


def test_ordem_de_id_e_so_as_invalidas(caminho):
    _avaliacao(caminho, status="ok")
    a1 = _avaliacao(caminho, texto="primeira")
    a2 = _avaliacao(caminho, texto="segunda")
    c = Classificador(OK, OK)
    _rodar(classificar=c, notificador=TelegramFalso())
    assert c.textos == ["primeira", "segunda"]
    assert _linha(caminho, "avaliacoes", "id", a1)["status_classificacao"] == "ok"
    assert _linha(caminho, "avaliacoes", "id", a2)["status_classificacao"] == "ok"


def test_falha_tecnica_pula_e_continua(caminho, capsys):
    a1 = _avaliacao(caminho)
    a2 = _avaliacao(caminho)
    assert _rodar(classificar=Classificador(tecnica(), OK), notificador=TelegramFalso()) == 0
    assert _linha(caminho, "avaliacoes", "id", a1)["status_classificacao"] == "invalida"
    assert _linha(caminho, "avaliacoes", "id", a2)["status_classificacao"] == "ok"
    assert "falhas técnicas: 1" in capsys.readouterr().out


def test_tres_falhas_tecnicas_seguidas_param_com_codigo_diferente_de_zero(caminho, capsys):
    ids = [_avaliacao(caminho) for _ in range(5)]
    c = Classificador(tecnica(), tecnica(), tecnica(), OK, OK)
    codigo = _rodar(classificar=c, notificador=TelegramFalso())
    assert codigo == reprocessar_invalidas.FALHAS_SEGUIDAS and codigo != 0
    assert len(c.textos) == 3  # a 4ª nem foi tentada
    assert all(_linha(caminho, "avaliacoes", "id", i)["status_classificacao"] == "invalida" for i in ids)
    saida = capsys.readouterr()
    assert "3 falhas técnicas seguidas" in saida.out + saida.err


def test_sucesso_zera_a_sequencia_de_falhas_tecnicas(caminho):
    for _ in range(5):
        _avaliacao(caminho)
    c = Classificador(tecnica(), tecnica(), OK, tecnica(), tecnica())
    assert _rodar(classificar=c, notificador=TelegramFalso()) == 0
    assert len(c.textos) == 5


def test_invalida_de_novo_tambem_zera_a_sequencia(caminho):
    for _ in range(5):
        _avaliacao(caminho)
    c = Classificador(tecnica(), tecnica(), None, tecnica(), tecnica())
    assert _rodar(classificar=c, notificador=TelegramFalso()) == 0


def test_erro_permanente_para_na_hora(caminho, capsys):
    a1 = _avaliacao(caminho)
    _avaliacao(caminho)
    c = Classificador(ErroPermanenteDaAPI(401, "req_teste"), OK)
    codigo = _rodar(classificar=c, notificador=TelegramFalso())
    assert codigo == reprocessar_invalidas.ERRO_PERMANENTE and codigo != 0
    assert len(c.textos) == 1
    assert _linha(caminho, "avaliacoes", "id", a1)["status_classificacao"] == "invalida"
    saida = capsys.readouterr()
    assert "401" in saida.err


def test_dry_run_nao_chama_a_api_nem_altera_o_banco(caminho, capsys, monkeypatch):
    a1 = _avaliacao(caminho)
    a2 = _avaliacao(caminho)
    monkeypatch.setattr(classificador, "criar_cliente", lambda: pytest.fail("dry-run não cria cliente"))
    antes = Path(caminho).read_bytes()

    def proibido(texto):
        raise AssertionError("dry-run não pode classificar")

    assert reprocessar_invalidas.main(["--dry-run"], classificar=proibido, notificador=TelegramFalso(),
                                      relogio=Relogio()) == 0
    assert Path(caminho).read_bytes() == antes
    saida = capsys.readouterr().out
    assert str(a1) in saida and str(a2) in saida
    assert "2 avaliações" in saida and "até 6 chamadas" in saida  # 3 chamadas por avaliação na regra
    assert TEXTO_CLIENTE not in saida


def test_dry_run_com_o_padrao_nao_constroi_o_cliente(caminho, monkeypatch, capsys):
    _avaliacao(caminho)
    monkeypatch.setattr(classificador, "criar_cliente", lambda: pytest.fail("dry-run não cria cliente"))
    assert reprocessar_invalidas.main(["--dry-run"]) == 0


def test_limite(caminho):
    ids = [_avaliacao(caminho) for _ in range(4)]
    c = Classificador(OK, OK)
    assert _rodar(["--limite", "2"], classificar=c, notificador=TelegramFalso()) == 0
    estados = [_linha(caminho, "avaliacoes", "id", i)["status_classificacao"] for i in ids]
    assert estados == ["ok", "ok", "invalida", "invalida"]


def test_limite_no_dry_run(caminho, capsys):
    for _ in range(4):
        _avaliacao(caminho)
    reprocessar_invalidas.main(["--dry-run", "--limite", "1"], classificar=None, notificador=None, relogio=Relogio())
    assert "1 avaliações" in capsys.readouterr().out


@pytest.mark.parametrize("valor", ["0", "-1", "abc"])
def test_limite_invalido(caminho, valor):
    with pytest.raises(SystemExit) as e:
        reprocessar_invalidas.main(["--limite", valor], classificar=Classificador(), notificador=None,
                                   relogio=Relogio())
    assert e.value.code != 0


def test_sem_radar_db(monkeypatch, capsys):
    monkeypatch.delenv("RADAR_DB", raising=False)
    assert _rodar(classificar=Classificador(), notificador=None) != 0
    assert "RADAR_DB" in capsys.readouterr().err


def test_banco_inexistente_nao_e_criado(tmp_path, monkeypatch):
    ausente = tmp_path / "nao_existe.db"
    monkeypatch.setenv("RADAR_DB", str(ausente))
    assert _rodar(classificar=Classificador(), notificador=None) != 0
    assert not ausente.exists()


def test_sem_invalidas(caminho, capsys):
    _avaliacao(caminho, status="ok")
    assert _rodar(classificar=Classificador(), notificador=None) == 0
    assert "reprocessadas: 0" in capsys.readouterr().out


def test_nada_de_segredo_nem_texto_na_saida_e_no_log(caminho, capsys, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("RADAR_TELEGRAM_TOKEN", TOKEN_TELEGRAM)
    for _ in range(4):
        _avaliacao(caminho)
    c = Classificador(CRITICO, None, tecnica(), ErroPermanenteDaAPI(401, "req_x"))
    _rodar(classificar=c, notificador=TelegramFalso(falhar=True))
    reprocessar_invalidas.main(["--dry-run"], classificar=None, notificador=None, relogio=Relogio())
    saida = capsys.readouterr()
    tudo = saida.out + saida.err + "\n".join(r.getMessage() for r in caplog.records)
    assert CHAVE_ANTHROPIC not in tudo and TOKEN_TELEGRAM not in tudo
    assert TEXTO_CLIENTE not in tudo


def test_chave_da_anthropic_ausente_sai_com_mensagem_curta(caminho, monkeypatch, capsys):
    _avaliacao(caminho)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert reprocessar_invalidas.main([], notificador=None, relogio=Relogio()) != 0
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_resumo_final_tem_todos_os_campos(caminho, capsys):
    for _ in range(3):
        _avaliacao(caminho)
    _rodar(classificar=Classificador(CRITICO, None, tecnica()), notificador=TelegramFalso())
    saida = capsys.readouterr().out
    for campo in ("reprocessadas: 1", "voltaram a invalida: 1", "falhas técnicas: 1",
                  "alertas criados: 1", "alertas enviados: 1"):
        assert campo in saida, campo


def test_versao_do_prompt_e_a_mesma_do_app():
    from app import main

    assert reprocessar_invalidas.VERSAO_PROMPT == main.VERSAO_PROMPT


# ---------- achados do revisor ----------

DDL_ANTIGO = """
CREATE TABLE avaliacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, id_externo TEXT NOT NULL UNIQUE, texto TEXT NOT NULL,
    nota INTEGER NOT NULL, recebida_em TEXT NOT NULL, prazo_resposta TEXT NOT NULL, categorias TEXT NOT NULL,
    criticidade TEXT NOT NULL, status_classificacao TEXT NOT NULL);
CREATE TABLE alertas (id INTEGER PRIMARY KEY AUTOINCREMENT, avaliacao_id INTEGER NOT NULL UNIQUE, tipos TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendente', tentativas INTEGER NOT NULL DEFAULT 0, criado_em TEXT NOT NULL,
    enviado_em TEXT);
INSERT INTO avaliacoes (id_externo, texto, nota, recebida_em, prazo_resposta, categorias, criticidade,
    status_classificacao) VALUES ('antiga', 't', 1, '2026-10-01T12:00:00+00:00', '2026-10-06T12:00:00+00:00',
    '[]', '[]', 'invalida');
"""


def test_dry_run_nao_migra_um_banco_antigo(tmp_path, monkeypatch, capsys):
    antigo = tmp_path / "antigo.db"
    with closing(sqlite3.connect(antigo)) as conn:
        conn.executescript(DDL_ANTIGO)
        conn.commit()
    monkeypatch.setenv("RADAR_DB", str(antigo))
    antes = antigo.read_bytes()
    assert reprocessar_invalidas.main(["--dry-run"], classificar=None, notificador=None, relogio=Relogio()) == 0
    assert antigo.read_bytes() == antes
    assert "1 avaliações" in capsys.readouterr().out


def test_erro_de_banco_numa_avaliacao_nao_derruba_o_script(caminho, monkeypatch, capsys):
    a1 = _avaliacao(caminho)
    a2 = _avaliacao(caminho)
    original = banco.reprocessar_avaliacao

    def instavel(conn, avaliacao_id, **k):
        if avaliacao_id == a1:
            raise sqlite3.OperationalError("database is locked")
        return original(conn, avaliacao_id, **k)

    monkeypatch.setattr(banco, "reprocessar_avaliacao", instavel)
    assert _rodar(classificar=Classificador(OK, OK), notificador=TelegramFalso()) == 0
    assert _linha(caminho, "avaliacoes", "id", a2)["status_classificacao"] == "ok"
    saida = capsys.readouterr()
    assert "erros de banco: 1" in saida.out and "reprocessadas: 1" in saida.out
    assert "database is locked" not in saida.out + saida.err


def test_excecao_inesperada_do_classificador_conta_como_falha_tecnica(caminho, capsys):
    _avaliacao(caminho)
    _avaliacao(caminho)
    assert _rodar(classificar=Classificador(RuntimeError("detalhe interno"), OK), notificador=TelegramFalso()) == 0
    saida = capsys.readouterr()
    assert "falhas técnicas: 1" in saida.out and "detalhe interno" not in saida.out + saida.err


def test_resumo_conta_alertas_que_falharam_e_os_pendentes(caminho, capsys):
    _avaliacao(caminho)
    _rodar(classificar=Classificador(CRITICO), notificador=TelegramFalso(falhar=True))
    _avaliacao(caminho)
    _rodar(classificar=Classificador(CRITICO), notificador=None)
    saida = capsys.readouterr().out
    assert "alertas que falharam: 1" in saida
    assert "alertas pendentes (Telegram não configurado): 1" in saida


def test_update_de_tentativas_que_falha_nao_finge_falhou(caminho, monkeypatch, caplog):
    from app import alertas

    caplog.set_level(logging.ERROR)
    a = _avaliacao(caminho)
    with closing(banco.abrir(caminho)) as conn:
        alerta_id = banco.reprocessar_avaliacao(conn, a, categorias=[], criticidade=["saude"],
                                                alerta_criado_em=AGORA.isoformat())
    abrir = banco.abrir

    def abrir_travado(c):
        conn = abrir(c)

        class Conexao:
            def __getattr__(self, nome):
                return getattr(conn, nome)

            def execute(self, sql, *args):
                if sql.startswith("UPDATE alertas SET tentativas"):
                    raise sqlite3.OperationalError("database is locked")
                return conn.execute(sql, *args)

            def close(self):
                conn.close()

        return Conexao()

    monkeypatch.setattr(banco, "abrir", abrir_travado)
    tg = TelegramFalso()
    assert alertas.enviar_alerta_reprocessado(caminho, alerta_id, "m", tg, Relogio()) == "pendente"
    assert tg.mensagens == []  # sem a tentativa gravada, nada é enviado
    monkeypatch.setattr(banco, "abrir", abrir)
    al = _linha(caminho, "alertas", "id", alerta_id)
    assert (al["estado"], al["tentativas"]) == ("pendente", 0)
    assert any("OperationalError" in r.getMessage() for r in caplog.records)


def test_segredos_fora_com_notificador_real_e_falha_de_rede(caminho, capsys, caplog):
    import httpx

    caplog.set_level(logging.DEBUG)
    _avaliacao(caminho)

    def quebra(request):
        raise httpx.ConnectError(f"falhou ao conectar em {request.url}")

    notificador = telegram.NotificadorTelegram(TOKEN_TELEGRAM, "-1001", transporte=httpx.MockTransport(quebra))
    _rodar(classificar=Classificador(CRITICO), notificador=notificador)
    saida = capsys.readouterr()
    tudo = saida.out + saida.err + "\n".join(r.getMessage() for r in caplog.records)
    assert TOKEN_TELEGRAM not in tudo and CHAVE_ANTHROPIC not in tudo and TEXTO_CLIENTE not in tudo
