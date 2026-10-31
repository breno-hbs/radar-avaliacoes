"""Testes das rotas de leitura do painel (bloco 4.1). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (contratos, token do painel, ranking, resumo semanal e a decisão de
31/10/2026 sobre as rotas de leitura do painel). Os tokens são sempre criados por tokens.criar_token_painel,
nunca fixos. Nenhum teste usa rede externa.
"""

import json
import logging
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from app import banco, tokens
from test_ranking_ponta_a_ponta import montar_banco_85
from test_resumo_semanal import (  # noqa: F401 - fixtures usadas pelos testes
    CHAVE, RESUMO_DA_SEGUNDA, SEGUNDA, SP, _acao, _get, _grava, _linhas, _varias, ambiente, app, caminho,
    cliente, relogio,
)

TEXTO = "texto que nunca vai no resumo"  # o texto que _grava usa
INICIO_UTC = "2026-10-26T03:00:00+00:00"  # início da semana de 26/10 em UTC (como acoes.semana_inicio)
ROTAS = ("cards", "historico")
LINK_INVALIDO = {"detail": "link inválido ou expirado"}


def _token(ambiente, *, agora=RESUMO_DA_SEGUNDA, escopo="painel"):
    with closing(banco.abrir(ambiente)) as conn:
        return tokens.criar_token_painel(conn, escopo, agora=agora)


@pytest.fixture
def token(ambiente):
    return _token(ambiente)


def _painel(cliente, token, rota="cards", semana=None):
    params = {} if semana is None else {"semana": semana}
    return cliente.get(f"/api/painel/{token}/{rota}", params=params)


def _avaliacao(ambiente, quando, *, nota=1, categorias=("atraso",), criticidade=(), texto=None, id_externo=None):
    """Grava direto com app.banco e devolve o avaliacao_id."""
    _grava.__globals__["_n"][0] += 1
    externo = id_externo or f"p-{_grava.__globals__['_n'][0]}"
    with closing(banco.abrir(ambiente)) as conn:
        r = banco.gravar_avaliacao(
            conn, id_externo=externo, texto=texto if texto is not None else f"relato {externo}", nota=nota,
            recebida_em=quando.isoformat(), categorias=list(categorias), criticidade=list(criticidade),
            status_classificacao="ok", alerta_criado_em=quando.isoformat(),
        )
    return r.avaliacao_id


def _acao_com_evidencias(ambiente, categoria, semana_inicio, estado="pendente", evidencias=(), criada_em=None,
                         nota=None, concluida_em=None):
    with closing(banco.abrir(ambiente)) as conn:
        cur = conn.execute(
            "INSERT INTO acoes (categoria, semana_inicio, estado, criada_em, concluida_em, nota) VALUES (?, ?, ?, ?, ?, ?)",
            (categoria, semana_inicio, estado, criada_em or "2026-10-27T12:00:00+00:00", concluida_em, nota),
        )
        acao_id = cur.lastrowid
        for posicao, (avaliacao_id, trecho) in enumerate(evidencias, start=1):
            conn.execute(
                "INSERT INTO acao_avaliacoes (acao_id, avaliacao_id, trecho_exibido, posicao) VALUES (?, ?, ?, ?)",
                (acao_id, avaliacao_id, trecho, posicao),
            )
    return acao_id


def _semana_utc(segunda_local):
    return segunda_local.astimezone(timezone.utc).isoformat()


# ---------- token: 404 uniforme ----------

@pytest.mark.parametrize("rota", ROTAS)
def test_404_uniforme_para_todo_link_invalido(cliente, ambiente, rota):
    inexistente = secrets.token_urlsafe(32)
    expirado = _token(ambiente, agora=RESUMO_DA_SEGUNDA - timedelta(days=8))
    revogado = _token(ambiente)
    with closing(banco.abrir(ambiente)) as conn:
        tokens.revogar_token(conn, revogado, RESUMO_DA_SEGUNDA)
    outro_escopo = _token(ambiente, escopo="outro")
    respostas = [_painel(cliente, t, rota) for t in (inexistente, expirado, revogado, "malformado", outro_escopo)]
    for r in respostas:
        assert r.status_code == 404
        assert r.json() == LINK_INVALIDO
        assert r.content == respostas[0].content
        assert r.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("rota", ROTAS)
def test_token_valido_responde_200(cliente, token, rota):
    r = _painel(cliente, token, rota)
    assert r.status_code == 200


def test_token_invalido_com_semana_invalida_da_404_e_nao_422(cliente, ambiente):
    for semana in ("2026-10-27", "lixo"):
        r = _painel(cliente, secrets.token_urlsafe(32), "cards", semana=semana)
        assert r.status_code == 404 and r.json() == LINK_INVALIDO


def test_token_no_ultimo_instante_e_no_vencimento(cliente, ambiente, relogio):
    t = _token(ambiente, agora=RESUMO_DA_SEGUNDA - timedelta(days=7))
    assert _painel(cliente, t).status_code == 404  # no instante exato de expira_em já é inválido
    relogio.instante = RESUMO_DA_SEGUNDA - timedelta(seconds=1)
    assert _painel(cliente, t).status_code == 200


# ---------- cards ----------

def test_evidencias_por_nota_depois_recencia_depois_id_e_no_maximo_3(cliente, ambiente, token):
    _varias(ambiente, 20)
    d = SEGUNDA + timedelta(days=1)
    nota3 = _avaliacao(ambiente, d + timedelta(hours=5), nota=3)
    antiga_n1 = _avaliacao(ambiente, d, nota=1)
    recente_n1 = _avaliacao(ambiente, d + timedelta(hours=9), nota=1)
    empate_a = _avaliacao(ambiente, d + timedelta(hours=2), nota=2)
    empate_b = _avaliacao(ambiente, d + timedelta(hours=2), nota=2)  # mesmo instante: desempata pelo id
    card = _painel(cliente, token).json()["cards"][0]
    assert card["categoria"] == "atraso" and card["mencoes"] == 5
    ids = [e["avaliacao_id"] for e in card["evidencias"]]
    assert ids == [recente_n1, antiga_n1, empate_a]
    assert [e["posicao"] for e in card["evidencias"]] == [1, 2, 3]
    assert empate_b not in ids and nota3 not in ids
    assert [e["nota"] for e in card["evidencias"]] == [1, 1, 2]


def test_evidencias_so_da_semana_e_da_categoria(cliente, ambiente, token):
    _varias(ambiente, 20)
    _avaliacao(ambiente, SEGUNDA - timedelta(days=1), nota=1)  # semana anterior
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1), nota=1, categorias=["embalagem"])
    da_semana = _avaliacao(ambiente, SEGUNDA + timedelta(days=2), nota=4)
    cards = {c["categoria"]: c for c in _painel(cliente, token).json()["cards"]}
    assert [e["avaliacao_id"] for e in cards["atraso"]["evidencias"]] == [da_semana]


def test_trecho_da_evidencia_usa_a_funcao_unica(cliente, ambiente, token):
    from app import trechos

    _varias(ambiente, 20)
    texto = "a comida   chegou\nfria " + "muito " * 40
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1), texto=texto)
    e = _painel(cliente, token).json()["cards"][0]["evidencias"][0]
    assert e["trecho"] == trechos.trecho(texto) and e["trecho"].endswith("…")


def test_critica_marcada_como_relato_a_verificar(cliente, ambiente, token):
    _varias(ambiente, 20)
    critica = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), nota=1, criticidade=["saude"])
    comum = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), nota=2)
    evid = {e["avaliacao_id"]: e for e in _painel(cliente, token).json()["cards"][0]["evidencias"]}
    assert evid[critica]["critica"] is True and evid[comum]["critica"] is False
    assert set(evid[critica]) == {"avaliacao_id", "posicao", "trecho", "nota", "critica"}


def test_card_com_acao_traz_id_e_estado_da_mais_recente(cliente, ambiente, token):
    _varias(ambiente, 20)
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1))
    _acao(ambiente, "atraso", INICIO_UTC, "pendente")
    _acao(ambiente, "atraso", INICIO_UTC, "em_andamento")
    mais_recente = _linhas(ambiente, "acoes")[-1]["id"]
    _acao(ambiente, "atraso", "2026-10-19T03:00:00+00:00", "concluida")  # outra semana: não conta
    card = _painel(cliente, token).json()["cards"][0]
    assert card["acao"] == {"id": mais_recente, "estado": "em_andamento"}


def test_card_sem_acao_traz_null(cliente, ambiente, token):
    _varias(ambiente, 20)
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1))
    _acao(ambiente, "embalagem", INICIO_UTC, "pendente")  # outra categoria
    _acao(ambiente, "atraso", "2026-10-19T03:00:00+00:00", "pendente")  # outra semana
    assert _painel(cliente, token).json()["cards"][0]["acao"] is None


def test_campos_do_card(cliente, ambiente, token):
    _varias(ambiente, 20)
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1), categorias=["item_faltando"])
    corpo = _painel(cliente, token).json()
    assert set(corpo) == {"periodo", "cards", "outros_temas", "temas_sem_acao", "cobertura", "ultima_leitura"}
    card = corpo["cards"][0]
    assert set(card) == {"categoria", "problema", "mencoes", "taxa_por_100", "tendencia_em_alta", "recomendacao",
                         "evidencias", "acao"}
    assert card["problema"] == "Item ausente ou errado"
    assert card["recomendacao"] == "Criar checklist de conferência antes de fechar a sacola"
    assert card["taxa_por_100"] == 4.8  # 1 de 21
    assert card["tendencia_em_alta"] is False
    assert "pontuacao" not in json.dumps(corpo)


def test_entregador_fora_dos_cards_e_em_temas_sem_acao(cliente, ambiente, token):
    _varias(ambiente, 20)
    for _ in range(7):
        _avaliacao(ambiente, SEGUNDA + timedelta(days=1), categorias=["entregador"])
    corpo = _painel(cliente, token).json()
    assert corpo["cards"] == [] and corpo["outros_temas"] == []
    assert corpo["temas_sem_acao"] == [{"categoria": "entregador", "mencoes": 7}]


def test_no_maximo_3_cards_e_o_resto_em_outros_temas(cliente, ambiente, token):
    _varias(ambiente, 20)
    for categoria, n in (("qualidade_temperatura", 5), ("item_faltando", 4), ("atraso", 6), ("embalagem", 1),
                         ("atendimento", 2)):
        for _ in range(n):
            _avaliacao(ambiente, SEGUNDA + timedelta(days=1), categorias=[categoria])
    corpo = _painel(cliente, token).json()
    assert [c["categoria"] for c in corpo["cards"]] == ["qualidade_temperatura", "item_faltando", "atraso"]
    assert corpo["outros_temas"] == [{"categoria": "atendimento", "mencoes": 2}, {"categoria": "embalagem", "mencoes": 1}]


def test_semana_vazia_responde_200_com_cards_vazio(cliente, ambiente, token):
    r = _painel(cliente, token)
    assert r.status_code == 200 and r.json()["cards"] == []
    assert r.json()["periodo"]["inicio"] == "2026-10-26T00:00:00-03:00"


def test_semana_valida_nao_segunda_e_formato(cliente, ambiente, token):
    r = _painel(cliente, token, semana="2026-10-19")
    assert r.status_code == 200 and r.json()["periodo"]["rotulo"] == "19/10 a 25/10"
    r = _painel(cliente, token, semana="2026-10-20")
    assert r.status_code == 422 and "segunda" in r.json()["detail"]
    for ruim in ("19/10/2026", "2026-1-5", "abc", ""):
        r = _painel(cliente, token, semana=ruim)
        assert r.status_code == 422 and "AAAA-MM-DD" in r.json()["detail"], ruim


def test_ultima_leitura_com_fuso_e_nula_sem_avaliacoes(cliente, ambiente, token):
    assert _painel(cliente, token).json()["ultima_leitura"] is None
    _avaliacao(ambiente, datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc))
    _avaliacao(ambiente, datetime(2026, 11, 3, 2, 30, tzinfo=timezone.utc))  # depois da semana pedida
    assert _painel(cliente, token).json()["ultima_leitura"] == "2026-11-02T23:30:00-03:00"


def test_cobertura_igual_a_do_resumo(cliente, ambiente, token):
    _varias(ambiente, 5, quando=SEGUNDA - timedelta(days=3))
    _varias(ambiente, 20)
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso", "entregador"])
    _grava(ambiente, SEGUNDA + timedelta(days=1), status="invalida")
    painel = _painel(cliente, token, semana="2026-10-26").json()["cobertura"]
    resumo = _get(cliente, semana="2026-10-26").json()["cobertura"]
    assert painel == resumo
    assert "tendência não calculada: amostra pequena" in painel["texto"]


# ---------- histórico ----------

def test_historico_vazio(cliente, token):
    assert _painel(cliente, token, "historico").json() == {"semanas": []}


def test_historico_ordem_semanas_sem_acao_ausentes_e_texto_original(cliente, ambiente, token):
    s1, s2, s3 = (SEGUNDA - timedelta(weeks=k) for k in (3, 2, 0))  # 05/10, 12/10 e 26/10; 19/10 sem ação
    av = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), texto="texto original da avaliação, inteiro")
    a1 = _acao_com_evidencias(ambiente, "atraso", _semana_utc(s1), "concluida", concluida_em="2026-10-12T12:00:00+00:00")
    a2 = _acao_com_evidencias(ambiente, "embalagem", _semana_utc(s2))
    a3 = _acao_com_evidencias(ambiente, "atraso", _semana_utc(s3), "em_andamento", nota="ligar para o motoboy",
                              evidencias=[(av, "trecho gravado na época")])
    a4 = _acao_com_evidencias(ambiente, "item_faltando", _semana_utc(s3))
    semanas = _painel(cliente, token, "historico").json()["semanas"]
    assert [s["semana_inicio"] for s in semanas] == [
        "2026-10-26T00:00:00-03:00", "2026-10-12T00:00:00-03:00", "2026-10-05T00:00:00-03:00"]
    assert [s["rotulo"] for s in semanas] == ["26/10 a 01/11", "12/10 a 18/10", "05/10 a 11/10"]
    assert [a["id"] for a in semanas[0]["acoes"]] == [a3, a4]
    acao = semanas[0]["acoes"][0]
    assert set(acao) == {"id", "categoria", "estado", "criada_em", "concluida_em", "nota", "evidencias"}
    assert (acao["categoria"], acao["estado"], acao["nota"]) == ("atraso", "em_andamento", "ligar para o motoboy")
    assert acao["evidencias"] == [{"posicao": 1, "trecho_exibido": "trecho gravado na época",
                                   "texto_original": "texto original da avaliação, inteiro"}]
    assert semanas[0]["acoes"][1]["evidencias"] == []  # ação sem evidência
    assert semanas[2]["acoes"][0]["concluida_em"] == "2026-10-12T09:00:00-03:00"
    assert semanas[1]["acoes"][0]["id"] == a2 and semanas[2]["acoes"][0]["id"] == a1


def test_historico_trecho_exibido_e_o_gravado_e_nao_o_recalculado(cliente, ambiente, token):
    from app import trechos

    texto = "um texto bem diferente do trecho que o dono viu na época " * 5
    av = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), texto=texto)
    _acao_com_evidencias(ambiente, "atraso", INICIO_UTC, evidencias=[(av, "o que o dono viu")])
    e = _painel(cliente, token, "historico").json()["semanas"][0]["acoes"][0]["evidencias"][0]
    assert e["trecho_exibido"] == "o que o dono viu" != trechos.trecho(texto)
    assert e["texto_original"] == texto


def test_historico_evidencias_em_ordem_de_posicao(cliente, ambiente, token):
    avs = [_avaliacao(ambiente, SEGUNDA + timedelta(days=1)) for _ in range(3)]
    acao = _acao_com_evidencias(ambiente, "atraso", INICIO_UTC)
    with closing(banco.abrir(ambiente)) as conn:
        for av, posicao in zip(avs, (3, 1, 2)):  # gravadas fora de ordem
            conn.execute("INSERT INTO acao_avaliacoes (acao_id, avaliacao_id, trecho_exibido, posicao) "
                         "VALUES (?, ?, ?, ?)", (acao, av, f"t{posicao}", posicao))
    e = _painel(cliente, token, "historico").json()["semanas"][0]["acoes"][0]["evidencias"]
    assert [x["posicao"] for x in e] == [1, 2, 3] and [x["trecho_exibido"] for x in e] == ["t1", "t2", "t3"]


def test_historico_no_maximo_12_semanas_as_mais_recentes(cliente, ambiente, token):
    for k in range(14):
        _acao_com_evidencias(ambiente, "atraso", _semana_utc(SEGUNDA - timedelta(weeks=k)))
    semanas = _painel(cliente, token, "historico").json()["semanas"]
    assert len(semanas) == 12
    assert semanas[0]["semana_inicio"] == "2026-10-26T00:00:00-03:00"
    assert semanas[-1]["semana_inicio"] == (SEGUNDA - timedelta(weeks=11)).astimezone(SP).replace(
        hour=0).isoformat()


# ---------- só leitura, cabeçalhos, logs ----------

def _todas_as_tabelas(caminho):
    with closing(banco.abrir(caminho)) as conn:
        return {t: [tuple(l) for l in conn.execute(f"SELECT * FROM {t}")]
                for t in ("avaliacoes", "alertas", "acoes", "acao_avaliacoes", "tokens_painel")}


def test_as_rotas_nao_escrevem_no_banco(cliente, ambiente, token):
    _varias(ambiente, 20)
    av = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), criticidade=["saude"])
    _acao_com_evidencias(ambiente, "atraso", INICIO_UTC, evidencias=[(av, "trecho")])
    antes = _todas_as_tabelas(ambiente)
    for rota in ROTAS:
        assert _painel(cliente, token, rota).status_code == 200
    assert _painel(cliente, "malformado").status_code == 404
    assert _todas_as_tabelas(ambiente) == antes


@pytest.mark.parametrize("rota", ROTAS)
def test_cache_control_e_referrer_policy(cliente, token, rota):
    r = _painel(cliente, token, rota)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_token_e_texto_de_cliente_fora_dos_logs(cliente, ambiente, token, caplog):
    caplog.set_level(logging.DEBUG)
    _varias(ambiente, 20)
    texto = "texto secreto do cliente que nao vai para log"
    av = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), texto=texto)
    _acao_com_evidencias(ambiente, "atraso", INICIO_UTC, evidencias=[(av, "trecho secreto")])
    for rota in ROTAS:
        _painel(cliente, token, rota)
    _painel(cliente, "malformado")
    # O TestClient (httpx2) registra a URL que ELE pediu: é o lado do cliente, fora do app.
    do_servidor = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert token not in do_servidor and texto not in do_servidor and "trecho secreto" not in do_servidor
    from app import tokens as t

    registro = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                 ("127.0.0.1:1", "GET", f"/api/painel/{token}/cards", "1.1", 200), None)
    t.FiltroDeToken().filter(registro)
    assert token not in registro.getMessage()


# ---------- ponta a ponta com as 85 ----------

def test_ponta_a_ponta_semana_3_bate_com_o_ranking_escrito_a_mao(cliente, caminho, ambiente):
    montar_banco_85(caminho)
    t = _token(ambiente)
    corpo = _painel(cliente, t, semana="2026-10-19").json()
    # escrito à mão em tests/test_ranking_ponta_a_ponta.py (semana 3 contra a 2)
    assert [(c["categoria"], c["mencoes"], c["tendencia_em_alta"]) for c in corpo["cards"]] == [
        ("item_faltando", 6, True), ("qualidade_temperatura", 6, True), ("embalagem", 5, True)]
    assert corpo["outros_temas"] == [{"categoria": "atendimento", "mencoes": 4}, {"categoria": "atraso", "mencoes": 4}]
    assert corpo["temas_sem_acao"] == [{"categoria": "entregador", "mencoes": 2}]
    for card in corpo["cards"]:
        assert 1 <= len(card["evidencias"]) <= 3
        notas = [e["nota"] for e in card["evidencias"]]
        assert notas == sorted(notas)


# ---------- achados do revisor ----------

@pytest.mark.parametrize("rota", ROTAS)
def test_radar_db_inexistente_da_500_e_nao_cria_o_arquivo(cliente, ambiente, token, monkeypatch, tmp_path, rota):
    ausente = tmp_path / "nao_existe.db"
    monkeypatch.setenv("RADAR_DB", str(ausente))
    r = _painel(cliente, token, rota)
    assert r.status_code == 500 and r.json() == {"detail": "falha interna"}
    assert not ausente.exists()


def test_cobertura_igual_a_do_resumo_tambem_na_semana_padrao(cliente, ambiente, token):
    _varias(ambiente, 20)
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso", "entregador"])
    assert _painel(cliente, token).json()["cobertura"] == _get(cliente, semana=None).json()["cobertura"]


def test_trecho_da_evidencia_tem_o_valor_esperado(cliente, ambiente, token):
    _varias(ambiente, 20)
    texto = "a comida   chegou\nfria e " + "muito " * 40
    _avaliacao(ambiente, SEGUNDA + timedelta(days=1), texto=texto)
    esperado = ("a comida chegou fria e " + "muito " * 19).rstrip() + "…"  # 137 caracteres + reticências
    assert _painel(cliente, token).json()["cards"][0]["evidencias"][0]["trecho"] == esperado


# ---------- terceiro commit: fuso do histórico e Cache-Control em todos os códigos ----------

def test_historico_criada_em_e_concluida_em_em_sao_paulo(cliente, ambiente, token):
    # 01:30 UTC de 27/10 é 22:30 de 26/10 em São Paulo (dia local anterior)
    _acao_com_evidencias(ambiente, "atraso", INICIO_UTC, "concluida", criada_em="2026-10-27T01:30:00+00:00",
                         concluida_em="2026-10-28T14:00:00+00:00")
    _acao_com_evidencias(ambiente, "embalagem", INICIO_UTC)  # concluida_em nulo
    acoes = _painel(cliente, token, "historico").json()["semanas"][0]["acoes"]
    assert acoes[0]["criada_em"] == "2026-10-26T22:30:00-03:00"
    assert acoes[0]["concluida_em"] == "2026-10-28T11:00:00-03:00"
    assert acoes[1]["concluida_em"] is None
    assert [a["criada_em"] for a in acoes][1] == "2026-10-27T09:00:00-03:00"  # o padrão de _acao_com_evidencias
    with closing(banco.abrir(ambiente)) as conn:  # o banco continua em UTC
        assert conn.execute("SELECT criada_em FROM acoes ORDER BY id").fetchone()[0] == "2026-10-27T01:30:00+00:00"


def _provoca_500_generico(monkeypatch, rota):
    from app import consultas

    def explode(*a, **k):
        raise RuntimeError("detalhe interno")

    monkeypatch.setattr(consultas, "listar_historico" if rota == "historico" else "ultima_leitura", explode)


@pytest.mark.parametrize("rota", ROTAS)
def test_cache_control_em_200_404_e_500(cliente, ambiente, token, monkeypatch, tmp_path, rota):
    ok = _painel(cliente, token, rota)
    invalido = _painel(cliente, "malformado", rota)
    assert (ok.status_code, invalido.status_code) == (200, 404)

    monkeypatch.setenv("RADAR_DB", str(tmp_path / "nao_existe.db"))
    configuracao = _painel(cliente, token, rota)  # 500 de configuração (ErroDeConfiguracao)
    assert configuracao.status_code == 500
    monkeypatch.setenv("RADAR_DB", str(ambiente))

    _provoca_500_generico(monkeypatch, rota)
    inesperado = _painel(cliente, token, rota)  # 500 genérico (ServerErrorMiddleware)
    assert inesperado.status_code == 500 and inesperado.json() == {"detail": "falha interna"}

    for r in (ok, invalido, configuracao, inesperado):
        assert r.headers["cache-control"] == "no-store", r.status_code
        assert r.headers["referrer-policy"] == "no-referrer"


def test_cache_control_no_422_dos_cards(cliente, token):
    r = _painel(cliente, token, "cards", semana="2026-10-27")
    assert r.status_code == 422 and r.headers["cache-control"] == "no-store"


def test_cache_control_vale_para_o_caminho_do_painel_e_nao_para_as_outras_rotas(cliente):
    assert cliente.post("/avaliacoes", json={}).headers.get("cache-control") is None  # 401, rota de outro assunto
    assert cliente.get("/rota-que-nao-existe").headers.get("cache-control") is None
    assert cliente.get("/api/painel/malformado/outra-coisa").headers["cache-control"] == "no-store"  # 404 do caminho
