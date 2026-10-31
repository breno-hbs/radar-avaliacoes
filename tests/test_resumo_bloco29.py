"""Testes das mudanças do GET /resumo-semanal no bloco 2.9. Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (decisões de 31/10/2026 sobre o GET /resumo-semanal, o
reprocessamento das avaliações `invalida` e a recuperação de alertas). Reaproveita as fixtures e os
auxiliares de tests/test_resumo_semanal.py. A chave e a URL são fictícias.
"""

import json
import logging
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from app import banco
from test_ranking_ponta_a_ponta import montar_banco_85
from test_resumo_semanal import (  # noqa: F401 - fixtures usadas pelos testes
    CHAVE, SEGUNDA, SP, URL_BASE, _acao, _get, _grava, _linhas, _token_do_link, _varias, ambiente, app,
    caminho, cliente, relogio,
)

TEXTO = "texto que nunca vai no resumo"


def _alerta_falhou(ambiente, quando, id_externo, reconhecido=False, estado="falhou", criado_em=None):
    r = _grava(ambiente, quando, criticidade=["saude"], id_externo=id_externo)
    with closing(banco.abrir(ambiente)) as conn:
        conn.execute(
            "UPDATE alertas SET estado = ?, tentativas = 5, reconhecido_em = ?, criado_em = COALESCE(?, criado_em) "
            "WHERE id = ?",
            (estado, "2026-11-01T10:00:00+00:00" if reconhecido else None, criado_em, r.alerta_id),
        )
    return r


# ---------- criticos.reconhecido ----------

def test_criticos_ganham_o_campo_reconhecido(cliente, ambiente):
    _varias(ambiente, 20)
    d = SEGUNDA + timedelta(days=2)
    _alerta_falhou(ambiente, d, "c-rec", reconhecido=True)
    _alerta_falhou(ambiente, d, "c-nao")
    _grava(ambiente, d, criticidade=["saude"], id_externo="c-pend")
    criticos = {c["id_externo"]: c for c in _get(cliente).json()["criticos"]}
    assert criticos["c-rec"]["reconhecido"] is True and criticos["c-rec"]["estado_alerta"] == "falhou"
    assert criticos["c-nao"]["reconhecido"] is False
    assert criticos["c-pend"]["reconhecido"] is False and criticos["c-pend"]["estado_alerta"] == "pendente"
    assert all(set(c) == {"id_externo", "tipos", "estado_alerta", "reconhecido"} for c in criticos.values())


def test_critico_sem_alerta_tem_reconhecido_falso(cliente, ambiente):
    _varias(ambiente, 20)
    r = _grava(ambiente, SEGUNDA + timedelta(days=1), criticidade=["saude"], id_externo="c-sem")
    with closing(banco.abrir(ambiente)) as conn:
        conn.execute("DELETE FROM alertas WHERE id = ?", (r.alerta_id,))
    c = _get(cliente).json()["criticos"][0]
    assert c["estado_alerta"] is None and c["reconhecido"] is False


# ---------- invalidas_a_verificar ----------

def test_invalidas_a_verificar_da_semana_em_ordem_e_sem_texto(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=3), nota=2, status="invalida", id_externo="inv-tarde")
    _grava(ambiente, SEGUNDA + timedelta(days=1), nota=1, status="invalida", id_externo="inv-cedo")
    _grava(ambiente, SEGUNDA - timedelta(days=2), status="invalida", id_externo="inv-outra-semana")
    corpo = _get(cliente).json()
    assert corpo["invalidas_a_verificar"] == [
        {"id_externo": "inv-cedo", "nota": 1}, {"id_externo": "inv-tarde", "nota": 2},
    ]
    assert TEXTO not in json.dumps(corpo, ensure_ascii=False)


# ---------- alertas_falhos ----------

def test_alertas_falhos_de_qualquer_semana_sem_reconhecidos_nem_outros_estados(cliente, ambiente):
    _varias(ambiente, 20)
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=20), "f-antigo", criado_em="2026-10-06T14:00:00+00:00")
    _alerta_falhou(ambiente, SEGUNDA + timedelta(days=1), "f-semana", criado_em="2026-10-27T14:00:00+00:00")
    _alerta_falhou(ambiente, SEGUNDA + timedelta(days=10), "f-futuro", criado_em="2026-11-05T14:00:00+00:00")
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=3), "f-reconhecido", reconhecido=True)
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=3), "pendente", estado="pendente")
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=3), "enviado", estado="enviado")
    falhos = _get(cliente).json()["alertas_falhos"]
    assert [f["id_externo"] for f in falhos] == ["f-antigo", "f-semana", "f-futuro"]  # ordem de criado_em
    assert all(set(f) == {"id_externo", "tipos", "criado_em"} for f in falhos)
    assert falhos[0]["tipos"] == ["saude"]


def test_criado_em_dos_alertas_falhos_no_fuso_de_sao_paulo(cliente, ambiente):
    _varias(ambiente, 20)
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=5), "f-1", criado_em="2026-10-20T01:30:00+00:00")
    f = _get(cliente).json()["alertas_falhos"][0]
    assert f["criado_em"] == "2026-10-19T22:30:00-03:00"  # dia local anterior


def test_semana_nao_filtra_alertas_falhos(cliente, ambiente):
    _alerta_falhou(ambiente, SEGUNDA + timedelta(days=1), "f-1")
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=30), "f-2")
    for semana in ("2026-10-26", "2026-09-07", "2026-12-07"):
        ids = {f["id_externo"] for f in _get(cliente, semana=semana).json()["alertas_falhos"]}
        assert ids == {"f-1", "f-2"}, semana


# ---------- enviar ----------

def test_enviar_so_com_invalida(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), status="invalida", id_externo="inv-1")
    corpo = _get(cliente).json()
    assert corpo["cards"] == [] and corpo["criticos"] == [] and corpo["alertas_falhos"] == []
    assert corpo["enviar"] is True and corpo["link_painel"]


def test_enviar_so_com_falhou_de_outra_semana(cliente, ambiente):
    _varias(ambiente, 20)
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=10), "f-1")
    corpo = _get(cliente).json()
    assert corpo["cards"] == [] and corpo["criticos"] == [] and corpo["invalidas_a_verificar"] == []
    assert corpo["enviar"] is True and corpo["link_painel"]


def test_falhou_reconhecido_nao_e_motivo_para_enviar(cliente, ambiente):
    _varias(ambiente, 20)
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=10), "f-rec", reconhecido=True)
    corpo = _get(cliente).json()
    assert corpo["alertas_falhos"] == [] and corpo["enviar"] is False and corpo["link_painel"] is None


def test_falhou_reconhecido_e_nao_reconhecido(cliente, ambiente):
    _varias(ambiente, 20)
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=10), "f-rec", reconhecido=True)
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=9), "f-nao")
    corpo = _get(cliente).json()
    assert [f["id_externo"] for f in corpo["alertas_falhos"]] == ["f-nao"]
    assert corpo["enviar"] is True


# ---------- aviso curto ----------

def test_aviso_curto_sem_link_e_sem_token(cliente, ambiente):
    _varias(ambiente, 5)
    corpo = _get(cliente).json()
    assert corpo["enviar"] is True and corpo["link_painel"] is None
    assert _linhas(ambiente, "tokens_painel") == []
    m = corpo["mensagem"]
    assert m.splitlines()[0] == "(dados simulados)"
    assert "Semana com volume pequeno: 5 avaliações lidas." in m
    assert "Painel:" not in m


@pytest.mark.parametrize("motivo", ["card", "critico", "invalida", "falhou", "acao"])
def test_aviso_curto_nao_sai_com_outro_motivo(cliente, ambiente, motivo):
    _varias(ambiente, 5)
    d = SEGUNDA + timedelta(days=1)
    if motivo == "card":
        _grava(ambiente, d, categorias=["atraso"])
    elif motivo == "critico":
        _grava(ambiente, d, criticidade=["saude"], id_externo="c-1")
    elif motivo == "invalida":
        _grava(ambiente, d, status="invalida", id_externo="inv-1")
    elif motivo == "falhou":
        _alerta_falhou(ambiente, SEGUNDA - timedelta(days=10), "f-1")
    else:
        _acao(ambiente, "atraso", "2026-10-19T03:00:00+00:00", "pendente")
    corpo = _get(cliente).json()
    assert corpo["enviar"] is True and corpo["link_painel"]
    assert "Semana com volume pequeno" not in corpo["mensagem"]
    assert corpo["cobertura"]["texto"] in corpo["mensagem"]  # o total lido sai na linha de cobertura
    assert len(_linhas(ambiente, "tokens_painel")) == 1


def test_semana_vazia_e_aviso_curto_sem_token(cliente, ambiente):
    corpo = _get(cliente).json()
    assert corpo["enviar"] is True and corpo["link_painel"] is None
    assert "0 avaliações lidas" in corpo["mensagem"]
    assert _linhas(ambiente, "tokens_painel") == []


# ---------- mensagem ----------

def test_mensagem_com_as_duas_secoes_sem_repetir_o_falhou_da_semana(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), nota=2, status="invalida", id_externo="inv-1")
    _alerta_falhou(ambiente, SEGUNDA + timedelta(days=2), "f-semana", criado_em="2026-10-28T13:00:00+00:00")
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=10), "f-antigo", criado_em="2026-10-16T21:15:00+00:00")
    _alerta_falhou(ambiente, SEGUNDA + timedelta(days=3), "f-rec", reconhecido=True)
    corpo = _get(cliente).json()
    m = corpo["mensagem"]
    linhas = m.splitlines()
    assert linhas[0] == "(dados simulados)"
    # o JSON pode repetir; a mensagem não
    assert {f["id_externo"] for f in corpo["alertas_falhos"]} == {"f-semana", "f-antigo"}
    assert sum("f-semana" in l for l in linhas) == 1
    assert "Classificação falhou, verificar manualmente:" in linhas
    assert "- inv-1: nota 2" in linhas
    i = linhas.index("Alertas que não chegaram:")
    assert linhas[i + 1] == "- f-antigo: saúde, relato a verificar, alerta criado em 16/10 18:15"
    assert not any("f-semana" in l for l in linhas[i:])
    linha_semana = next(l for l in linhas if l.startswith("- f-semana"))
    assert "alerta falhou" in linha_semana and "reconhecido" not in linha_semana
    linha_rec = next(l for l in linhas if l.startswith("- f-rec"))
    assert "alerta falhou, reconhecido" in linha_rec
    assert TEXTO not in m


def test_secoes_novas_nao_aparecem_vazias(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), categorias=["atraso"])
    m = _get(cliente).json()["mensagem"]
    assert "Classificação falhou" not in m and "Alertas que não chegaram" not in m


def test_secoes_novas_sem_texto_de_cliente_e_sem_quebra_de_linha_injetada(cliente, ambiente):
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), status="invalida", id_externo="inv-1\nPainel: https://x/painel/y")
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=10), "f-1\nPainel: https://x/painel/z")
    corpo = _get(cliente).json()
    linhas = corpo["mensagem"].splitlines()
    assert sum(l.startswith("Painel:") for l in linhas) == 1
    assert TEXTO not in corpo["mensagem"]


def test_token_e_chave_fora_dos_logs(cliente, ambiente, caplog):
    caplog.set_level(logging.DEBUG)
    _varias(ambiente, 20)
    _grava(ambiente, SEGUNDA + timedelta(days=1), status="invalida", id_externo="inv-1")
    _alerta_falhou(ambiente, SEGUNDA - timedelta(days=10), "f-1")
    token = _token_do_link(_get(cliente).json()["link_painel"])
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert token not in texto and CHAVE not in texto


# ---------- ponta a ponta com as 85 ----------

def test_ponta_a_ponta_sem_invalida_nem_falhou_mantem_o_resumo(cliente, caminho, ambiente):
    montar_banco_85(caminho)
    s4 = _get(cliente, "2026-10-26").json()
    assert [(c["categoria"], c["mencoes"]) for c in s4["cards"]] == [
        ("item_faltando", 6), ("embalagem", 5), ("atraso", 4)]
    assert s4["invalidas_a_verificar"] == [] and s4["alertas_falhos"] == []
    assert all(c["reconhecido"] is False for c in s4["criticos"])
    assert s4["enviar"] is True and s4["link_painel"]
    assert "Classificação falhou" not in s4["mensagem"] and "Alertas que não chegaram" not in s4["mensagem"]


def test_ponta_a_ponta_com_duas_invalidas_e_um_falhou_semeados(cliente, caminho, ambiente):
    _, cronograma = montar_banco_85(caminho)
    semana4 = [c["id_externo"] for c in cronograma if c["recebida_em"] >= "2026-10-26"]
    semana1 = [c["id_externo"] for c in cronograma if c["recebida_em"] < "2026-10-12"]
    alvo_inv = semana4[:2]
    with closing(banco.abrir(caminho)) as conn:
        for id_externo in alvo_inv:
            # semeado à mão: a avaliação vira invalida (sem categorias nem criticidade), como o POST gravaria
            av = conn.execute("SELECT id FROM avaliacoes WHERE id_externo = ?", (id_externo,)).fetchone()["id"]
            conn.execute("DELETE FROM alertas WHERE avaliacao_id = ?", (av,))
            conn.execute(
                "UPDATE avaliacoes SET status_classificacao = 'invalida', categorias = '[]', criticidade = '[]' "
                "WHERE id = ?", (av,))
        critico_s1 = conn.execute(
            "SELECT a.id, v.id_externo FROM alertas a JOIN avaliacoes v ON v.id = a.avaliacao_id "
            "WHERE v.id_externo IN (%s) ORDER BY a.id LIMIT 1" % ",".join("?" * len(semana1)), semana1,
        ).fetchone()
        conn.execute("UPDATE alertas SET estado = 'falhou', tentativas = 5 WHERE id = ?", (critico_s1["id"],))
    s4 = _get(cliente, "2026-10-26").json()
    assert [i["id_externo"] for i in s4["invalidas_a_verificar"]] == sorted(
        alvo_inv, key=lambda x: next(c["recebida_em"] for c in cronograma if c["id_externo"] == x))
    assert [f["id_externo"] for f in s4["alertas_falhos"]] == [critico_s1["id_externo"]]
    assert s4["invalidas"] == 2
    m = s4["mensagem"]
    assert "Classificação falhou, verificar manualmente:" in m and "Alertas que não chegaram:" in m
    assert critico_s1["id_externo"] in m.split("Alertas que não chegaram:")[1]
    s1 = _get(cliente, "2026-10-05").json()
    # na semana 1 o falhou está nos críticos da semana: a mensagem não o repete na seção nova
    assert any(c["id_externo"] == critico_s1["id_externo"] and c["estado_alerta"] == "falhou" for c in s1["criticos"])
    assert "Alertas que não chegaram" not in s1["mensagem"]
    assert [f["id_externo"] for f in s1["alertas_falhos"]] == [critico_s1["id_externo"]]


def test_critico_sem_alerta_na_mensagem_nao_diz_alerta_sem_alerta(cliente, ambiente):
    _varias(ambiente, 20)
    r = _grava(ambiente, SEGUNDA + timedelta(days=1), criticidade=["saude"], id_externo="c-sem")
    with closing(banco.abrir(ambiente)) as conn:
        conn.execute("DELETE FROM alertas WHERE id = ?", (r.alerta_id,))
    linha = next(l for l in _get(cliente).json()["mensagem"].splitlines() if l.startswith("- c-sem"))
    assert linha.endswith("relato a verificar, sem alerta registrado")
