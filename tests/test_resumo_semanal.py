"""Testes do GET /resumo-semanal (bloco 2.7). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (contrato de GET /resumo-semanal, "Regras de negócio", ranking,
token do painel e a decisão de 31/10/2026 sobre o resumo). Nenhum teste chama a API real. A chave e a
URL abaixo são fictícias e existem só nestes testes.
"""

import json
import logging
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import banco, config_ranking, main, resumo, tokens
from test_ranking_ponta_a_ponta import montar_banco_85

SP = ZoneInfo("America/Sao_Paulo")
CHAVE = "chave-ficticia-do-resumo"
URL_BASE = "https://radar.teste"
SEGUNDA = datetime(2026, 10, 26, tzinfo=SP)  # semana em teste: 26/10 a 01/11
RESUMO_DA_SEGUNDA = datetime(2026, 11, 2, 8, 0, tzinfo=SP)  # segunda seguinte, 8h


class Relogio:
    def __init__(self, instante):
        self.instante = instante

    def __call__(self):
        return self.instante


@pytest.fixture
def caminho(tmp_path):
    return tmp_path / "radar.db"


@pytest.fixture
def ambiente(caminho, monkeypatch):
    monkeypatch.setenv("RADAR_DB", str(caminho))
    monkeypatch.setenv("RADAR_API_KEY", CHAVE)
    monkeypatch.setenv("RADAR_URL_BASE", URL_BASE)
    with closing(banco.abrir(caminho)):
        pass
    return caminho


@pytest.fixture
def relogio():
    return Relogio(RESUMO_DA_SEGUNDA)


@pytest.fixture
def app(ambiente, relogio):
    aplicacao = main.criar_app()
    aplicacao.dependency_overrides[main.obter_relogio] = lambda: relogio
    return aplicacao


@pytest.fixture
def cliente(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _get(cliente, semana="2026-10-26", chave=CHAVE):
    params = {} if semana is None else {"semana": semana}
    headers = {} if chave is None else {"X-API-Key": chave}
    return cliente.get("/resumo-semanal", params=params, headers=headers)


_n = [0]


def _grava(caminho, quando, nota=1, categorias=(), criticidade=(), status="ok", id_externo=None):
    _n[0] += 1
    with closing(banco.abrir(caminho)) as conn:
        r = banco.gravar_avaliacao(
            conn, id_externo=id_externo or f"ext-{_n[0]}", texto="texto que nunca vai no resumo", nota=nota,
            recebida_em=quando.isoformat(), categorias=list(categorias), criticidade=list(criticidade),
            status_classificacao=status, alerta_criado_em=quando.isoformat(),
        )
    return r


def _varias(caminho, n, quando=None, nota=5, categorias=()):
    quando = quando or SEGUNDA + timedelta(days=2, hours=12)
    for _ in range(n):
        _grava(caminho, quando, nota=nota, categorias=categorias)


def _acao(caminho, categoria, semana_inicio, estado):
    with closing(banco.abrir(caminho)) as conn:
        conn.execute(
            "INSERT INTO acoes (categoria, semana_inicio, estado, criada_em) VALUES (?, ?, ?, ?)",
            (categoria, semana_inicio, estado, "2026-10-20T12:00:00+00:00"),
        )


def _linhas(caminho, tabela):
    with closing(banco.abrir(caminho)) as conn:
        return conn.execute(f"SELECT * FROM {tabela} ORDER BY id").fetchall()


def _token_do_link(link):
    assert link.startswith(f"{URL_BASE}/painel/")
    return link.rsplit("/", 1)[1]


# ---------- chave ----------

@pytest.mark.parametrize("chave", [None, "", "errada"])
def test_401_sem_chave_vazia_ou_errada_antes_de_validar_semana(cliente, chave):
    for semana in ("2026-10-26", "2026-10-27", "lixo", None):
        r = _get(cliente, semana=semana, chave=chave)
        assert r.status_code == 401, (chave, semana)


def test_401_nao_cria_token(cliente, ambiente):
    _get(cliente, chave="errada")
    assert _linhas(ambiente, "tokens_painel") == []


# ---------- semana ----------

@pytest.mark.parametrize("agora", [
    datetime(2026, 11, 2, 7, 59, tzinfo=SP),  # segunda às 07:59
    datetime(2026, 11, 2, 8, 0, tzinfo=SP),  # segunda às 08:00
    datetime(2026, 11, 8, 15, 0, tzinfo=SP),  # domingo
])
def test_semana_padrao_e_a_ultima_completa(cliente, relogio, agora):
    relogio.instante = agora
    r = _get(cliente, semana=None)
    assert r.status_code == 200
    assert r.json()["periodo"]["inicio"] == "2026-10-26T00:00:00-03:00"


def test_semana_valida(cliente):
    r = _get(cliente, semana="2026-10-19")
    assert r.status_code == 200
    assert r.json()["periodo"] == {
        "inicio": "2026-10-19T00:00:00-03:00",
        "fim_exclusivo": "2026-10-26T00:00:00-03:00",
        "rotulo": "19/10 a 25/10",
    }


@pytest.mark.parametrize("semana", ["2026-10-27", "2026-11-01"])
def test_semana_que_nao_e_segunda_da_422_curto(cliente, ambiente, semana):
    r = _get(cliente, semana=semana)
    assert r.status_code == 422
    assert "segunda" in r.json()["detail"] and len(r.text) < 200
    assert _linhas(ambiente, "tokens_painel") == []


@pytest.mark.parametrize("semana", ["26/10/2026", "2026-13-01", "20261026", "2026-10-26T00:00", "", "abc"])
def test_semana_com_formato_invalido_da_422_curto(cliente, semana):
    r = _get(cliente, semana=semana)
    assert r.status_code == 422
    assert "AAAA-MM-DD" in r.json()["detail"] and len(r.text) < 200


# ---------- forma da resposta ----------

def test_forma_da_resposta(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["item_faltando"])
    r = _get(cliente)
    assert r.status_code == 200
    corpo = r.json()
    assert set(corpo) == {
        "periodo", "total", "negativas", "invalidas", "cards", "outros_temas", "temas_sem_acao",
        "criticos", "acoes_pendentes", "cobertura", "enviar", "mensagem", "link_painel",
    }
    assert corpo["periodo"] == {
        "inicio": "2026-10-26T00:00:00-03:00", "fim_exclusivo": "2026-11-02T00:00:00-03:00",
        "rotulo": "26/10 a 01/11",
    }
    assert (corpo["total"], corpo["negativas"], corpo["invalidas"]) == (21, 1, 0)
    card = corpo["cards"][0]
    assert set(card) == {"categoria", "mencoes", "taxa_por_100", "tendencia_em_alta", "problema", "recomendacao"}
    assert card["categoria"] == "item_faltando" and card["mencoes"] == 1
    assert card["taxa_por_100"] == 4.8  # 100/21 = 4,7619...: 1 casa decimal
    assert card["tendencia_em_alta"] is False
    assert card["recomendacao"] == "Criar checklist de conferência antes de fechar a sacola"
    assert "pontuacao" not in json.dumps(corpo)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_recomendacoes_copiadas_da_especificacao(cliente, ambiente):
    _varias(ambiente, 20)
    for categoria in ("atraso", "item_faltando", "qualidade_temperatura", "embalagem", "atendimento"):
        _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=[categoria])
    corpo = _get(cliente).json()
    todas = {c["categoria"]: c["recomendacao"] for c in corpo["cards"]}
    assert todas == {
        "item_faltando": "Criar checklist de conferência antes de fechar a sacola",
        "qualidade_temperatura": "Revisar o preparo ou a conservação do prato citado",
        "atendimento": "Orientar a equipe responsável",
    }
    assert {t["categoria"] for t in corpo["outros_temas"]} == {"atraso", "embalagem"}
    assert resumo.RECOMENDACOES["atraso"] == ("Investigar o gargalo nos horários das reclamações, "
                                              "sem presumir a causa")
    assert resumo.RECOMENDACOES["embalagem"] == "Testar melhoria da embalagem do item citado"
    assert "entregador" not in resumo.RECOMENDACOES


def test_taxa_arredondada_so_na_borda(cliente, ambiente):
    _varias(ambiente, 19)
    for _ in range(2):
        _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso"])
    # 200/21 = 9,5238...
    assert _get(cliente).json()["cards"][0]["taxa_por_100"] == 9.5


def test_tendencia_em_alta(cliente, ambiente):
    _varias(ambiente, 20, quando=SEGUNDA - timedelta(days=5))  # semana anterior: 20, nenhuma menção
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso"])
    card = _get(cliente).json()["cards"][0]
    assert card["tendencia_em_alta"] is True


# ---------- cards ----------

def test_no_maximo_3_cards_e_o_resto_em_outros_temas(cliente, ambiente):
    _varias(ambiente, 20)
    for categoria, n in (("qualidade_temperatura", 5), ("item_faltando", 4), ("atraso", 6),
                         ("embalagem", 1), ("atendimento", 2)):
        for _ in range(n):
            _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=[categoria])
    corpo = _get(cliente).json()
    assert [c["categoria"] for c in corpo["cards"]] == ["qualidade_temperatura", "item_faltando", "atraso"]
    assert corpo["outros_temas"] == [{"categoria": "atendimento", "mencoes": 2},
                                     {"categoria": "embalagem", "mencoes": 1}]


def test_entregador_so_em_temas_sem_acao(cliente, ambiente):
    _varias(ambiente, 20)
    for _ in range(9):
        _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["entregador"])
    corpo = _get(cliente).json()
    assert corpo["cards"] == [] and corpo["outros_temas"] == []
    assert corpo["temas_sem_acao"] == [{"categoria": "entregador", "mencoes": 9}]


# ---------- enviar ----------

def test_enviar_com_card(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso"])
    corpo = _get(cliente).json()
    assert corpo["enviar"] is True and corpo["link_painel"]


def test_enviar_so_com_critico(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), criticidade=["cobranca_indevida"])
    corpo = _get(cliente).json()
    assert corpo["cards"] == [] and corpo["acoes_pendentes"] == []
    assert corpo["enviar"] is True and corpo["link_painel"]


def test_enviar_so_com_acao_pendente(cliente, ambiente):
    _varias(ambiente, 20)
    _acao(ambiente, "atraso", "2026-10-12T03:00:00+00:00", "pendente")
    corpo = _get(cliente).json()
    assert corpo["cards"] == [] and corpo["criticos"] == []
    assert corpo["enviar"] is True and corpo["link_painel"]


def test_enviar_em_volume_pequeno_sem_nada_com_aviso_curto(cliente, ambiente):
    _varias(ambiente, 19)
    corpo = _get(cliente).json()
    assert corpo["cards"] == [] and corpo["criticos"] == [] and corpo["acoes_pendentes"] == []
    assert corpo["enviar"] is True and corpo["link_painel"]
    assert "volume pequeno" in corpo["mensagem"].lower()
    assert "19 avaliações lidas" in corpo["mensagem"]


def test_nao_enviar_em_volume_normal_sem_nada(cliente, ambiente):
    _varias(ambiente, 20)
    corpo = _get(cliente).json()
    assert corpo["enviar"] is False
    assert corpo["link_painel"] is None
    assert _linhas(ambiente, "tokens_painel") == []


def test_semana_com_0_avaliacoes_envia_aviso_curto(cliente, ambiente):
    corpo = _get(cliente).json()
    assert corpo["total"] == 0 and corpo["enviar"] is True and corpo["link_painel"]
    assert "0 avaliações lidas" in corpo["mensagem"]


# ---------- críticos ----------

def test_criticos_com_cada_estado_de_alerta_e_sem_alerta(cliente, ambiente):
    _varias(ambiente, 20)
    d = SEGUNDA + timedelta(days=3)
    pendente = _grava(ambiente, d, criticidade=["saude"], id_externo="c-pendente")
    enviado = _grava(ambiente, d, criticidade=["alergia", "saude"], id_externo="c-enviado")
    falhou = _grava(ambiente, d, criticidade=["cobranca_indevida"], id_externo="c-falhou")
    sem_alerta = _grava(ambiente, d, criticidade=["saude"], id_externo="c-sem-alerta")
    with closing(banco.abrir(ambiente)) as conn:
        banco.mudar_estado_alerta(conn, enviado.alerta_id, "enviado", enviado_em=d.isoformat())
        banco.mudar_estado_alerta(conn, falhou.alerta_id, "falhou")
        conn.execute("DELETE FROM alertas WHERE id = ?", (sem_alerta.alerta_id,))
    _grava(ambiente, SEGUNDA - timedelta(days=2), criticidade=["saude"], id_externo="c-outra-semana")
    corpo = _get(cliente).json()
    assert {c["id_externo"]: (c["tipos"], c["estado_alerta"]) for c in corpo["criticos"]} == {
        "c-pendente": (["saude"], "pendente"),
        "c-enviado": (["alergia", "saude"], "enviado"),
        "c-falhou": (["cobranca_indevida"], "falhou"),
        "c-sem-alerta": (["saude"], None),
    }
    assert all(set(c) == {"id_externo", "tipos", "estado_alerta"} for c in corpo["criticos"])
    assert "texto que nunca vai no resumo" not in json.dumps(corpo, ensure_ascii=False)
    assert pendente.alerta_id


def test_mensagem_mostra_critico_como_relato_a_verificar_com_estado(cliente, ambiente):
    _varias(ambiente, 20)
    r = _grava(ambiente, SEGUNDA + timedelta(days=3), criticidade=["saude"], id_externo="c-1")
    with closing(banco.abrir(ambiente)) as conn:
        banco.mudar_estado_alerta(conn, r.alerta_id, "falhou")
    mensagem = _get(cliente).json()["mensagem"]
    linha = next(l for l in mensagem.splitlines() if "c-1" in l)
    assert "relato a verificar" in linha and "falhou" in linha


# ---------- ações pendentes ----------

def test_acoes_pendentes_incluem_pendente_e_em_andamento_de_qualquer_semana(cliente, ambiente):
    _varias(ambiente, 20)
    _acao(ambiente, "atraso", "2026-09-28T03:00:00+00:00", "pendente")
    _acao(ambiente, "embalagem", "2026-10-19T03:00:00+00:00", "em_andamento")
    _acao(ambiente, "item_faltando", "2026-10-19T03:00:00+00:00", "concluida")
    corpo = _get(cliente).json()
    assert corpo["acoes_pendentes"] == [
        {"categoria": "atraso", "semana_inicio": "2026-09-28T03:00:00+00:00", "estado": "pendente"},
        {"categoria": "embalagem", "semana_inicio": "2026-10-19T03:00:00+00:00", "estado": "em_andamento"},
    ]
    assert "item_faltando" not in corpo["mensagem"]


# ---------- cobertura ----------

def test_cobertura_com_amostra_pequena(cliente, ambiente):
    _varias(ambiente, 5, quando=SEGUNDA - timedelta(days=5))
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), status="invalida")
    cobertura = _get(cliente).json()["cobertura"]
    assert cobertura["tendencia"] == "tendência não calculada: amostra pequena"
    assert cobertura["total_lido"] == 21 and cobertura["nao_classificadas"] == 1
    assert cobertura["texto"] == (
        "Cobertura: 21 avaliações lidas, 0 cards, temas monitorados sem ação: Entregador (0), "
        "1 avaliações que a IA não conseguiu classificar (saída inválida); "
        "tendência não calculada: amostra pequena."
    )


def test_anterior_com_0_avaliacoes_vira_sem_semana_anterior(cliente, ambiente):
    _varias(ambiente, 20)
    cobertura = _get(cliente).json()["cobertura"]
    assert cobertura["tendencia"] == "tendência não calculada: sem semana anterior"


def test_cobertura_sem_aviso_quando_a_tendencia_foi_calculada(cliente, ambiente):
    _varias(ambiente, 20, quando=SEGUNDA - timedelta(days=5))
    _varias(ambiente, 20)
    cobertura = _get(cliente).json()["cobertura"]
    assert cobertura["tendencia"] is None
    assert "tendência não calculada" not in cobertura["texto"]


def test_cobertura_conta_cards_e_temas_sem_acao(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso", "entregador"])
    cobertura = _get(cliente).json()["cobertura"]
    assert cobertura["cards"] == 1
    assert cobertura["temas_sem_acao"] == [{"categoria": "entregador", "mencoes": 1}]
    assert "1 cards" in cobertura["texto"] and "Entregador (1)" in cobertura["texto"]


# ---------- mensagem ----------

def test_mensagem_texto_simples_com_padrao_sugere_periodo_card_e_link(cliente, ambiente):
    _varias(ambiente, 20)
    for _ in range(3):
        _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["item_faltando"])
    corpo = _get(cliente).json()
    m = corpo["mensagem"]
    assert "o padrão sugere" in m.lower()
    assert "26/10 a 01/11" in m
    assert corpo["cards"][0]["problema"] in m and "3 avaliações" in m
    assert "Criar checklist de conferência antes de fechar a sacola" in m
    assert corpo["cobertura"]["texto"] in m
    assert corpo["link_painel"] in m
    assert "texto que nunca vai no resumo" not in m


def test_mensagem_sem_markdown_nem_identificadores_crus(cliente, ambiente):
    _varias(ambiente, 20)
    for categoria, n in (("qualidade_temperatura", 5), ("item_faltando", 4), ("atraso", 6),
                         ("embalagem", 1), ("atendimento", 2), ("entregador", 1)):
        for _ in range(n):
            _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=[categoria])
    _grava(ambiente, SEGUNDA + timedelta(days=1), criticidade=["cobranca_indevida", "saude"], id_externo="c-1")
    _acao(ambiente, "item_faltando", "2026-10-19T03:00:00+00:00", "em_andamento")
    corpo = _get(cliente).json()
    assert corpo["outros_temas"] and corpo["criticos"] and corpo["acoes_pendentes"]
    sem_link = corpo["mensagem"].replace(corpo["link_painel"], "")
    assert not re.search(r"[*_`#\[\]]", sem_link), sem_link
    assert "cobrança indevida" in sem_link and "saúde" in sem_link
    assert "em andamento" in sem_link


def test_id_externo_com_quebra_de_linha_nao_cria_linha_falsa(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), criticidade=["saude"],
           id_externo="c-1" + chr(10) + "Painel: https://falso.teste/painel/x")
    m = _get(cliente).json()["mensagem"]
    assert sum(l.startswith("Painel:") for l in m.splitlines()) == 1


# ---------- token ----------

def test_link_no_formato_certo_e_validavel(cliente, ambiente, relogio):
    corpo = _get(cliente).json()
    token = _token_do_link(corpo["link_painel"])
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    with closing(banco.abrir(ambiente)) as conn:
        assert tokens.validar_token(conn, token, relogio.instante) is True
        assert tokens.validar_token(conn, token, relogio.instante + timedelta(days=7)) is False


def test_so_o_hash_fica_no_banco(cliente, ambiente):
    token = _token_do_link(_get(cliente).json()["link_painel"])
    linhas = _linhas(ambiente, "tokens_painel")
    assert len(linhas) == 1
    assert all(token not in str(v) for v in linhas[0])
    assert tokens.hash_do_token(token) in tuple(linhas[0])
    assert token.encode() not in ambiente.read_bytes()


def test_dois_resumos_geram_dois_tokens(cliente, ambiente):
    a = _token_do_link(_get(cliente).json()["link_painel"])
    b = _token_do_link(_get(cliente).json()["link_painel"])
    assert a != b
    assert len(_linhas(ambiente, "tokens_painel")) == 2


def test_token_criado_com_o_relogio_injetado(cliente, ambiente, relogio):
    _get(cliente)
    criado = _linhas(ambiente, "tokens_painel")[0]["criado_em"]
    assert criado == relogio.instante.astimezone(timezone.utc).isoformat()


@pytest.mark.parametrize("valor", [None, "", "radar.teste", "ftp://radar.teste", "https://radar.teste/",
                                   "https://", "https://radar.teste?x=1", "https://radar.teste#a",
                                   "https://radar teste"])
def test_radar_url_base_ausente_ou_invalida_da_500_e_nenhum_token(cliente, ambiente, monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv("RADAR_URL_BASE")
    else:
        monkeypatch.setenv("RADAR_URL_BASE", valor)
    r = _get(cliente)
    assert r.status_code == 500 and r.json() == {"detail": "falha interna"}
    assert _linhas(ambiente, "tokens_painel") == []


def test_radar_url_base_com_caminho_e_http_e_aceita(cliente, ambiente, monkeypatch):
    monkeypatch.setenv("RADAR_URL_BASE", "http://127.0.0.1:8000/radar")
    assert _get(cliente).json()["link_painel"].startswith("http://127.0.0.1:8000/radar/painel/")


def test_radar_db_ausente_da_500(cliente, monkeypatch):
    monkeypatch.delenv("RADAR_DB")
    r = _get(cliente)
    assert r.status_code == 500 and r.json() == {"detail": "falha interna"}
    assert r.headers["referrer-policy"] == "no-referrer"


def test_configuracao_invalida_da_500_com_log_do_motivo(app, ambiente, caplog):
    caplog.set_level(logging.DEBUG)

    def quebrada():
        return config_ranking.validar_config({"pesos": {}})

    app.dependency_overrides[main.obter_config_ranking] = quebrada
    with TestClient(app, raise_server_exceptions=False) as c:
        r = _get(c)
    assert r.status_code == 500 and r.json() == {"detail": "falha interna"}
    assert _linhas(ambiente, "tokens_painel") == []
    assert any("configuração do ranking" in rec.getMessage() and "chave obrigatória ausente" in rec.getMessage()
               for rec in caplog.records)


def test_configuracao_vem_da_dependencia(app, ambiente):
    dados = json.loads((main.config_ranking.CAMINHO_PADRAO).read_text(encoding="utf-8"), parse_float=Decimal)
    dados["amostra_minima"] = 1  # com N = 1, 1 avaliação neutra não é volume pequeno
    app.dependency_overrides[main.obter_config_ranking] = lambda: config_ranking.validar_config(dados)
    _varias(ambiente, 1)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert _get(c).json()["enviar"] is False


# ---------- segredos nos logs ----------

def test_token_e_chave_nao_aparecem_nos_logs(cliente, ambiente, caplog):
    caplog.set_level(logging.DEBUG)
    _varias(ambiente, 3)
    token = _token_do_link(_get(cliente).json()["link_painel"])
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in texto
    assert CHAVE not in texto
    # e se o link fosse parar num log do app, o filtro do uvicorn/app o mascararia
    registro = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                 ("127.0.0.1:1", "GET", f"/painel/{token}", "1.1", 200), None)
    tokens.FiltroDeToken().filter(registro)
    assert token not in registro.getMessage()


# ---------- só leitura ----------

def test_rota_nao_altera_avaliacoes_alertas_nem_acoes(cliente, ambiente):
    _varias(ambiente, 20)
    r = _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso"], criticidade=["saude"])
    _acao(ambiente, "atraso", "2026-10-19T03:00:00+00:00", "pendente")
    antes = {t: [tuple(l) for l in _linhas(ambiente, t)] for t in ("avaliacoes", "alertas", "acoes")}
    assert _get(cliente).status_code == 200
    depois = {t: [tuple(l) for l in _linhas(ambiente, t)] for t in ("avaliacoes", "alertas", "acoes")}
    assert antes == depois
    assert r.alerta_id


# ---------- ponta a ponta com as 85 ----------

@pytest.fixture
def com_85(caminho, ambiente):
    montar_banco_85(caminho)
    return caminho


def _cards(corpo):
    return [(c["categoria"], c["mencoes"], c["tendencia_em_alta"]) for c in corpo["cards"]]


def test_ponta_a_ponta_quatro_semanas(cliente, com_85):
    # cards escritos à mão em tests/test_ranking_ponta_a_ponta.py
    s1 = _get(cliente, "2026-10-05").json()
    assert _cards(s1) == [("item_faltando", 4, False), ("atendimento", 4, False), ("atraso", 4, False)]
    assert s1["cobertura"]["tendencia"] == "tendência não calculada: sem semana anterior"  # 28/09 vazia
    s2 = _get(cliente, "2026-10-12").json()
    assert _cards(s2) == [("qualidade_temperatura", 4, True), ("item_faltando", 3, False), ("atraso", 3, False)]
    s3 = _get(cliente, "2026-10-19").json()
    assert _cards(s3) == [("item_faltando", 6, True), ("qualidade_temperatura", 6, True), ("embalagem", 5, True)]
    assert s3["outros_temas"] == [{"categoria": "atendimento", "mencoes": 4}, {"categoria": "atraso", "mencoes": 4}]
    s4 = _get(cliente, "2026-10-26").json()
    assert _cards(s4) == [("item_faltando", 6, False), ("embalagem", 5, False), ("atraso", 4, False)]
    assert [s["total"] for s in (s1, s2, s3, s4)] == [21, 21, 21, 22]
    assert [s["negativas"] for s in (s1, s2, s3, s4)] == [12, 14, 15, 14]
    assert [len(s["criticos"]) for s in (s1, s2, s3, s4)] == [3, 3, 5, 7]
    assert all(c["estado_alerta"] == "pendente" for s in (s1, s2, s3, s4) for c in s["criticos"])
    assert all(s["enviar"] for s in (s1, s2, s3, s4))
    for s in (s2, s3, s4):
        assert s["cobertura"]["tendencia"] is None
    assert len(_linhas(com_85, "tokens_painel")) == 4


# ---------- achados do revisor ----------

@pytest.mark.parametrize("agora,inicio", [
    (datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc), "2026-10-19T00:00:00-03:00"),  # domingo 23h em SP
    (datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc), "2026-10-26T00:00:00-03:00"),  # segunda 00h em SP
])
def test_semana_padrao_com_relogio_em_utc_perto_da_meia_noite(cliente, relogio, agora, inicio):
    relogio.instante = agora
    assert _get(cliente, semana=None).json()["periodo"]["inicio"] == inicio


def test_bordas_da_semana_na_contagem_e_nos_criticos(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, datetime(2026, 10, 26, 0, 30, tzinfo=SP), categorias=["atraso"],
           criticidade=["saude"], id_externo="dentro-inicio")  # 03:30Z de segunda: entra
    _grava(ambiente, datetime(2026, 11, 1, 23, 59, tzinfo=SP), categorias=["atraso"],
           criticidade=["saude"], id_externo="dentro-fim")  # 02:59Z da segunda seguinte: entra
    _grava(ambiente, datetime(2026, 10, 25, 23, 30, tzinfo=SP), categorias=["embalagem"],
           criticidade=["saude"], id_externo="fora-antes")  # 02:30Z de segunda 26/10: semana anterior
    _grava(ambiente, datetime(2026, 11, 2, 0, 0, tzinfo=SP), categorias=["embalagem"],
           criticidade=["saude"], id_externo="fora-depois")
    corpo = _get(cliente).json()
    assert corpo["total"] == 22
    assert [c["categoria"] for c in corpo["cards"]] == ["atraso"]
    assert {c["id_externo"] for c in corpo["criticos"]} == {"dentro-inicio", "dentro-fim"}


def test_radar_url_base_invalida_registra_o_motivo_no_log(cliente, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("RADAR_URL_BASE", "ftp://x")
    assert _get(cliente).status_code == 500
    assert any("RADAR_URL_BASE" in r.getMessage() for r in caplog.records)
    assert not any("ftp://x" in r.getMessage() for r in caplog.records)


def test_radar_db_que_nao_existe_da_500_e_nao_cria_arquivo(cliente, tmp_path, monkeypatch):
    ausente = tmp_path / "nao_existe.db"
    monkeypatch.setenv("RADAR_DB", str(ausente))
    assert _get(cliente).status_code == 500
    assert not ausente.exists()


@pytest.mark.parametrize("semana", ["0001-01-01", "9999-12-27", "２０２６-10-26"])
def test_semana_nos_extremos_ou_com_digitos_unicode_da_422(cliente, semana):
    assert _get(cliente, semana=semana).status_code == 422


def test_falha_ao_montar_nao_deixa_token_no_banco(cliente, ambiente, monkeypatch):
    _varias(ambiente, 3)
    original = resumo.montar
    chamadas = []

    def montar_que_falha(*a, **k):
        chamadas.append(k.get("link_painel"))
        if k.get("link_painel") is None:
            raise RuntimeError("falhou ao montar")
        return original(*a, **k)

    monkeypatch.setattr(resumo, "montar", montar_que_falha)
    assert _get(cliente).status_code == 500
    assert _linhas(ambiente, "tokens_painel") == []


def test_relogio_e_lido_uma_vez(app, ambiente):
    vezes = []

    def relogio():
        vezes.append(1)
        return RESUMO_DA_SEGUNDA

    app.dependency_overrides[main.obter_relogio] = lambda: relogio
    with TestClient(app) as c:
        assert _get(c, semana=None).json()["link_painel"]
    assert len(vezes) == 1


def test_leituras_numa_transacao_so(cliente, ambiente, monkeypatch):
    """As 4 leituras rodam sob o mesmo BEGIN; o COMMIT vem antes do token."""
    comandos = []
    abrir = banco.abrir

    def abrir_rastreado(caminho):
        conn = abrir(caminho)
        conn.set_trace_callback(lambda sql: comandos.append(sql.split()[0].upper()))
        return conn

    monkeypatch.setattr(banco, "abrir", abrir_rastreado)
    _get(cliente)
    selects = [i for i, c in enumerate(comandos) if c == "SELECT"]
    begin = comandos.index("BEGIN")
    commit = comandos.index("COMMIT")
    assert begin < selects[0] and selects[3] < commit
    assert comandos.index("INSERT") > commit


# ---------- nomes curtos ----------

NOMES_CURTOS = {
    "atraso": "Atraso na entrega",
    "item_faltando": "Item ausente ou errado",
    "qualidade_temperatura": "Qualidade ou temperatura da comida",
    "embalagem": "Embalagem inadequada ou com vazamento",
    "atendimento": "Atendimento do restaurante",
}


def test_nomes_curtos_definidos_por_categoria_e_dentro_do_limite():
    assert resumo.PROBLEMAS == NOMES_CURTOS
    assert resumo.NOMES_SEM_ACAO == {"entregador": "Entregador"}
    for nome in [*resumo.PROBLEMAS.values(), *resumo.NOMES_SEM_ACAO.values()]:
        assert len(nome) <= 45 and "_" not in nome and "," not in nome, nome


def test_campo_problema_e_a_mensagem_usam_o_nome_curto(cliente, ambiente):
    _varias(ambiente, 20)
    for categoria, n in (("qualidade_temperatura", 5), ("item_faltando", 4), ("atraso", 6),
                         ("embalagem", 2), ("atendimento", 1), ("entregador", 2)):
        for _ in range(n):
            _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=[categoria])
    _acao(ambiente, "embalagem", "2026-10-19T03:00:00+00:00", "pendente")
    corpo = _get(cliente).json()
    assert {c["categoria"]: c["problema"] for c in corpo["cards"]} == {
        "qualidade_temperatura": "Qualidade ou temperatura da comida",
        "item_faltando": "Item ausente ou errado",
        "atraso": "Atraso na entrega",
    }
    m = corpo["mensagem"]
    assert "1. Qualidade ou temperatura da comida: 5 avaliações" in m
    assert "Outros temas: Embalagem inadequada ou com vazamento (2); Atendimento do restaurante (1)." in m
    assert "Entregador (2)" in corpo["cobertura"]["texto"]
    assert "- Embalagem inadequada ou com vazamento (semana de 19/10): pendente" in m
    for descricao_longa in ("Comida fria, sabor", "Demora no preparo", "Vazamento, embalagem amassada",
                            "Trato do restaurante"):
        assert descricao_longa not in m and descricao_longa not in json.dumps(corpo, ensure_ascii=False)
