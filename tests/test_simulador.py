"""Testes do simulador (bloco 2.4).

Fonte da verdade: docs/ESPECIFICACAO.md (POST /avaliacoes e a decisão de 31/10/2026 sobre o
simulador). Nenhum teste faz chamada de rede real: o HTTP usa httpx.MockTransport. A chave abaixo
é fictícia e existe só nestes testes.
"""

import json
import logging
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import simulador  # noqa: E402

FUSO = ZoneInfo("America/Sao_Paulo")
CHAVE = "chave-ficticia-do-simulador"
SEGUNDA = date(2026, 10, 5)  # segunda-feira
CHAVES_DO_CORPO = {"id_externo", "texto", "nota", "recebida_em"}
PRIMEIRA_COM_SEMENTE_42 = ("ger-025", "2026-10-05T11:55:07-03:00")
CAMPOS_DO_GABARITO = ("categorias", "criticidade", "justificativa", "conjunto", "fora_do_escopo")


@pytest.fixture(scope="module")
def avaliacoes():
    return simulador.carregar_avaliacoes()


@pytest.fixture
def sem_espera():
    esperas = []
    return esperas, esperas.append


def _corpos(n=3):
    return [
        {"id_externo": f"x-{i}", "texto": f"texto secreto numero {i}", "nota": 1,
         "recebida_em": f"2026-10-0{i + 1}T12:00:00-03:00"}
        for i in range(n)
    ]


def _transporte(respostas, vistos=None):
    """Responde, por id_externo, com a próxima resposta da fila (status, json) ou levanta a exceção."""
    filas = {k: list(v) for k, v in respostas.items()}

    def tratar(request):
        corpo = json.loads(request.content)
        if vistos is not None:
            vistos.append((corpo, dict(request.headers)))
        item = filas[corpo["id_externo"]].pop(0)
        if isinstance(item, Exception):
            raise item
        status, dados = item
        return httpx.Response(status, json=dados)

    return httpx.MockTransport(tratar)


def _enviar(corpos, respostas, esperas, vistos=None, intervalo=2):
    return simulador.enviar(
        corpos, url="http://servidor.teste", chave=CHAVE, intervalo=intervalo,
        transporte=_transporte(respostas, vistos), dormir=esperas.append,
    )


# ---------- dados ----------

def test_carrega_as_85_avaliacoes_sem_gabarito(avaliacoes):
    assert len(avaliacoes) == 85
    assert len({a["id_externo"] for a in avaliacoes}) == 85
    for a in avaliacoes:
        assert set(a) == {"id_externo", "texto", "nota"}


# ---------- corpo e recebida_em ----------

def test_corpo_tem_exatamente_as_4_chaves(avaliacoes):
    cronograma = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    assert len(cronograma) == 85
    for corpo in cronograma:
        assert set(corpo) == CHAVES_DO_CORPO


def test_nenhum_campo_do_gabarito_vai_no_corpo_enviado(avaliacoes):
    vistos = []
    cronograma = simulador.montar_cronograma(avaliacoes[:3], semente=1, semanas=1, semana_inicio=SEGUNDA)
    respostas = {c["id_externo"]: [(201, {"avaliacao_id": 1, "criada": True, "alerta_id": None})]
                 for c in cronograma}
    _enviar(cronograma, respostas, [], vistos)
    assert len(vistos) == 3
    for corpo, _ in vistos:
        assert set(corpo) == CHAVES_DO_CORPO
        for campo in CAMPOS_DO_GABARITO:
            assert campo not in corpo


def test_recebida_em_tem_fuso_de_sao_paulo_e_fica_dentro_da_semana(avaliacoes):
    cronograma = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    inicio = datetime.combine(SEGUNDA, datetime.min.time(), FUSO)
    for corpo in cronograma:
        instante = datetime.fromisoformat(corpo["recebida_em"])
        assert instante.utcoffset() == timedelta(hours=-3)
        assert instante.tzinfo is not None
        assert inicio <= instante < inicio + timedelta(weeks=4)
        assert 11 <= instante.hour <= 22  # 11:00 a 22:59


def test_pelo_menos_20_por_semana_e_tamanhos_21_21_21_22(avaliacoes):
    cronograma = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    por_semana = Counter(
        (datetime.fromisoformat(c["recebida_em"]).date() - SEGUNDA).days // 7 for c in cronograma
    )
    assert sorted(por_semana) == [0, 1, 2, 3]
    assert all(n >= 20 for n in por_semana.values())
    assert [por_semana[i] for i in range(4)] == [21, 21, 21, 22]


def test_cada_avaliacao_aparece_uma_vez(avaliacoes):
    cronograma = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    assert sorted(c["id_externo"] for c in cronograma) == sorted(a["id_externo"] for a in avaliacoes)


def test_ordenado_por_recebida_em(avaliacoes):
    cronograma = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    instantes = [datetime.fromisoformat(c["recebida_em"]) for c in cronograma]
    assert instantes == sorted(instantes)


def test_mesma_semente_gera_o_mesmo_resultado(avaliacoes):
    a = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    b = simulador.montar_cronograma(list(avaliacoes), semente=42, semanas=4, semana_inicio=SEGUNDA)
    assert a == b


def test_ordem_de_entrada_nao_muda_o_resultado(avaliacoes):
    a = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    b = simulador.montar_cronograma(list(reversed(avaliacoes)), semente=42, semanas=4, semana_inicio=SEGUNDA)
    assert a == b


def test_semente_diferente_gera_resultado_diferente(avaliacoes):
    a = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    b = simulador.montar_cronograma(avaliacoes, semente=43, semanas=4, semana_inicio=SEGUNDA)
    assert a != b


def test_semente_padrao_e_42_e_semanas_padrao_e_4():
    args = simulador.ler_argumentos(["--dry-run"])
    assert args.semente == 42
    assert args.semanas == 4
    assert args.url == "http://127.0.0.1:8000"
    assert args.intervalo == 2


# ---------- âncora ----------

def test_ancora_que_nao_e_segunda_e_recusada():
    with pytest.raises(ValueError, match="segunda"):
        simulador.validar_segunda("2026-10-06")  # terça-feira


def test_ancora_segunda_e_aceita():
    assert simulador.validar_segunda("2026-10-05") == date(2026, 10, 5)


@pytest.mark.parametrize("texto", ["05/10/2026", "2026-13-01", "abc", ""])
def test_ancora_com_formato_invalido_e_recusada(texto):
    with pytest.raises(ValueError):
        simulador.validar_segunda(texto)


def test_main_recusa_ancora_que_nao_e_segunda_com_mensagem_clara(capsys):
    codigo = simulador.main(["--dry-run", "--semana-inicio", "2026-10-06"])
    saida = capsys.readouterr()
    assert codigo != 0
    assert "segunda" in (saida.err + saida.out)


def test_ancora_padrao_e_a_segunda_que_abre_as_4_semanas_completas_anteriores():
    # sábado 31/10/2026: a semana atual abre em 26/10; 4 semanas antes = 28/09
    assert simulador.semana_inicio_padrao(date(2026, 10, 31), 4) == date(2026, 9, 28)
    # na própria segunda, a semana atual é a que começa nela
    assert simulador.semana_inicio_padrao(date(2026, 10, 26), 4) == date(2026, 9, 28)
    # domingo ainda pertence à semana que abriu em 26/10
    assert simulador.semana_inicio_padrao(date(2026, 11, 1), 4) == date(2026, 9, 28)
    assert simulador.semana_inicio_padrao(date(2026, 11, 2), 4) == date(2026, 10, 5)
    assert simulador.semana_inicio_padrao(date(2026, 10, 31), 4).weekday() == 0


def test_semanas_invalidas_sao_recusadas(avaliacoes):
    with pytest.raises(ValueError):
        simulador.montar_cronograma(avaliacoes, semente=42, semanas=0, semana_inicio=SEGUNDA)
    with pytest.raises(ValueError):
        simulador.montar_cronograma(avaliacoes[:3], semente=42, semanas=4, semana_inicio=SEGUNDA)


# ---------- envio ----------

def test_chave_vai_no_cabecalho_e_corpo_exato_vai_no_post(sem_espera):
    esperas, _ = sem_espera
    corpos = _corpos(1)
    vistos = []
    resumo = _enviar(corpos, {"x-0": [(201, {"avaliacao_id": 1, "criada": True, "alerta_id": None})]},
                     esperas, vistos)
    corpo, cabecalhos = vistos[0]
    assert corpo == corpos[0]
    assert cabecalhos["x-api-key"] == CHAVE
    assert resumo.total == 1 and resumo.criadas == 1


def test_201_e_200_contam_e_alerta_id_nao_nulo_conta_alerta(sem_espera):
    esperas, _ = sem_espera
    corpos = _corpos(3)
    resumo = _enviar(corpos, {
        "x-0": [(201, {"avaliacao_id": 1, "criada": True, "alerta_id": None})],
        "x-1": [(201, {"avaliacao_id": 2, "criada": True, "alerta_id": 7})],
        "x-2": [(200, {"avaliacao_id": 3, "criada": False})],
    }, esperas)
    assert (resumo.total, resumo.criadas, resumo.duplicatas, resumo.com_alerta) == (3, 2, 1, 1)
    assert resumo.falhas == [] and resumo.reenvios == 0


def test_200_conta_duplicata_e_nao_conta_alerta(sem_espera):
    esperas, _ = sem_espera
    resumo = _enviar(_corpos(1), {"x-0": [(200, {"avaliacao_id": 3, "criada": False, "alerta_id": 9})]},
                     esperas)
    assert resumo.duplicatas == 1 and resumo.criadas == 0 and resumo.com_alerta == 0


def test_503_reenvia_a_mesma_avaliacao_e_depois_da_certo(sem_espera):
    esperas, _ = sem_espera
    vistos = []
    corpos = _corpos(1)
    resumo = _enviar(corpos, {"x-0": [
        (503, {"detail": "x"}), (503, {"detail": "x"}),
        (201, {"avaliacao_id": 1, "criada": True, "alerta_id": None}),
    ]}, esperas, vistos)
    assert resumo.criadas == 1 and resumo.reenvios == 2 and resumo.falhas == []
    assert [c for c, _ in vistos] == [corpos[0]] * 3  # sempre a MESMA avaliação
    assert 5 in esperas and 15 in esperas


def test_503_esgotado_registra_o_id_e_continua(sem_espera):
    esperas, _ = sem_espera
    resumo = _enviar(_corpos(2), {
        "x-0": [(503, {})] * 3,
        "x-1": [(201, {"avaliacao_id": 2, "criada": True, "alerta_id": None})],
    }, esperas)
    assert resumo.falhas == ["x-0"]
    assert resumo.falhas_finais == 1
    assert resumo.criadas == 1
    assert resumo.reenvios == 2
    assert esperas.count(5) == 1 and esperas.count(15) == 1


def test_erro_de_rede_e_timeout_tambem_reenviam(sem_espera):
    esperas, _ = sem_espera
    resumo = _enviar(_corpos(2), {
        "x-0": [httpx.ConnectError("sem conexao"), httpx.ReadTimeout("lento"),
                (201, {"avaliacao_id": 1, "criada": True, "alerta_id": None})],
        "x-1": [httpx.ConnectError("sem conexao")] * 3,
    }, esperas)
    assert resumo.criadas == 1
    assert resumo.falhas == ["x-1"]
    assert resumo.reenvios == 4


def test_401_para_tudo_na_hora(sem_espera):
    esperas, _ = sem_espera
    vistos = []
    resumo = _enviar(_corpos(3), {
        "x-0": [(201, {"avaliacao_id": 1, "criada": True, "alerta_id": None})],
        "x-1": [(401, {"detail": "não autorizado"})],
        "x-2": [(201, {"avaliacao_id": 3, "criada": True, "alerta_id": None})],
    }, esperas, vistos)
    assert resumo.interrompido_por_401 is True
    assert [c["id_externo"] for c, _ in vistos] == ["x-0", "x-1"]  # x-2 nunca foi enviada
    assert resumo.reenvios == 0


def test_422_e_500_registram_e_seguem_sem_reenviar(sem_espera):
    esperas, _ = sem_espera
    vistos = []
    resumo = _enviar(_corpos(3), {
        "x-0": [(422, {"detail": []})],
        "x-1": [(500, {"detail": "falha interna"})],
        "x-2": [(201, {"avaliacao_id": 3, "criada": True, "alerta_id": None})],
    }, esperas, vistos)
    assert resumo.erros_422 == 1 and resumo.erros_500 == 1 and resumo.criadas == 1
    assert resumo.reenvios == 0
    assert [c["id_externo"] for c, _ in vistos] == ["x-0", "x-1", "x-2"]
    assert resumo.falhas == ["x-0", "x-1"]


def test_intervalo_entre_envios_e_respeitado(sem_espera):
    esperas, _ = sem_espera
    resp = (201, {"avaliacao_id": 1, "criada": True, "alerta_id": None})
    _enviar(_corpos(3), {"x-0": [resp], "x-1": [resp], "x-2": [resp]}, esperas, intervalo=2)
    assert esperas == [2, 2]  # entre envios, não depois do último


def test_timeout_do_cliente_e_60_segundos():
    assert simulador.TIMEOUT_SEGUNDOS == 60


# ---------- saída: sem chave e sem texto ----------

def test_chave_e_texto_nao_aparecem_na_saida_nem_nos_logs(capsys, caplog, sem_espera):
    esperas, _ = sem_espera
    caplog.set_level(logging.DEBUG)
    corpos = _corpos(3)
    resumo = _enviar(corpos, {
        "x-0": [(201, {"avaliacao_id": 1, "criada": True, "alerta_id": 4})],
        "x-1": [(401, {"detail": "não autorizado"})],
        "x-2": [(201, {"avaliacao_id": 3, "criada": True, "alerta_id": None})],
    }, esperas)
    simulador.imprimir_resumo(resumo)
    saida = capsys.readouterr()
    todo = saida.out + saida.err + caplog.text
    assert CHAVE not in todo
    for c in corpos:
        assert c["texto"] not in todo
    assert "x-1" in todo or "401" in todo


def test_resumo_traz_todos_os_campos_pedidos(capsys, sem_espera):
    esperas, _ = sem_espera
    resp = (201, {"avaliacao_id": 1, "criada": True, "alerta_id": 4})
    resumo = _enviar(_corpos(2), {"x-0": [resp], "x-1": [(422, {})]}, esperas)
    simulador.imprimir_resumo(resumo)
    saida = capsys.readouterr().out.lower()
    for rotulo in ("total", "criadas", "duplicatas", "alerta", "reenvios", "falhas finais", "422", "500", "x-1"):
        assert rotulo in saida


# ---------- dry-run ----------

def test_dry_run_nao_envia_nada_e_nao_imprime_chave_nem_texto(avaliacoes, capsys, monkeypatch):
    monkeypatch.setenv("RADAR_API_KEY", CHAVE)

    def proibido(*a, **k):
        raise AssertionError("o dry-run não pode enviar nada")

    monkeypatch.setattr(simulador, "enviar", proibido)
    monkeypatch.setattr(httpx.Client, "send", proibido)
    codigo = simulador.main(["--dry-run", "--semana-inicio", "2026-10-05"])
    saida = capsys.readouterr().out
    assert codigo == 0
    assert "2026-10-05" in saida
    import re
    tamanhos = re.findall(r"semana \d \(\d{4}-\d\d-\d\d\): (\d+) avalia", saida)
    assert tamanhos == ["21", "21", "21", "22"]
    for a in avaliacoes:
        assert a["texto"] not in saida
    for chave in CHAVES_DO_CORPO:
        assert chave in saida  # exemplo de corpo


def test_dry_run_funciona_sem_chave(monkeypatch, capsys):
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    assert simulador.main(["--dry-run", "--semana-inicio", "2026-10-05"]) == 0


# ---------- chave e main ----------

def test_sem_chave_o_envio_real_recusa_antes_de_enviar(monkeypatch, capsys):
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    monkeypatch.setattr(simulador, "load_dotenv", lambda *a, **k: False)
    codigo = simulador.main(["--semana-inicio", "2026-10-05"])
    saida = capsys.readouterr()
    assert codigo != 0
    assert "RADAR_API_KEY" in saida.err + saida.out


def test_main_envia_com_a_chave_do_ambiente(monkeypatch, capsys):
    monkeypatch.setenv("RADAR_API_KEY", CHAVE)
    monkeypatch.setattr(simulador, "load_dotenv", lambda *a, **k: False)
    vistos = []

    def tratar(request):
        vistos.append((json.loads(request.content), request.headers["x-api-key"]))
        return httpx.Response(201, json={"avaliacao_id": len(vistos), "criada": True, "alerta_id": None})

    monkeypatch.setattr(simulador.time, "sleep", lambda s: None)
    codigo = simulador.main(["--semana-inicio", "2026-10-05"], transporte=httpx.MockTransport(tratar))
    assert codigo == 0
    assert len(vistos) == 85
    assert {k for _, k in vistos} == {CHAVE}
    assert CHAVE not in capsys.readouterr().out


# ---------- achados do revisor ----------

def test_valor_fixo_da_semente_42(avaliacoes):
    c = simulador.montar_cronograma(avaliacoes, semente=42, semanas=4, semana_inicio=SEGUNDA)
    assert (c[0]["id_externo"], c[0]["recebida_em"]) == PRIMEIRA_COM_SEMENTE_42


def test_ordem_das_esperas_do_503_e_5_depois_15(sem_espera):
    esperas, _ = sem_espera
    _enviar(_corpos(1), {"x-0": [(503, {})] * 3}, esperas, intervalo=0)
    assert esperas == [5, 15]


def test_erro_de_transporte_que_nao_e_de_rede_nao_reenvia(sem_espera):
    esperas, _ = sem_espera
    resumo = _enviar(_corpos(2), {
        "x-0": [httpx.UnsupportedProtocol("esquema errado")],
        "x-1": [httpx.LocalProtocolError("cabecalho invalido")],
    }, esperas, intervalo=0)
    assert resumo.reenvios == 0 and esperas == []
    assert resumo.falhas == ["x-0", "x-1"]


def test_status_inesperado_e_contado_a_parte_e_listado(sem_espera):
    esperas, _ = sem_espera
    resumo = _enviar(_corpos(1), {"x-0": [(404, {})]}, esperas)
    assert resumo.outros == 1 and resumo.falhas == ["x-0"] and resumo.reenvios == 0


def test_201_com_corpo_que_nao_e_json_conta_criada_sem_alerta(sem_espera):
    esperas, _ = sem_espera
    transporte = httpx.MockTransport(lambda r: httpx.Response(201, content=b"nao e json"))
    resumo = simulador.enviar(_corpos(1), chave=CHAVE, intervalo=0, transporte=transporte,
                              dormir=esperas.append)
    assert resumo.criadas == 1 and resumo.com_alerta == 0


def test_main_com_401_sai_com_codigo_3_e_avisa(monkeypatch, capsys):
    monkeypatch.setenv("RADAR_API_KEY", CHAVE)
    monkeypatch.setattr(simulador, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(simulador.time, "sleep", lambda s: None)
    transporte = httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "x"}))
    codigo = simulador.main(["--semana-inicio", "2026-10-05"], transporte=transporte)
    saida = capsys.readouterr().out
    assert codigo == 3 and "PARADO no 401" in saida and CHAVE not in saida


def test_intervalo_negativo_e_recusado(capsys):
    assert simulador.main(["--dry-run", "--intervalo", "-1"]) == 2
    assert "intervalo" in capsys.readouterr().err


def test_arquivo_de_dados_ausente_da_erro_curto(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(simulador, "PASTA_DADOS", tmp_path)
    assert simulador.main(["--dry-run"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_chave_do_env_e_aparada(monkeypatch):
    monkeypatch.setenv("RADAR_API_KEY", "  " + CHAVE + "\n")
    monkeypatch.setattr(simulador, "load_dotenv", lambda *a, **k: False)
    vistos = []

    def tratar(request):
        vistos.append(request.headers["x-api-key"])
        return httpx.Response(201, json={"avaliacao_id": 1, "criada": True, "alerta_id": None})

    monkeypatch.setattr(simulador.time, "sleep", lambda s: None)
    simulador.main(["--semana-inicio", "2026-10-05"], transporte=httpx.MockTransport(tratar))
    assert set(vistos) == {CHAVE}
