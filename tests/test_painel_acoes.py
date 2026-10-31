"""Testes das rotas de ação do painel, POST e PATCH (bloco 4.2). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (decisões de 31/10/2026 sobre as evidências do POST /acoes, as
rotas de leitura e as rotas de ação do painel; token do painel; tabelas acoes e acao_avaliacoes). Os
tokens são sempre criados por tokens.criar_token_painel. Nenhum teste usa rede externa.
"""

import json
import logging
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import banco, tokens, trechos
from test_painel_leitura import INICIO_UTC, _avaliacao, _token
from test_resumo_semanal import (  # noqa: F401 - fixtures usadas pelos testes
    CHAVE, RESUMO_DA_SEGUNDA, SEGUNDA, _get, _linhas, _varias, ambiente, app, caminho, cliente, relogio,
)

SEMANA = "2026-10-26"  # segunda-feira da semana de SEGUNDA (a última completa no relógio dos testes)
NOTA_SECRETA = "nota secreta do dono que nao vai para o log"  # com espaços: o filtro de token não a mascara
LINK_INVALIDO = {"detail": "link inválido ou expirado"}
NAO_ENCONTRADA = {"detail": "ação não encontrada"}


@pytest.fixture
def token(ambiente):
    return _token(ambiente)


@pytest.fixture
def avs(ambiente):
    """Três avaliações de atraso na semana de 26/10, com textos longos (o trecho é derivado pelo servidor)."""
    textos = [f"relato {i}: a entrega demorou " + "muito " * 40 for i in range(3)]
    return [(_avaliacao(ambiente, SEGUNDA + timedelta(days=1, hours=i), texto=t), t) for i, t in enumerate(textos)]


def _corpo(avs, n=1, **extra):
    corpo = {"categoria": "atraso", "semana_inicio": SEMANA,
             "evidencias": [{"avaliacao_id": avs[i][0], "posicao": i + 1} for i in range(n)]}
    corpo.update(extra)
    return corpo


def _post(cliente, token, corpo, **k):
    return cliente.post(f"/api/painel/{token}/acoes", json=corpo, **k)


def _patch(cliente, token, acao_id, corpo, **k):
    return cliente.patch(f"/api/painel/{token}/acoes/{acao_id}", json=corpo, **k)


def _tokens_invalidos(ambiente):
    expirado = _token(ambiente, agora=RESUMO_DA_SEGUNDA - timedelta(days=8))
    revogado = _token(ambiente)
    with closing(banco.abrir(ambiente)) as conn:
        tokens.revogar_token(conn, revogado, RESUMO_DA_SEGUNDA)
    return {
        "inexistente": secrets.token_urlsafe(32), "expirado": expirado, "revogado": revogado,
        "malformado": "malformado", "outro_escopo": _token(ambiente, escopo="outro"),
    }


# ---------- token: 404 uniforme, antes do corpo ----------

def test_404_uniforme_no_post_e_no_patch(cliente, ambiente, token, avs):
    acao_id = _post(cliente, token, _corpo(avs)).json()["id"]
    respostas = []
    for nome, t in _tokens_invalidos(ambiente).items():
        respostas.append(_post(cliente, t, _corpo(avs, categoria="embalagem")))
        respostas.append(_patch(cliente, t, acao_id, {"estado": "concluida"}))
    for r in respostas:
        assert r.status_code == 404 and r.json() == LINK_INVALIDO
        assert r.content == respostas[0].content
    assert _linhas(ambiente, "acoes")[0]["estado"] == "pendente"  # nada mudou
    assert len(_linhas(ambiente, "acoes")) == 1


def test_404_vem_antes_do_422_do_corpo(cliente, ambiente):
    t = _tokens_invalidos(ambiente)["expirado"]
    for corpo in ({"categoria": "atraso", "trecho_exibido": "x"}, {}, [], "texto"):
        assert _post(cliente, t, corpo).status_code == 404
    assert cliente.post(f"/api/painel/{t}/acoes", content=b"{nao e json",
                        headers={"Content-Type": "application/json"}).status_code == 404
    assert cliente.patch(f"/api/painel/{t}/acoes/abc", json={"estado": "x"}).status_code == 404
    assert cliente.patch(f"/api/painel/{t}/acoes/1", content=b"{lixo",
                         headers={"Content-Type": "application/json"}).status_code == 404


# ---------- POST feliz ----------

def test_post_feliz_201_formato_fusos_e_trechos_derivados(cliente, ambiente, token, avs):
    r = _post(cliente, token, _corpo(avs, n=3, nota="ligar para o motoboy"))
    assert r.status_code == 201
    corpo = r.json()
    assert set(corpo) == {"id", "categoria", "semana_inicio", "estado", "criada_em", "concluida_em", "nota",
                          "evidencias"}
    assert corpo["categoria"] == "atraso" and corpo["estado"] == "pendente"
    assert corpo["semana_inicio"] == "2026-10-26T00:00:00-03:00"
    assert corpo["criada_em"] == "2026-11-02T08:00:00-03:00"  # relógio injetado, em São Paulo
    assert corpo["concluida_em"] is None and corpo["nota"] == "ligar para o motoboy"
    assert corpo["evidencias"] == [
        {"posicao": i + 1, "avaliacao_id": avs[i][0], "trecho_exibido": trechos.trecho(avs[i][1])} for i in range(3)]
    linha = _linhas(ambiente, "acoes")[0]
    assert linha["semana_inicio"] == INICIO_UTC  # gravada em UTC
    assert linha["criada_em"] == "2026-11-02T11:00:00+00:00"
    gravadas = _linhas_evidencias(ambiente)
    assert [(g["posicao"], g["trecho_exibido"]) for g in gravadas] == [
        (i + 1, trechos.trecho(avs[i][1])) for i in range(3)]


def _linhas_evidencias(ambiente):
    with closing(banco.abrir(ambiente)) as conn:
        return [dict(l) for l in conn.execute("SELECT * FROM acao_avaliacoes ORDER BY posicao")]


def test_post_sem_nota_tem_nota_nula(cliente, token, avs):
    assert _post(cliente, token, _corpo(avs)).json()["nota"] is None


def test_semana_atual_e_aceita(cliente, ambiente, token):
    av = _avaliacao(ambiente, RESUMO_DA_SEGUNDA)  # segunda 02/11, 08:00: semana atual
    r = _post(cliente, token, {"categoria": "atraso", "semana_inicio": "2026-11-02",
                               "evidencias": [{"avaliacao_id": av, "posicao": 1}]})
    assert r.status_code == 201


# ---------- POST: corpo inválido ----------

@pytest.mark.parametrize("extra", [
    {"trecho_exibido": "eu escolho o trecho"}, {"id": 99}, {"estado": "concluida"}, {"criada_em": "x"},
])
def test_campo_extra_no_corpo_e_422(cliente, ambiente, token, avs, extra):
    assert _post(cliente, token, _corpo(avs, **extra)).status_code == 422
    assert _linhas(ambiente, "acoes") == []


def test_campo_extra_dentro_da_evidencia_e_422(cliente, ambiente, token, avs):
    corpo = _corpo(avs)
    corpo["evidencias"][0]["trecho_exibido"] = "texto forjado <script>alert(1)</script>"
    assert _post(cliente, token, corpo).status_code == 422
    assert _linhas(ambiente, "acoes") == []


@pytest.mark.parametrize("categoria", ["entregador", "desconhecida", "", 3, None])
def test_categoria_entregador_ou_desconhecida_e_422(cliente, ambiente, token, avs, categoria):
    assert _post(cliente, token, _corpo(avs, categoria=categoria)).status_code == 422
    assert _linhas(ambiente, "acoes") == []


def test_evidencia_inexistente_de_outra_semana_ou_de_outra_categoria_e_422(cliente, ambiente, token):
    outra_semana = _avaliacao(ambiente, SEGUNDA - timedelta(days=2))
    outra_categoria = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), categorias=["embalagem"])
    for avaliacao_id in (999999, outra_semana, outra_categoria):
        r = _post(cliente, token, {"categoria": "atraso", "semana_inicio": SEMANA,
                                   "evidencias": [{"avaliacao_id": avaliacao_id, "posicao": 1}]})
        assert r.status_code == 422, avaliacao_id
    assert _linhas(ambiente, "acoes") == []


def test_uma_evidencia_ruim_entre_boas_recusa_tudo(cliente, ambiente, token, avs):
    outra_semana = _avaliacao(ambiente, SEGUNDA - timedelta(days=2))
    corpo = _corpo(avs, n=2)
    corpo["evidencias"].append({"avaliacao_id": outra_semana, "posicao": 3})
    assert _post(cliente, token, corpo).status_code == 422
    assert _linhas(ambiente, "acoes") == [] and _linhas_evidencias(ambiente) == []


@pytest.mark.parametrize("evidencias", [
    "posicao_repetida", "avaliacao_repetida", "zero", "quatro", "posicao_0", "posicao_4", "posicao_texto",
    "sem_posicao", "nao_lista",
])
def test_evidencias_mal_formadas_sao_422(cliente, ambiente, token, avs, evidencias):
    (a, _), (b, _), (c, _) = avs
    d = _avaliacao(ambiente, SEGUNDA + timedelta(days=2))
    valores = {
        "posicao_repetida": [{"avaliacao_id": a, "posicao": 1}, {"avaliacao_id": b, "posicao": 1}],
        "avaliacao_repetida": [{"avaliacao_id": a, "posicao": 1}, {"avaliacao_id": a, "posicao": 2}],
        "zero": [],
        "quatro": [{"avaliacao_id": x, "posicao": p} for x, p in ((a, 1), (b, 2), (c, 3), (d, 1))],
        "posicao_0": [{"avaliacao_id": a, "posicao": 0}],
        "posicao_4": [{"avaliacao_id": a, "posicao": 4}],
        "posicao_texto": [{"avaliacao_id": a, "posicao": "1"}],
        "sem_posicao": [{"avaliacao_id": a}],
        "nao_lista": {"avaliacao_id": a, "posicao": 1},
    }
    assert _post(cliente, token, _corpo(avs, evidencias=valores[evidencias])).status_code == 422
    assert _linhas(ambiente, "acoes") == []


@pytest.mark.parametrize("semana", ["2026-10-27", "26/10/2026", "2026-10-26T00:00:00-03:00", "", "2026-11-09"])
def test_semana_inicio_nao_segunda_formato_errado_ou_futura_e_422(cliente, ambiente, token, avs, semana):
    assert _post(cliente, token, _corpo(avs, semana_inicio=semana)).status_code == 422
    assert _linhas(ambiente, "acoes") == []


def test_nota_de_500_aceita_e_de_501_recusada(cliente, ambiente, token, avs):
    assert _post(cliente, token, _corpo(avs, nota="x" * 501)).status_code == 422
    r = _post(cliente, token, _corpo(avs, nota="x" * 500))
    assert r.status_code == 201 and r.json()["nota"] == "x" * 500


def test_nota_que_nao_e_texto_e_422(cliente, token, avs):
    assert _post(cliente, token, _corpo(avs, nota=123)).status_code == 422


# ---------- duplicata e corrida ----------

def test_duplicata_e_409_com_id_e_estado_da_existente(cliente, ambiente, token, avs):
    primeira = _post(cliente, token, _corpo(avs)).json()
    _patch(cliente, token, primeira["id"], {"estado": "em_andamento"})
    r = _post(cliente, token, _corpo(avs, n=2, nota="outra"))
    assert r.status_code == 409
    assert r.json()["id"] == primeira["id"] and r.json()["estado"] == "em_andamento"
    assert len(_linhas(ambiente, "acoes")) == 1 and len(_linhas_evidencias(ambiente)) == 1


def test_mesma_categoria_em_outra_semana_nao_e_duplicata(cliente, ambiente, token, avs):
    assert _post(cliente, token, _corpo(avs)).status_code == 201
    av = _avaliacao(ambiente, SEGUNDA - timedelta(days=5))
    r = _post(cliente, token, {"categoria": "atraso", "semana_inicio": "2026-10-19",
                               "evidencias": [{"avaliacao_id": av, "posicao": 1}]})
    assert r.status_code == 201


def test_duas_requisicoes_simultaneas_um_201_e_um_409(app, ambiente, token, avs):
    barreira = threading.Barrier(2, timeout=10)
    codigos = []

    def rodar():
        with TestClient(app, raise_server_exceptions=False) as c:
            barreira.wait()
            codigos.append(_post(c, token, _corpo(avs)).status_code)

    threads = [threading.Thread(target=rodar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(codigos) == [201, 409]
    assert len(_linhas(ambiente, "acoes")) == 1


def test_indice_unico_no_banco(ambiente):
    with closing(banco.abrir(ambiente)) as conn:
        conn.execute("INSERT INTO acoes (categoria, semana_inicio, estado, criada_em) VALUES ('atraso', 's', 'pendente', 'c')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO acoes (categoria, semana_inicio, estado, criada_em) "
                         "VALUES ('atraso', 's', 'pendente', 'c')")


def test_indice_unico_e_criado_em_banco_antigo_e_e_idempotente(tmp_path):
    antigo = tmp_path / "antigo.db"
    with closing(sqlite3.connect(antigo)) as conn:
        conn.execute("CREATE TABLE acoes (id INTEGER PRIMARY KEY AUTOINCREMENT, categoria TEXT NOT NULL, "
                     "semana_inicio TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'pendente', criada_em TEXT NOT NULL, "
                     "concluida_em TEXT, nota TEXT)")
        conn.execute("INSERT INTO acoes (categoria, semana_inicio, criada_em) VALUES ('atraso', 's', 'c')")
        conn.commit()
    for _ in range(2):
        with closing(banco.abrir(antigo)) as conn:
            indices = [l[1] for l in conn.execute("PRAGMA index_list(acoes)") if l[2] == 1]
            assert indices, "índice único ausente"
            assert conn.execute("SELECT COUNT(*) FROM acoes").fetchone()[0] == 1


# ---------- atomicidade ----------

def test_erro_ao_gravar_a_evidencia_desfaz_a_acao(ambiente):
    with closing(banco.abrir(ambiente)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            banco.registrar_acao(conn, categoria="atraso", semana_inicio=INICIO_UTC,
                                 criada_em="2026-11-02T11:00:00+00:00", nota=None,
                                 evidencias=[(999999, 1, "trecho")])  # avaliação inexistente: viola a FK
    assert _linhas(ambiente, "acoes") == [] and _linhas_evidencias(ambiente) == []


def test_erro_na_segunda_evidencia_desfaz_a_primeira(ambiente, avs):
    with closing(banco.abrir(ambiente)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            banco.registrar_acao(conn, categoria="atraso", semana_inicio=INICIO_UTC,
                                 criada_em="2026-11-02T11:00:00+00:00", nota=None,
                                 evidencias=[(avs[0][0], 1, "ok"), (999999, 2, "ruim")])
    assert _linhas(ambiente, "acoes") == [] and _linhas_evidencias(ambiente) == []


# ---------- PATCH: transições ----------

@pytest.fixture
def acao(cliente, token, avs):
    return _post(cliente, token, _corpo(avs)).json()


def _para(cliente, token, acao_id, *estados):
    return [_patch(cliente, token, acao_id, {"estado": e}) for e in estados]


@pytest.mark.parametrize("caminho_de_estados", [
    ("em_andamento",), ("concluida",), ("em_andamento", "concluida"),
])
def test_transicoes_validas(cliente, token, acao, caminho_de_estados):
    for r in _para(cliente, token, acao["id"], *caminho_de_estados):
        assert r.status_code == 200
    assert r.json()["estado"] == caminho_de_estados[-1]


@pytest.mark.parametrize("antes,depois", [
    (("em_andamento",), "pendente"), (("concluida",), "pendente"), (("concluida",), "em_andamento"),
    (("em_andamento", "concluida"), "em_andamento"), (("em_andamento", "concluida"), "pendente"),
])
def test_transicoes_invalidas_sao_409_com_o_estado_atual(cliente, ambiente, token, acao, antes, depois):
    _para(cliente, token, acao["id"], *antes)
    r = _patch(cliente, token, acao["id"], {"estado": depois})
    assert r.status_code == 409 and r.json()["estado"] == antes[-1]
    assert _linhas(ambiente, "acoes")[0]["estado"] == antes[-1]


@pytest.mark.parametrize("estado", ["pendente", "em_andamento", "concluida"])
def test_repetir_o_mesmo_estado_e_200_sem_mudanca(cliente, ambiente, token, acao, estado, relogio):
    if estado != "pendente":
        _para(cliente, token, acao["id"], estado)
    antes = _linhas(ambiente, "acoes")[0]
    relogio.instante = RESUMO_DA_SEGUNDA + timedelta(hours=5)
    r = _patch(cliente, token, acao["id"], {"estado": estado})
    assert r.status_code == 200 and r.json()["estado"] == estado
    assert _linhas(ambiente, "acoes")[0] == antes


def test_concluida_em_gravado_uma_vez_e_preservado(cliente, ambiente, token, acao, relogio):
    relogio.instante = RESUMO_DA_SEGUNDA + timedelta(hours=1)
    r = _patch(cliente, token, acao["id"], {"estado": "concluida"})
    assert r.json()["concluida_em"] == "2026-11-02T09:00:00-03:00"
    relogio.instante = RESUMO_DA_SEGUNDA + timedelta(days=2)
    assert _patch(cliente, token, acao["id"], {"estado": "concluida"}).json()["concluida_em"] == "2026-11-02T09:00:00-03:00"
    assert _patch(cliente, token, acao["id"], {"nota": "depois"}).json()["concluida_em"] == "2026-11-02T09:00:00-03:00"
    assert _linhas(ambiente, "acoes")[0]["concluida_em"] == "2026-11-02T12:00:00+00:00"


def test_em_andamento_nao_grava_concluida_em(cliente, token, acao):
    assert _patch(cliente, token, acao["id"], {"estado": "em_andamento"}).json()["concluida_em"] is None


def test_nota_editavel_sempre_inclusive_depois_de_concluida(cliente, token, acao):
    _patch(cliente, token, acao["id"], {"estado": "concluida"})
    r = _patch(cliente, token, acao["id"], {"nota": "resolvido com checklist"})
    assert r.status_code == 200 and r.json()["nota"] == "resolvido com checklist" and r.json()["estado"] == "concluida"
    assert _patch(cliente, token, acao["id"], {"nota": None}).json()["nota"] is None  # null apaga a nota


def test_patch_com_estado_e_nota_juntos(cliente, token, acao):
    r = _patch(cliente, token, acao["id"], {"estado": "em_andamento", "nota": "começou"})
    assert r.json()["estado"] == "em_andamento" and r.json()["nota"] == "começou"


def test_transicao_invalida_nao_muda_a_nota_junto(cliente, ambiente, token, acao):
    _patch(cliente, token, acao["id"], {"estado": "concluida"})
    r = _patch(cliente, token, acao["id"], {"estado": "pendente", "nota": "não deve gravar"})
    assert r.status_code == 409
    assert _linhas(ambiente, "acoes")[0]["nota"] is None


@pytest.mark.parametrize("corpo", [
    {}, {"estado": "aberta"}, {"estado": None}, {"nota": "x" * 501}, {"estado": "concluida", "id": 1},
    {"trecho_exibido": "x"}, {"nota": 5},
])
def test_patch_com_corpo_invalido_e_422(cliente, ambiente, token, acao, corpo):
    assert _patch(cliente, token, acao["id"], corpo).status_code == 422
    assert _linhas(ambiente, "acoes")[0]["estado"] == "pendente"


def test_patch_nota_de_500_aceita(cliente, token, acao):
    assert _patch(cliente, token, acao["id"], {"nota": "y" * 500}).status_code == 200


# ---------- nota vazia vira null; nota com texto não é alterada ----------

@pytest.mark.parametrize("vazia", ["", "   ", "\t \n"])
def test_post_com_nota_vazia_ou_so_espacos_grava_null(cliente, ambiente, token, avs, vazia):
    r = _post(cliente, token, _corpo(avs, nota=vazia))
    assert r.status_code == 201 and r.json()["nota"] is None
    assert _linhas(ambiente, "acoes")[0]["nota"] is None


@pytest.mark.parametrize("vazia", ["", "   ", "\t \n"])
def test_patch_com_so_nota_vazia_grava_null_e_nao_e_corpo_vazio(cliente, ambiente, token, avs, vazia):
    acao = _post(cliente, token, _corpo(avs, nota="antiga")).json()
    r = _patch(cliente, token, acao["id"], {"nota": vazia})
    assert r.status_code == 200 and r.json()["nota"] is None
    assert _linhas(ambiente, "acoes")[0]["nota"] is None


def test_patch_nota_com_texto_nao_sofre_strip(cliente, ambiente, token, acao):
    r = _patch(cliente, token, acao["id"], {"nota": "  oi  "})
    assert r.json()["nota"] == "  oi  "
    assert _linhas(ambiente, "acoes")[0]["nota"] == "  oi  "


def test_post_nota_com_texto_nao_sofre_strip(cliente, token, avs):
    assert _post(cliente, token, _corpo(avs, nota="  oi  ")).json()["nota"] == "  oi  "


def test_patch_null_continua_apagando_e_limite_vale_para_o_que_chega(cliente, token, avs):
    acao = _post(cliente, token, _corpo(avs, nota="x")).json()
    assert _patch(cliente, token, acao["id"], {"nota": None}).json()["nota"] is None
    assert _patch(cliente, token, acao["id"], {"nota": " " * 501}).status_code == 422


def test_id_inexistente_404_e_id_nao_inteiro_422(cliente, token, acao):
    for acao_id in ("999999", "-1", "0"):
        r = _patch(cliente, token, acao_id, {"estado": "concluida"})
        assert r.status_code == 404 and r.json() == NAO_ENCONTRADA
    for acao_id in ("abc", "1.5", "1e3"):
        assert _patch(cliente, token, acao_id, {"estado": "concluida"}).status_code == 422


def test_patch_devolve_o_mesmo_formato_do_post(cliente, token, acao):
    r = _patch(cliente, token, acao["id"], {"estado": "em_andamento"}).json()
    assert set(r) == set(acao) and r["evidencias"] == acao["evidencias"] and r["criada_em"] == acao["criada_em"]


# ---------- integração ----------

def test_integracao_resumo_cards_e_historico(cliente, ambiente, token, avs):
    _varias(ambiente, 20)
    acao = _post(cliente, token, _corpo(avs, n=2, nota="checklist")).json()
    resumo = cliente.get("/resumo-semanal", params={"semana": SEMANA}, headers={"X-API-Key": CHAVE}).json()
    assert {"categoria": "atraso", "semana_inicio": INICIO_UTC, "estado": "pendente"} in resumo["acoes_pendentes"]
    cards = {c["categoria"]: c for c in cliente.get(f"/api/painel/{token}/cards").json()["cards"]}
    assert cards["atraso"]["acao"] == {"id": acao["id"], "estado": "pendente"}
    # o texto da avaliação muda depois (reprocessamento, correção): o histórico mostra o trecho GRAVADO
    with closing(banco.abrir(ambiente)) as conn:
        conn.execute("UPDATE avaliacoes SET texto = 'texto novo' WHERE id = ?", (avs[0][0],))
    semana = cliente.get(f"/api/painel/{token}/historico").json()["semanas"][0]
    evid = semana["acoes"][0]["evidencias"]
    assert evid[0]["trecho_exibido"] == trechos.trecho(avs[0][1])
    assert evid[0]["texto_original"] == "texto novo"
    assert semana["acoes"][0]["nota"] == "checklist"
    _patch(cliente, token, acao["id"], {"estado": "concluida"})
    resumo = cliente.get("/resumo-semanal", params={"semana": SEMANA}, headers={"X-API-Key": CHAVE}).json()
    assert all(a["categoria"] != "atraso" for a in resumo["acoes_pendentes"])  # concluída sai das pendentes


# ---------- cabeçalhos, logs e escrita ----------

def test_no_store_em_todos_os_codigos(cliente, ambiente, token, avs):
    criada = _post(cliente, token, _corpo(avs))
    respostas = [
        criada,  # 201
        _post(cliente, token, _corpo(avs)),  # 409
        _post(cliente, token, _corpo(avs, categoria="entregador")),  # 422
        _post(cliente, "malformado", _corpo(avs)),  # 404
        _patch(cliente, token, criada.json()["id"], {"estado": "em_andamento"}),  # 200
        _patch(cliente, token, criada.json()["id"], {"estado": "pendente"}),  # 409
        _patch(cliente, token, "abc", {"estado": "concluida"}),  # 422
        _patch(cliente, token, "999999", {"estado": "concluida"}),  # 404
    ]
    assert [r.status_code for r in respostas] == [201, 409, 422, 404, 200, 409, 422, 404]
    for r in respostas:
        assert r.headers["cache-control"] == "no-store", r.status_code
        assert r.headers["referrer-policy"] == "no-referrer"


def test_nota_e_token_fora_dos_logs(cliente, ambiente, token, avs, caplog):
    caplog.set_level(logging.DEBUG)
    acao = _post(cliente, token, _corpo(avs, nota=NOTA_SECRETA)).json()
    _patch(cliente, token, acao["id"], {"nota": NOTA_SECRETA + "-2"})
    _post(cliente, token, _corpo(avs, nota=NOTA_SECRETA))  # 409
    _post(cliente, token, _corpo(avs, nota="z" * 501, categoria="embalagem"))  # 422
    do_servidor = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert NOTA_SECRETA not in do_servidor and token not in do_servidor and avs[0][1] not in do_servidor


def test_o_painel_nao_escreve_em_avaliacoes_nem_alertas(cliente, ambiente, token, avs):
    _avaliacao(ambiente, SEGUNDA + timedelta(days=2), criticidade=["saude"])
    with closing(banco.abrir(ambiente)) as conn:
        antes = {t: [tuple(l) for l in conn.execute(f"SELECT * FROM {t}")] for t in ("avaliacoes", "alertas")}
    acao = _post(cliente, token, _corpo(avs, n=3)).json()
    _patch(cliente, token, acao["id"], {"estado": "concluida", "nota": "fim"})
    with closing(banco.abrir(ambiente)) as conn:
        depois = {t: [tuple(l) for l in conn.execute(f"SELECT * FROM {t}")] for t in ("avaliacoes", "alertas")}
    assert antes == depois


def test_resposta_devolve_textos_como_dados(cliente, token, avs):
    nota = "<img src=x onerror=alert(1)>"
    r = _post(cliente, token, _corpo(avs, nota=nota))
    assert r.headers["content-type"].startswith("application/json")
    assert json.loads(r.content)["nota"] == nota


# ---------- reforços (mutações que sobreviveram na primeira rodada) ----------

def test_entregador_e_422_mesmo_com_evidencia_de_entregador(cliente, ambiente, token):
    av = _avaliacao(ambiente, SEGUNDA + timedelta(days=1), categorias=["entregador"])
    r = _post(cliente, token, {"categoria": "entregador", "semana_inicio": SEMANA,
                               "evidencias": [{"avaliacao_id": av, "posicao": 1}]})
    assert r.status_code == 422 and "ranking" in r.json()["detail"]
    assert _linhas(ambiente, "acoes") == []


def test_semana_futura_e_422_mesmo_com_evidencia_daquela_semana(cliente, ambiente, token):
    av = _avaliacao(ambiente, SEGUNDA + timedelta(weeks=2, days=1))  # semana de 09/11, posterior à atual (02/11)
    r = _post(cliente, token, {"categoria": "atraso", "semana_inicio": "2026-11-09",
                               "evidencias": [{"avaliacao_id": av, "posicao": 1}]})
    assert r.status_code == 422 and "posterior" in r.json()["detail"]
    assert _linhas(ambiente, "acoes") == []


# ---------- achados do revisor ----------

def test_id_grande_demais_ou_com_digito_unicode(cliente, token, acao):
    assert _patch(cliente, token, "99999999999999999999", {"estado": "concluida"}).json() == NAO_ENCONTRADA
    for acao_id in ("%D9%A1", " 1", "+1", "1_0"):  # "١" (dígito árabe), espaço, sinal, sublinhado
        assert _patch(cliente, token, acao_id, {"estado": "concluida"}).status_code == 422, acao_id


def test_avaliacao_id_grande_demais_e_422(cliente, ambiente, token):
    r = _post(cliente, token, {"categoria": "atraso", "semana_inicio": SEMANA,
                               "evidencias": [{"avaliacao_id": 10 ** 20, "posicao": 1}]})
    assert r.status_code == 422
    assert _linhas(ambiente, "acoes") == []


def test_semana_atual_vista_pelo_fuso_de_sao_paulo(cliente, ambiente, token, relogio):
    # domingo 01/11 às 23h em São Paulo = segunda 02/11 02h UTC: a semana atual ainda é a de 26/10
    relogio.instante = RESUMO_DA_SEGUNDA.replace(day=1, hour=23)
    av = _avaliacao(ambiente, SEGUNDA + timedelta(weeks=1, hours=12))  # 02/11, semana de 02/11
    r = _post(cliente, token, {"categoria": "atraso", "semana_inicio": "2026-11-02",
                               "evidencias": [{"avaliacao_id": av, "posicao": 1}]})
    assert r.status_code == 422 and "posterior" in r.json()["detail"]


def test_evidencia_na_borda_da_semana_pelo_fuso_de_sao_paulo(cliente, ambiente, token):
    from datetime import datetime, timezone

    # domingo 01/11 23:30 em São Paulo = segunda 02/11 02:30 UTC: ainda é da semana de 26/10
    domingo_tarde = _avaliacao(ambiente, datetime(2026, 11, 2, 2, 30, tzinfo=timezone.utc))
    # segunda 02/11 00:30 em São Paulo = 03:30 UTC: já é da semana de 02/11
    segunda_cedo = _avaliacao(ambiente, datetime(2026, 11, 2, 3, 30, tzinfo=timezone.utc))
    ok = _post(cliente, token, {"categoria": "atraso", "semana_inicio": SEMANA,
                                "evidencias": [{"avaliacao_id": domingo_tarde, "posicao": 1}]})
    assert ok.status_code == 201
    ruim = _post(cliente, token, {"categoria": "embalagem", "semana_inicio": SEMANA,
                                  "evidencias": [{"avaliacao_id": segunda_cedo, "posicao": 1}]})
    assert ruim.status_code == 422


def test_422_nao_ecoa_o_valor_enviado(cliente, token, avs):
    r = _post(cliente, token, _corpo(avs, nota=NOTA_SECRETA + "z" * 501))
    assert r.status_code == 422
    assert NOTA_SECRETA not in r.text and "input" not in r.text


def test_500_durante_o_post_com_nota_nao_vaza_nota_nem_token_e_tem_no_store(cliente, ambiente, token, avs, caplog,
                                                                            monkeypatch):
    caplog.set_level(logging.DEBUG)

    def explode(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(banco, "registrar_acao", explode)
    r = _post(cliente, token, _corpo(avs, nota=NOTA_SECRETA))
    assert r.status_code == 500 and r.json() == {"detail": "falha interna"}
    assert r.headers["cache-control"] == "no-store"
    do_servidor = "\n".join(x.getMessage() for x in caplog.records if not x.name.startswith("httpx"))
    assert any("OperationalError" in m for m in do_servidor.splitlines())
    assert NOTA_SECRETA not in do_servidor and token not in do_servidor and "database is locked" not in do_servidor


def test_500_durante_o_patch_tem_no_store(cliente, token, acao, monkeypatch):
    def explode(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(banco, "mudar_acao", explode)
    r = _patch(cliente, token, acao["id"], {"nota": NOTA_SECRETA})
    assert r.status_code == 500 and r.headers["cache-control"] == "no-store"


def test_erro_interno_no_patch_nao_vira_404(cliente, token, acao, monkeypatch):
    def bug(*a, **k):
        raise KeyError("estado inesperado")  # um bug, não "ação não encontrada"

    monkeypatch.setattr(banco, "mudar_acao", bug)
    assert _patch(cliente, token, acao["id"], {"estado": "concluida"}).status_code == 500
