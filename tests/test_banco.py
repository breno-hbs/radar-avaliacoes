"""Testes da camada de persistência SQLite (bloco 2.1 da Fase 2).

Fonte da verdade: docs/ESPECIFICACAO.md, seção "Contratos e dados". Dados sintéticos.
Decisões do dono adotadas aqui (a especificação será atualizada em PR de documentação à parte):
trecho_exibido e posicao NOT NULL; ON DELETE RESTRICT; ação sem evidências permitida pelo
esquema; recebida_em com fuso, gravada normalizada em UTC (+00:00).
"""

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from app import banco


@pytest.fixture
def caminho(tmp_path):
    return tmp_path / "radar.db"


@pytest.fixture
def conn(caminho):
    c = banco.abrir(caminho)
    yield c
    c.close()


def _grava(conn, id_externo="ext-1", recebida_em="2026-10-26T12:00:00-03:00", **extra):
    dados = dict(
        id_externo=id_externo,
        texto="O pedido chegou frio e faltou um item.",
        nota=2,
        recebida_em=recebida_em,
        categorias=["qualidade_temperatura", "item_faltando"],
        criticidade=[],
        status_classificacao="ok",
    )
    dados.update(extra)
    return banco.gravar_avaliacao(conn, **dados)


def _colunas(conn, tabela):
    return {l["name"] for l in conn.execute(f"PRAGMA table_info({tabela})")}


def _conta(conn, tabela):
    return conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]


def _acao(conn, categoria="atraso"):
    cur = conn.execute(
        "INSERT INTO acoes (categoria, semana_inicio, estado, criada_em) "
        "VALUES (?, '2026-10-26', 'pendente', '2026-10-27T11:00:00+00:00')",
        (categoria,),
    )
    return cur.lastrowid


class _ConexaoQueFalha:
    """Delega tudo à conexão real, mas falha ao executar os comandos SQL indicados."""

    def __init__(self, real, falha_em):
        self._real = real
        self._falha_em = set(falha_em)

    def execute(self, sql, *args):
        if sql in self._falha_em:
            raise sqlite3.OperationalError(f"falha simulada em {sql}")
        return self._real.execute(sql, *args)

    def __getattr__(self, nome):
        return getattr(self._real, nome)


# a) esquema -----------------------------------------------------------------


def test_tabelas_existem_com_as_colunas_da_especificacao(conn):
    assert _colunas(conn, "avaliacoes") == {
        "id", "id_externo", "texto", "nota", "recebida_em", "prazo_resposta",
        "categorias", "criticidade", "status_classificacao",
    }
    assert _colunas(conn, "alertas") == {
        "id", "avaliacao_id", "tipos", "estado", "tentativas", "criado_em", "enviado_em",
        "tardio", "reconhecido_em",  # bloco 2.8 (decisão de 31/10/2026 sobre a recuperação de alertas)
    }
    assert _colunas(conn, "acoes") == {
        "id", "categoria", "semana_inicio", "estado", "criada_em", "concluida_em", "nota",
    }
    assert _colunas(conn, "acao_avaliacoes") == {
        "acao_id", "avaliacao_id", "trecho_exibido", "posicao",
    }
    assert _colunas(conn, "tokens_painel") == {
        "id", "token_hash", "escopo", "criado_em", "expira_em", "revogado_em",
    }


def test_avaliacoes_nao_tem_confianca(conn):
    assert "confianca" not in _colunas(conn, "avaliacoes")


def test_status_classificacao_aceita_ok_e_invalida(conn):
    _grava(conn, "ext-ok", status_classificacao="ok")
    _grava(conn, "ext-inv", status_classificacao="invalida", categorias=[], criticidade=[])
    assert _conta(conn, "avaliacoes") == 2


@pytest.mark.parametrize("status", ["duvida", "OK", "Invalida", "", None, 1, ["ok"]])
def test_status_classificacao_fora_de_ok_e_invalida_levanta_valueerror(conn, status):
    with pytest.raises(ValueError):
        _grava(conn, "ext-x", status_classificacao=status)
    assert _conta(conn, "avaliacoes") == 0
    assert conn.in_transaction is False


def test_check_do_esquema_tambem_recusa_status_invalido(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO avaliacoes (id_externo, texto, nota, recebida_em, prazo_resposta, "
            "categorias, criticidade, status_classificacao) "
            "VALUES ('x', 't', 1, '2026-10-26T12:00:00+00:00', '2026-10-31T12:00:00+00:00', "
            "'[]', '[]', 'duvida')"
        )


def test_listas_sao_guardadas_como_json_em_texto(conn):
    _grava(conn, categorias=["atraso"], criticidade=["saude", "alergia"])
    linha = conn.execute("SELECT categorias, criticidade FROM avaliacoes").fetchone()
    assert json.loads(linha["categorias"]) == ["atraso"]
    assert json.loads(linha["criticidade"]) == ["saude", "alergia"]


# validação das listas contra app/contrato.py -------------------------------------


@pytest.mark.parametrize("categoria", ["atraso", "item_faltando", "qualidade_temperatura",
                                       "embalagem", "atendimento", "entregador"])
def test_as_seis_categorias_sao_aceitas(conn, categoria):
    _grava(conn, categorias=[categoria])
    assert _conta(conn, "avaliacoes") == 1


@pytest.mark.parametrize("categorias", [["preco"], ["atraso", "preco"], [""], ["ATRASO"]])
def test_categoria_fora_do_conjunto_e_recusada(conn, categorias):
    with pytest.raises(ValueError):
        _grava(conn, categorias=categorias)
    assert _conta(conn, "avaliacoes") == 0


def test_categoria_repetida_e_recusada(conn):
    with pytest.raises(ValueError):
        _grava(conn, categorias=["atraso", "atraso"])
    assert _conta(conn, "avaliacoes") == 0


@pytest.mark.parametrize("criticidade", [["fraude"], ["saude", "fraude"], ["Saude"]])
def test_criticidade_fora_do_conjunto_e_recusada(conn, criticidade):
    with pytest.raises(ValueError):
        _grava(conn, criticidade=criticidade)
    assert _conta(conn, "avaliacoes") == 0
    assert _conta(conn, "alertas") == 0


def test_criticidade_repetida_e_recusada(conn):
    with pytest.raises(ValueError):
        _grava(conn, criticidade=["saude", "saude"])
    assert _conta(conn, "alertas") == 0


@pytest.mark.parametrize("valor", [None, "atraso", 3, {"atraso": 1}, [1], [None]])
def test_categorias_que_nao_sao_lista_de_texto_sao_recusadas(conn, valor):
    with pytest.raises(ValueError):
        _grava(conn, categorias=valor)


@pytest.mark.parametrize("valor", [None, "saude", 3, [1], [None]])
def test_criticidade_que_nao_e_lista_de_texto_e_recusada(conn, valor):
    with pytest.raises(ValueError):
        _grava(conn, criticidade=valor)


def test_invalida_exige_categorias_e_criticidade_vazias(conn):
    with pytest.raises(ValueError):
        _grava(conn, status_classificacao="invalida", categorias=["atraso"], criticidade=[])
    with pytest.raises(ValueError):
        _grava(conn, status_classificacao="invalida", categorias=[], criticidade=["saude"])
    assert _conta(conn, "avaliacoes") == 0
    assert _conta(conn, "alertas") == 0


def test_invalida_com_listas_vazias_e_gravada_sem_alerta(conn):
    r = _grava(conn, status_classificacao="invalida", categorias=[], criticidade=[])
    assert r.criada is True
    assert r.alerta_id is None
    assert _conta(conn, "alertas") == 0


# entrada que não é texto ----------------------------------------------------------


@pytest.mark.parametrize("campo, valor", [
    ("id_externo", None), ("id_externo", 123),
    ("texto", None), ("texto", 5),
    ("recebida_em", None), ("recebida_em", 123), ("recebida_em", b"2026-10-26T12:00:00Z"),
    ("nota", "2"), ("nota", 2.5), ("nota", True), ("nota", None),
])
def test_entrada_de_tipo_errado_levanta_valueerror(conn, campo, valor):
    with pytest.raises(ValueError):
        _grava(conn, **{campo: valor})
    assert _conta(conn, "avaliacoes") == 0


# b) chaves estrangeiras ------------------------------------------------------


def test_toda_conexao_tem_foreign_keys_ligado(caminho):
    for _ in range(3):
        c = banco.abrir(caminho)
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        c.close()


def test_evidencia_de_avaliacao_inexistente_e_recusada(conn):
    acao_id = _acao(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO acao_avaliacoes (acao_id, avaliacao_id, trecho_exibido, posicao) "
            "VALUES (?, 9999, 'trecho', 1)",
            (acao_id,),
        )
    assert _conta(conn, "acao_avaliacoes") == 0


def test_evidencia_de_acao_inexistente_e_recusada(conn):
    r = _grava(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO acao_avaliacoes (acao_id, avaliacao_id, trecho_exibido, posicao) "
            "VALUES (9999, ?, 'trecho', 1)",
            (r.avaliacao_id,),
        )


def test_alerta_de_avaliacao_inexistente_e_recusado(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alertas (avaliacao_id, tipos, estado, criado_em) "
            "VALUES (9999, '[\"saude\"]', 'pendente', '2026-10-26T12:00:00+00:00')"
        )


# B2) ON DELETE RESTRICT -------------------------------------------------------


def _on_delete(conn, tabela):
    return {l["table"]: l["on_delete"] for l in conn.execute(f"PRAGMA foreign_key_list({tabela})")}


def test_on_delete_restrict_declarado_nas_chaves_estrangeiras(conn):
    assert _on_delete(conn, "acao_avaliacoes") == {"acoes": "RESTRICT", "avaliacoes": "RESTRICT"}
    assert _on_delete(conn, "alertas") == {"avaliacoes": "RESTRICT"}


def test_apagar_avaliacao_com_alerta_e_recusado(conn):
    r = _grava(conn, criticidade=["saude"])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM avaliacoes WHERE id = ?", (r.avaliacao_id,))
    assert _conta(conn, "avaliacoes") == 1
    assert _conta(conn, "alertas") == 1


def test_apagar_avaliacao_que_e_evidencia_e_recusado(conn):
    av = _grava(conn).avaliacao_id
    _evidencia(conn, _acao(conn), av, 1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM avaliacoes WHERE id = ?", (av,))
    assert _conta(conn, "avaliacoes") == 1


def test_apagar_acao_com_evidencias_e_recusado(conn):
    acao_id = _acao(conn)
    _evidencia(conn, acao_id, _grava(conn).avaliacao_id, 1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM acoes WHERE id = ?", (acao_id,))
    assert _conta(conn, "acoes") == 1
    assert _conta(conn, "acao_avaliacoes") == 1


def test_apagar_linha_sem_referencia_e_permitido(conn):
    r = _grava(conn)
    conn.execute("DELETE FROM avaliacoes WHERE id = ?", (r.avaliacao_id,))
    assert _conta(conn, "avaliacoes") == 0


# c) acao_avaliacoes ----------------------------------------------------------


def _evidencia(conn, acao_id, avaliacao_id, posicao, trecho="trecho"):
    conn.execute(
        "INSERT INTO acao_avaliacoes (acao_id, avaliacao_id, trecho_exibido, posicao) "
        "VALUES (?, ?, ?, ?)",
        (acao_id, avaliacao_id, trecho, posicao),
    )


def test_evidencia_repetida_para_a_mesma_acao_e_avaliacao_e_recusada(conn):
    acao_id = _acao(conn)
    av = _grava(conn).avaliacao_id
    _evidencia(conn, acao_id, av, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _evidencia(conn, acao_id, av, 2)


def test_posicao_repetida_na_mesma_acao_e_recusada(conn):
    acao_id = _acao(conn)
    a1 = _grava(conn, "ext-1").avaliacao_id
    a2 = _grava(conn, "ext-2").avaliacao_id
    _evidencia(conn, acao_id, a1, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _evidencia(conn, acao_id, a2, 1)


def test_mesma_avaliacao_pode_ser_evidencia_de_varias_acoes(conn):
    av = _grava(conn).avaliacao_id
    # bloco 4.2: uma ação por (categoria, semana); as duas ações aqui são de categorias diferentes
    _evidencia(conn, _acao(conn), av, 1)
    _evidencia(conn, _acao(conn, "embalagem"), av, 1)
    assert _conta(conn, "acao_avaliacoes") == 2


@pytest.mark.parametrize("posicao", [1, 2, 3])
def test_posicao_de_1_a_3_e_aceita(conn, posicao):
    _evidencia(conn, _acao(conn), _grava(conn).avaliacao_id, posicao)


@pytest.mark.parametrize("posicao", [0, 4, -1])
def test_posicao_fora_de_1_a_3_e_recusada(conn, posicao):
    with pytest.raises(sqlite3.IntegrityError):
        _evidencia(conn, _acao(conn), _grava(conn).avaliacao_id, posicao)


def test_trecho_exibido_nulo_e_recusado(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _evidencia(conn, _acao(conn), _grava(conn).avaliacao_id, 1, trecho=None)


def test_posicao_nula_e_recusada(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _evidencia(conn, _acao(conn), _grava(conn).avaliacao_id, None)


def test_acao_sem_evidencias_e_permitida_pelo_esquema(conn):
    acao_id = _acao(conn)
    assert acao_id
    assert _conta(conn, "acao_avaliacoes") == 0


# acoes.estado -----------------------------------------------------------------


@pytest.mark.parametrize("estado", ["pendente", "em_andamento", "concluida"])
def test_estados_validos_da_acao(conn, estado):
    acao_id = _acao(conn)
    conn.execute("UPDATE acoes SET estado = ? WHERE id = ?", (estado, acao_id))
    assert conn.execute("SELECT estado FROM acoes WHERE id = ?", (acao_id,)).fetchone()[0] == estado


@pytest.mark.parametrize("estado", ["cancelada", "Pendente", "", "enviado"])
def test_estado_invalido_da_acao_e_recusado_no_insert(conn, estado):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO acoes (categoria, semana_inicio, estado, criada_em) "
            "VALUES ('atraso', '2026-10-26', ?, '2026-10-27T11:00:00+00:00')",
            (estado,),
        )
    assert _conta(conn, "acoes") == 0


def test_estado_invalido_da_acao_e_recusado_no_update(conn):
    acao_id = _acao(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE acoes SET estado = 'cancelada' WHERE id = ?", (acao_id,))
    assert conn.execute("SELECT estado FROM acoes").fetchone()[0] == "pendente"


# d) duplicidade --------------------------------------------------------------


def test_mesma_avaliacao_duas_vezes_nao_cria_nova_avaliacao_nem_novo_alerta(conn):
    primeira = _grava(conn, criticidade=["saude"])
    segunda = _grava(conn, criticidade=["saude"])

    assert primeira.criada is True
    assert segunda.criada is False
    assert segunda.avaliacao_id == primeira.avaliacao_id
    assert segunda.alerta_id is None
    assert _conta(conn, "avaliacoes") == 1
    assert _conta(conn, "alertas") == 1


def test_duplicata_nao_altera_a_avaliacao_original(conn):
    _grava(conn, texto="texto original")
    _grava(conn, texto="texto diferente")
    assert banco.buscar_avaliacao_por_id_externo(conn, "ext-1")["texto"] == "texto original"


def test_duplicata_com_criticidade_nao_cria_alerta_para_avaliacao_que_nao_tinha(conn):
    # A regra "reenvio nunca altera a avaliação" depende da decisão em aberto sobre
    # reprocessar avaliações invalidas (docs/ESPECIFICACAO.md).
    _grava(conn)
    segunda = _grava(conn, criticidade=["saude"])
    assert segunda.criada is False
    assert _conta(conn, "alertas") == 0


def test_buscar_por_id_externo_inexistente_devolve_none(conn):
    assert banco.buscar_avaliacao_por_id_externo(conn, "nao-existe") is None


def test_duplicidade_com_duas_conexoes_ao_mesmo_arquivo(caminho):
    c1 = banco.abrir(caminho)
    c2 = banco.abrir(caminho)
    primeira = _grava(c1, criticidade=["saude"])
    segunda = _grava(c2, criticidade=["saude"])

    assert primeira.criada is True
    assert segunda.criada is False
    assert segunda.avaliacao_id == primeira.avaliacao_id
    assert segunda.alerta_id is None
    assert _conta(c1, "avaliacoes") == 1
    assert _conta(c2, "alertas") == 1
    c1.close()
    c2.close()


def _em_threads(alvos, tempo_maximo=15):
    """Roda cada alvo(barreira) em uma thread, alinhados por uma Barrier com timeout.

    Exceções das threads são capturadas e relançadas na thread principal; uma thread presa
    faz o teste falhar em vez de travar a suíte.
    """
    barreira = threading.Barrier(len(alvos), timeout=5)
    resultados = [None] * len(alvos)
    erros = []

    def executar(i, alvo):
        try:
            resultados[i] = alvo(barreira)
        except BaseException as e:  # noqa: BLE001 - relançada na thread principal
            erros.append(e)

    threads = [threading.Thread(target=executar, args=(i, a), daemon=True) for i, a in enumerate(alvos)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(tempo_maximo)
    if any(t.is_alive() for t in threads):
        pytest.fail("thread presa: possível deadlock ou lock não liberado")
    reais = [e for e in erros if not isinstance(e, threading.BrokenBarrierError)]
    if erros:
        raise (reais or erros)[0]
    return resultados


def test_duplicidade_em_gravacoes_simultaneas(caminho):
    banco.abrir(caminho).close()

    def gravar(barreira):
        c = banco.abrir(caminho)
        try:
            barreira.wait()
            return _grava(c, criticidade=["alergia"])
        finally:
            c.close()

    resultados = _em_threads([gravar, gravar])

    assert sorted(r.criada for r in resultados) == [False, True]
    assert len({r.avaliacao_id for r in resultados}) == 1
    assert sorted(r.alerta_id is None for r in resultados) == [False, True]
    c = banco.abrir(caminho)
    try:
        assert _conta(c, "avaliacoes") == 1
        assert _conta(c, "alertas") == 1
    finally:
        c.close()


def test_segunda_conexao_espera_o_lock_e_enxerga_a_linha_na_consulta_previa(caminho, monkeypatch):
    # Prova de que não há corrida entre a consulta por id_externo e o INSERT: a segunda
    # conexão fica parada no BEGIN IMMEDIATE e, quando entra, a consulta prévia já vê a linha.
    c1 = banco.abrir(caminho)
    consultas = []
    original = banco._id_por_externo

    def registra(c, id_externo):
        consultas.append(threading.current_thread().name)
        return original(c, id_externo)

    monkeypatch.setattr(banco, "_id_por_externo", registra)
    resultados, erros = [], []

    def segunda():
        c2 = banco.abrir(caminho)
        try:
            resultados.append(_grava(c2, criticidade=["saude"]))
        except BaseException as e:  # noqa: BLE001
            erros.append(e)
        finally:
            c2.close()

    t = threading.Thread(target=segunda, name="segunda-conexao", daemon=True)
    with banco._transacao(c1):
        c1.execute(
            "INSERT INTO avaliacoes (id_externo, texto, nota, recebida_em, prazo_resposta, "
            "categorias, criticidade, status_classificacao) "
            "VALUES ('ext-1', 't', 1, '2026-10-26T12:00:00+00:00', '2026-10-31T12:00:00+00:00', "
            "'[]', '[]', 'ok')"
        )
        t.start()
        t.join(0.5)
        assert t.is_alive(), "a segunda conexão deveria estar esperando o lock"
    t.join(15)
    assert not t.is_alive()
    assert erros == []
    assert resultados[0].criada is False
    assert resultados[0].alerta_id is None
    assert consultas.count("segunda-conexao") == 1  # uma consulta só: nenhum plano B depois do INSERT
    assert _conta(c1, "avaliacoes") == 1
    assert _conta(c1, "alertas") == 0
    c1.close()


# e) alertas ------------------------------------------------------------------


def test_alerta_e_unico_por_avaliacao(conn):
    r = _grava(conn, criticidade=["saude"])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alertas (avaliacao_id, tipos, estado, criado_em) "
            "VALUES (?, '[\"alergia\"]', 'pendente', '2026-10-26T12:00:00+00:00')",
            (r.avaliacao_id,),
        )
    assert _conta(conn, "alertas") == 1


def test_criticidade_nao_vazia_cria_um_unico_alerta_com_todos_os_tipos(conn):
    r = _grava(conn, criticidade=["saude", "alergia"])
    assert r.alerta_id is not None
    assert _conta(conn, "alertas") == 1
    alerta = banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)
    assert json.loads(alerta["tipos"]) == ["saude", "alergia"]


def test_criticidade_vazia_nao_cria_alerta(conn):
    r = _grava(conn, criticidade=[])
    assert r.alerta_id is None
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id) is None
    assert _conta(conn, "alertas") == 0


def test_gravar_avaliacao_nao_aceita_mais_alerta_tipos(conn):
    with pytest.raises(TypeError):
        _grava(conn, criticidade=["saude"], alerta_tipos=["alergia"])


def test_alerta_nasce_pendente_com_zero_tentativas(conn):
    r = _grava(conn, criticidade=["cobranca_indevida"])
    alerta = banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)
    assert alerta["estado"] == "pendente"
    assert alerta["tentativas"] == 0
    assert alerta["enviado_em"] is None
    assert alerta["criado_em"]


def test_criado_em_do_alerta_e_utc(conn):
    r = _grava(conn, criticidade=["saude"])
    criado = datetime.fromisoformat(banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["criado_em"])
    assert criado.utcoffset() == timedelta(0)
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["criado_em"].endswith("+00:00")


def test_criado_em_informado_do_alerta_e_normalizado_para_utc(conn):
    r = _grava(conn, criticidade=["saude"], alerta_criado_em="2026-10-26T12:00:05-03:00")
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["criado_em"] == "2026-10-26T15:00:05+00:00"


def test_criado_em_do_alerta_sem_fuso_e_recusado(conn):
    with pytest.raises(ValueError):
        _grava(conn, criticidade=["saude"], alerta_criado_em="2026-10-26T12:00:05")
    assert _conta(conn, "avaliacoes") == 0


@pytest.mark.parametrize("anterior, estado", [
    ("enviado", "pendente"), ("pendente", "enviado"), ("pendente", "falhou"), ("enviado", "falhou"),
])
def test_estados_validos_do_alerta_provam_a_mudanca(conn, anterior, estado):
    r = _grava(conn, criticidade=["saude"])
    banco.mudar_estado_alerta(conn, r.alerta_id, anterior)
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["estado"] == anterior
    banco.mudar_estado_alerta(conn, r.alerta_id, estado)
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["estado"] == estado


def test_estado_invalido_do_alerta_e_recusado_pela_funcao(conn):
    r = _grava(conn, criticidade=["saude"])
    with pytest.raises(ValueError):
        banco.mudar_estado_alerta(conn, r.alerta_id, "entregue")
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["estado"] == "pendente"


def test_estado_invalido_do_alerta_e_recusado_pelo_banco(conn):
    r = _grava(conn, criticidade=["saude"])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE alertas SET estado = 'entregue' WHERE id = ?", (r.alerta_id,))


def test_nao_ha_regra_de_transicao_de_estado(conn):
    # A especificação não define transições; qualquer estado válido pode vir de qualquer outro.
    r = _grava(conn, criticidade=["saude"])
    for estado in ["enviado", "pendente", "falhou", "pendente", "enviado"]:
        banco.mudar_estado_alerta(conn, r.alerta_id, estado)
        assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["estado"] == estado


def test_tentativas_soma_um_a_cada_chamada(conn):
    r = _grava(conn, criticidade=["saude"])
    for esperado in (1, 2, 3):
        banco.mudar_estado_alerta(conn, r.alerta_id, "pendente", incrementar_tentativas=True)
        assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["tentativas"] == esperado


def test_tentativas_nao_muda_sem_pedir_incremento(conn):
    r = _grava(conn, criticidade=["saude"])
    banco.mudar_estado_alerta(conn, r.alerta_id, "enviado")
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["tentativas"] == 0


def test_mudar_estado_nao_aceita_valor_absoluto_de_tentativas(conn):
    r = _grava(conn, criticidade=["saude"])
    with pytest.raises(TypeError):
        banco.mudar_estado_alerta(conn, r.alerta_id, "enviado", tentativas=5)


def test_incrementos_sequenciais_de_duas_conexoes_somam(caminho):
    c1 = banco.abrir(caminho)
    c2 = banco.abrir(caminho)
    r = _grava(c1, criticidade=["saude"])
    banco.mudar_estado_alerta(c1, r.alerta_id, "pendente", incrementar_tentativas=True)
    banco.mudar_estado_alerta(c2, r.alerta_id, "pendente", incrementar_tentativas=True)
    assert banco.buscar_alerta_por_avaliacao(c1, r.avaliacao_id)["tentativas"] == 2
    c1.close()
    c2.close()


def test_incrementos_concorrentes_nao_perdem_contagem(caminho):
    por_thread = 25
    c0 = banco.abrir(caminho)
    r = _grava(c0, criticidade=["saude"])

    def incrementar(barreira):
        c = banco.abrir(caminho)
        try:
            barreira.wait()
            for _ in range(por_thread):
                banco.mudar_estado_alerta(c, r.alerta_id, "pendente", incrementar_tentativas=True)
        finally:
            c.close()

    _em_threads([incrementar, incrementar])

    assert banco.buscar_alerta_por_avaliacao(c0, r.avaliacao_id)["tentativas"] == 2 * por_thread
    c0.close()


def test_mudar_estado_registra_envio_em_utc(conn):
    r = _grava(conn, criticidade=["saude"])
    banco.mudar_estado_alerta(conn, r.alerta_id, "enviado", enviado_em="2026-10-26T12:01:00-03:00")
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["enviado_em"] == "2026-10-26T15:01:00+00:00"


def test_enviado_em_sem_fuso_e_recusado(conn):
    r = _grava(conn, criticidade=["saude"])
    with pytest.raises(ValueError):
        banco.mudar_estado_alerta(conn, r.alerta_id, "enviado", enviado_em="2026-10-26T12:01:00")
    assert banco.buscar_alerta_por_avaliacao(conn, r.avaliacao_id)["estado"] == "pendente"


def test_mudar_estado_de_alerta_inexistente_levanta_erro(conn):
    with pytest.raises(LookupError):
        banco.mudar_estado_alerta(conn, 9999, "enviado")


# f) recebida_em, UTC e prazo_resposta -----------------------------------------


@pytest.mark.parametrize(
    "entrada, recebida_utc, prazo_utc",
    [
        ("2026-10-26T12:00:00-03:00", "2026-10-26T15:00:00+00:00", "2026-10-31T15:00:00+00:00"),
        ("2026-10-26T15:00:00Z", "2026-10-26T15:00:00+00:00", "2026-10-31T15:00:00+00:00"),
        ("2026-10-26T15:00:00+00:00", "2026-10-26T15:00:00+00:00", "2026-10-31T15:00:00+00:00"),
        ("2026-10-26T22:00:00-03:00", "2026-10-27T01:00:00+00:00", "2026-11-01T01:00:00+00:00"),
        ("2026-10-28T09:30:00-03:00", "2026-10-28T12:30:00+00:00", "2026-11-02T12:30:00+00:00"),
        ("2026-10-26T18:30:00+05:30", "2026-10-26T13:00:00+00:00", "2026-10-31T13:00:00+00:00"),
    ],
)
def test_recebida_em_e_normalizada_para_utc_e_prazo_soma_5_dias(conn, entrada, recebida_utc, prazo_utc):
    _grava(conn, recebida_em=entrada)
    linha = banco.buscar_avaliacao_por_id_externo(conn, "ext-1")
    assert linha["recebida_em"] == recebida_utc
    assert linha["prazo_resposta"] == prazo_utc


def test_menos_tres_horas_vira_utc(conn):
    _grava(conn, recebida_em="2026-10-26T09:00:00-03:00")
    assert banco.buscar_avaliacao_por_id_externo(conn, "ext-1")["recebida_em"] == "2026-10-26T12:00:00+00:00"


def test_z_e_aceito(conn):
    _grava(conn, recebida_em="2026-10-26T12:00:00Z")
    assert _conta(conn, "avaliacoes") == 1


@pytest.mark.parametrize("entrada", ["2026-10-26T12:00:00", "2026-10-26", "2026-10-26 12:00:00"])
def test_recebida_em_sem_fuso_e_recusada(conn, entrada):
    with pytest.raises(ValueError):
        _grava(conn, recebida_em=entrada)
    assert _conta(conn, "avaliacoes") == 0


def test_recebida_em_ilegivel_e_recusada_sem_gravar(conn):
    with pytest.raises(ValueError):
        _grava(conn, recebida_em="ontem de manhã")
    assert _conta(conn, "avaliacoes") == 0


# g) transação ----------------------------------------------------------------


def test_falha_ao_gravar_o_alerta_desfaz_a_avaliacao(conn, monkeypatch):
    def quebra(*args, **kwargs):
        raise RuntimeError("falha forçada entre avaliação e alerta")

    monkeypatch.setattr(banco, "_inserir_alerta", quebra)
    with pytest.raises(RuntimeError):
        _grava(conn, criticidade=["saude"])
    assert _conta(conn, "avaliacoes") == 0
    assert _conta(conn, "alertas") == 0


def test_reenvio_depois_da_falha_volta_a_ser_processado(conn, monkeypatch):
    original = banco._inserir_alerta

    def quebra(*args, **kwargs):
        raise RuntimeError("falha forçada")

    monkeypatch.setattr(banco, "_inserir_alerta", quebra)
    with pytest.raises(RuntimeError):
        _grava(conn, criticidade=["saude"])

    monkeypatch.setattr(banco, "_inserir_alerta", original)
    r = _grava(conn, criticidade=["saude"])
    assert r.criada is True
    assert _conta(conn, "avaliacoes") == 1
    assert _conta(conn, "alertas") == 1


def test_conexao_continua_usavel_depois_da_falha(conn, monkeypatch):
    monkeypatch.setattr(banco, "_inserir_alerta", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        _grava(conn, criticidade=["saude"])
    monkeypatch.undo()
    assert _grava(conn, "ext-2").criada is True


def test_falha_real_do_banco_no_alerta_desfaz_a_avaliacao(conn):
    # Sem mock e com o esquema de produção: um trigger aborta o INSERT em alertas depois de a
    # avaliação já ter sido inserida na mesma transação.
    conn.execute(
        "CREATE TRIGGER falha_no_alerta BEFORE INSERT ON alertas "
        "BEGIN SELECT RAISE(ABORT, 'falha simulada no alerta'); END"
    )
    with pytest.raises(sqlite3.DatabaseError, match="falha simulada no alerta"):
        _grava(conn, criticidade=["saude"])
    assert _conta(conn, "avaliacoes") == 0
    assert _conta(conn, "alertas") == 0
    assert conn.in_transaction is False


def test_erro_de_integridade_que_nao_e_duplicata_e_propagado(conn):
    conn.execute(
        "CREATE TRIGGER falha_na_avaliacao BEFORE INSERT ON avaliacoes "
        "BEGIN SELECT RAISE(ABORT, 'falha simulada na avaliacao'); END"
    )
    with pytest.raises(sqlite3.DatabaseError, match="falha simulada na avaliacao"):
        _grava(conn)
    assert _conta(conn, "avaliacoes") == 0
    assert conn.in_transaction is False


def test_erro_original_nao_e_escondido_pelo_rollback_que_falha(conn, monkeypatch):
    falho = _ConexaoQueFalha(conn, {"ROLLBACK"})
    monkeypatch.setattr(banco, "_inserir_alerta", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("erro original")))
    with pytest.raises(RuntimeError, match="erro original"):
        _grava(falho, criticidade=["saude"])
    conn.execute("ROLLBACK")  # a transação ficou aberta porque o ROLLBACK foi sabotado
    assert _conta(conn, "avaliacoes") == 0


def test_falha_no_commit_faz_rollback_e_propaga(conn):
    falho = _ConexaoQueFalha(conn, {"COMMIT"})
    with pytest.raises(sqlite3.OperationalError, match="COMMIT"):
        _grava(falho, criticidade=["saude"])
    assert conn.in_transaction is False
    assert _conta(conn, "avaliacoes") == 0
    assert _conta(conn, "alertas") == 0
    assert _grava(conn).criada is True  # a conexão não ficou presa em transação aberta


def test_falha_no_commit_com_rollback_tambem_falhando_propaga_o_erro_do_commit(conn):
    falho = _ConexaoQueFalha(conn, {"COMMIT", "ROLLBACK"})
    with pytest.raises(sqlite3.OperationalError, match="COMMIT"):
        _grava(falho)
    conn.execute("ROLLBACK")


# h) persistência -------------------------------------------------------------


def test_dados_sobrevivem_ao_fechar_e_reabrir(caminho):
    c1 = banco.abrir(caminho)
    r = _grava(c1, criticidade=["alergia"])
    c1.close()

    c2 = banco.abrir(caminho)
    av = banco.buscar_avaliacao_por_id_externo(c2, "ext-1")
    assert av["id"] == r.avaliacao_id
    assert av["texto"] == "O pedido chegou frio e faltou um item."
    assert json.loads(av["categorias"]) == ["qualidade_temperatura", "item_faltando"]
    alerta = banco.buscar_alerta_por_avaliacao(c2, r.avaliacao_id)
    assert alerta["estado"] == "pendente"
    assert json.loads(alerta["tipos"]) == ["alergia"]
    c2.close()


def test_duplicidade_vale_depois_de_reabrir(caminho):
    c1 = banco.abrir(caminho)
    _grava(c1, criticidade=["saude"])
    c1.close()

    c2 = banco.abrir(caminho)
    segunda = _grava(c2, criticidade=["saude"])
    assert segunda.criada is False
    assert _conta(c2, "avaliacoes") == 1
    assert _conta(c2, "alertas") == 1
    c2.close()


def test_abrir_duas_vezes_nao_apaga_nem_recria_dados(caminho):
    c1 = banco.abrir(caminho)
    _grava(c1)
    c1.close()
    banco.abrir(caminho).close()
    c3 = banco.abrir(caminho)
    assert _conta(c3, "avaliacoes") == 1
    c3.close()


def test_estado_do_alerta_sobrevive_ao_reinicio(caminho):
    c1 = banco.abrir(caminho)
    r = _grava(c1, criticidade=["saude"])
    banco.mudar_estado_alerta(c1, r.alerta_id, "pendente", incrementar_tentativas=True)
    banco.mudar_estado_alerta(c1, r.alerta_id, "falhou", incrementar_tentativas=True)
    c1.close()

    c2 = banco.abrir(caminho)
    alerta = banco.buscar_alerta_por_avaliacao(c2, r.avaliacao_id)
    assert alerta["estado"] == "falhou"
    assert alerta["tentativas"] == 2
    c2.close()


# i) tokens_painel ------------------------------------------------------------


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def test_token_hash_e_unico(conn):
    h = _hash("token-a")
    banco.registrar_token_painel(conn, h, "painel", "2026-10-26T08:00:00-03:00")
    with pytest.raises(sqlite3.IntegrityError):
        banco.registrar_token_painel(conn, h, "painel", "2026-10-27T08:00:00-03:00")
    assert _conta(conn, "tokens_painel") == 1


def test_so_o_hash_e_guardado(caminho, conn):
    token = "token-secreto-de-teste-4f9a"
    banco.registrar_token_painel(conn, _hash(token), "painel", "2026-10-26T08:00:00-03:00")
    despejo = "\n".join(conn.iterdump())
    assert _hash(token) in despejo
    assert token not in despejo
    conn.close()
    assert token.encode() not in caminho.read_bytes()
    reaberta = banco.abrir(caminho)
    assert "token" not in _colunas(reaberta, "tokens_painel")
    reaberta.close()


def test_criado_em_e_expira_em_ficam_em_utc_com_7_dias(conn):
    banco.registrar_token_painel(conn, _hash("t"), "painel", "2026-10-26T08:00:00-03:00")
    linha = conn.execute("SELECT * FROM tokens_painel").fetchone()
    assert linha["criado_em"] == "2026-10-26T11:00:00+00:00"
    assert linha["expira_em"] == "2026-11-02T11:00:00+00:00"
    assert linha["revogado_em"] is None
    assert linha["escopo"] == "painel"


@pytest.mark.parametrize("criado_em", ["2026-10-26T08:00:00", None, 123, "ontem"])
def test_token_com_criado_em_sem_fuso_ou_invalido_e_recusado(conn, criado_em):
    with pytest.raises(ValueError):
        banco.registrar_token_painel(conn, _hash("t"), "painel", criado_em)
    assert _conta(conn, "tokens_painel") == 0


def test_revogado_em_nasce_nulo_e_aceita_data_de_revogacao(conn):
    banco.registrar_token_painel(conn, _hash("t"), "painel", "2026-10-26T08:00:00-03:00")
    assert conn.execute("SELECT revogado_em FROM tokens_painel").fetchone()[0] is None
    conn.execute("UPDATE tokens_painel SET revogado_em = '2026-10-27T11:00:00+00:00'")
    assert conn.execute("SELECT revogado_em FROM tokens_painel").fetchone()[0] == "2026-10-27T11:00:00+00:00"


def test_revogado_em_sobrevive_ao_reinicio(caminho):
    c1 = banco.abrir(caminho)
    banco.registrar_token_painel(c1, _hash("t"), "painel", "2026-10-26T08:00:00-03:00")
    c1.execute("UPDATE tokens_painel SET revogado_em = '2026-10-27T11:00:00+00:00'")
    c1.close()
    c2 = banco.abrir(caminho)
    assert c2.execute("SELECT revogado_em FROM tokens_painel").fetchone()[0] == "2026-10-27T11:00:00+00:00"
    c2.close()


@pytest.mark.parametrize("valor", ["abc", "A" * 64, "a" * 63, "", None, 123, b"a" * 64])
def test_hash_que_nao_e_sha256_hex_e_recusado(conn, valor):
    with pytest.raises(ValueError):
        banco.registrar_token_painel(conn, valor, "painel", "2026-10-26T08:00:00-03:00")
    assert _conta(conn, "tokens_painel") == 0
