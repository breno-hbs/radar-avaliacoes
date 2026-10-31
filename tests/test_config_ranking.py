"""Testes da configuração do ranking (bloco 2.6).

Fonte da verdade: docs/ESPECIFICACAO.md (decisões de 31/10/2026 sobre os pesos e o complemento do
ranking, e o bullet "Configuração").
"""

import copy
import json
from decimal import Decimal
from fractions import Fraction

import pytest

from app import config_ranking

VALIDA = {
    "pesos": {
        "qualidade_temperatura": 3,
        "item_faltando": 3,
        "atraso": 2,
        "embalagem": 2,
        "atendimento": 2,
        "entregador": None,
    },
    "fator_tendencia": 1.25,
    "amostra_minima": 20,
}


def _grava(tmp_path, dados):
    caminho = tmp_path / "ranking.json"
    caminho.write_text(json.dumps(dados) if not isinstance(dados, str) else dados, encoding="utf-8")
    return caminho


def _com(**mudancas):
    dados = copy.deepcopy(VALIDA)
    for chave, valor in mudancas.items():
        dados[chave] = valor
    return dados


def _com_peso(categoria, valor):
    dados = copy.deepcopy(VALIDA)
    dados["pesos"][categoria] = valor
    return dados


def test_arquivo_versionado_tem_os_valores_da_especificacao():
    cfg = config_ranking.carregar_config()
    assert cfg.pesos == {
        "qualidade_temperatura": 3, "item_faltando": 3, "atraso": 2, "embalagem": 2, "atendimento": 2,
    }
    assert cfg.fora_do_ranking == ("entregador",)
    assert cfg.fator_tendencia == Fraction(5, 4)
    assert isinstance(cfg.fator_tendencia, Fraction)
    assert cfg.amostra_minima == 20


def test_fator_e_lido_sem_passar_por_float(tmp_path):
    cfg = config_ranking.carregar_config(_grava(tmp_path, '{"pesos": {"qualidade_temperatura": 3, '
                                                '"item_faltando": 3, "atraso": 2, "embalagem": 2, '
                                                '"atendimento": 2, "entregador": null}, '
                                                '"fator_tendencia": 1.1, "amostra_minima": 20}'))
    assert cfg.fator_tendencia == Fraction(11, 10)  # float(1.1) daria outra fração


def test_config_valida_carrega(tmp_path):
    assert config_ranking.carregar_config(_grava(tmp_path, VALIDA)).amostra_minima == 20


@pytest.mark.parametrize("dados,trecho", [
    ({k: v for k, v in VALIDA.items() if k != "pesos"}, "pesos"),
    ({k: v for k, v in VALIDA.items() if k != "fator_tendencia"}, "fator_tendencia"),
    ({k: v for k, v in VALIDA.items() if k != "amostra_minima"}, "amostra_minima"),
    (_com(extra=1), "extra"),
    (_com(pesos=[3, 3]), "pesos"),
    (_com_peso("qualidade_temperatura", 0), "qualidade_temperatura"),
    (_com_peso("qualidade_temperatura", -1), "qualidade_temperatura"),
    (_com_peso("atraso", 2.5), "atraso"),
    (_com_peso("atraso", 2.0), "atraso"),
    (_com_peso("atraso", "2"), "atraso"),
    (_com_peso("atraso", True), "atraso"),
    (_com_peso("atraso", None), "atraso"),
    (_com_peso("entregador", 1), "entregador"),
    (_com_peso("desconhecida", 2), "desconhecida"),
    ({**VALIDA, "pesos": {k: v for k, v in VALIDA["pesos"].items() if k != "embalagem"}}, "embalagem"),
    (_com(fator_tendencia=1), "fator_tendencia"),
    (_com(fator_tendencia=1.0), "fator_tendencia"),
    (_com(fator_tendencia=0.9), "fator_tendencia"),
    (_com(fator_tendencia="1.25"), "fator_tendencia"),
    (_com(fator_tendencia=True), "fator_tendencia"),
    (_com(fator_tendencia=None), "fator_tendencia"),
    (_com(amostra_minima=0), "amostra_minima"),
    (_com(amostra_minima=-5), "amostra_minima"),
    (_com(amostra_minima=20.0), "amostra_minima"),
    (_com(amostra_minima="20"), "amostra_minima"),
    (_com(amostra_minima=True), "amostra_minima"),
])
def test_cada_erro_de_configuracao_e_recusado_com_mensagem_curta(tmp_path, dados, trecho):
    with pytest.raises(config_ranking.ErroNaConfiguracao) as erro:
        config_ranking.carregar_config(_grava(tmp_path, dados))
    mensagem = str(erro.value)
    assert trecho in mensagem
    assert len(mensagem) < 200 and "\n" not in mensagem


def test_json_invalido_e_raiz_que_nao_e_objeto(tmp_path):
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="JSON"):
        config_ranking.carregar_config(_grava(tmp_path, "{nao e json"))
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="objeto"):
        config_ranking.carregar_config(_grava(tmp_path, "[1, 2]"))


def test_arquivo_ausente(tmp_path):
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="não encontrado"):
        config_ranking.carregar_config(tmp_path / "nao_existe.json")


def test_amostra_minima_1_e_aceita(tmp_path):
    assert config_ranking.carregar_config(_grava(tmp_path, _com(amostra_minima=1))).amostra_minima == 1


def test_config_e_imutavel():
    cfg = config_ranking.carregar_config()
    with pytest.raises(Exception):
        cfg.amostra_minima = 1
    with pytest.raises(TypeError):
        cfg.pesos["atraso"] = 9


@pytest.mark.parametrize("texto", [
    '"fator_tendencia": 1e999999999',  # expoente enorme: não pode travar montando o inteiro
    '"fator_tendencia": NaN',
    '"fator_tendencia": Infinity',
    '"amostra_minima": 1' + "0" * 5000,  # inteiro acima do limite de dígitos do Python
])
def test_valores_numericos_patologicos_sao_recusados_com_erro_da_config(tmp_path, texto):
    base = json.dumps({k: v for k, v in VALIDA.items() if k not in ("fator_tendencia", "amostra_minima")})
    outro = '"amostra_minima": 20' if "fator" in texto else '"fator_tendencia": 1.25'
    with pytest.raises(config_ranking.ErroNaConfiguracao):
        config_ranking.carregar_config(_grava(tmp_path, base[:-1] + ", " + texto + ", " + outro + "}"))


@pytest.mark.parametrize("valor", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("1e999999999")])
def test_decimal_nao_finito_ou_enorme_direto_no_validador(valor):
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="fator_tendencia"):
        config_ranking.validar_config(_com(fator_tendencia=valor))


def test_chave_repetida_no_json_e_recusada(tmp_path):
    texto = json.dumps(VALIDA)[:-1] + ', "amostra_minima": 5}'
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="repetida"):
        config_ranking.carregar_config(_grava(tmp_path, texto))


def test_arquivo_que_nao_e_utf8(tmp_path):
    caminho = tmp_path / "ranking.json"
    caminho.write_bytes(bytes([0xFF, 0xFE, 0x00]))
    with pytest.raises(config_ranking.ErroNaConfiguracao):
        config_ranking.carregar_config(caminho)


def test_float_passado_direto_ao_validador_e_recusado():
    dados = copy.deepcopy(VALIDA)  # VALIDA usa 1.25 como float do Python
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="fator_tendencia"):
        config_ranking.validar_config(dados)


# ---------- peso vence tendência ----------

def _config_com(fator, **pesos):
    dados = copy.deepcopy(VALIDA)
    dados["pesos"].update(pesos)
    dados["fator_tendencia"] = Decimal(str(fator))
    return dados


def test_fator_1_5_com_pesos_2_e_3_e_recusado_e_1_4999_aceito():
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="peso vence tendência"):
        config_ranking.validar_config(_config_com("1.5"))
    assert config_ranking.validar_config(_config_com("1.4999")).fator_tendencia == Fraction(14999, 10000)
    assert config_ranking.validar_config(_config_com("1.49")).fator_tendencia == Fraction(149, 100)
    assert config_ranking.validar_config(_config_com("1.25")).fator_tendencia == Fraction(5, 4)


def test_fator_acima_do_limite_e_recusado():
    for fator in ("1.51", "2", "3"):
        with pytest.raises(config_ranking.ErroNaConfiguracao, match="peso vence tendência"):
            config_ranking.validar_config(_config_com(fator))


def test_mensagem_da_regra_e_curta_e_cita_fator_tendencia():
    with pytest.raises(config_ranking.ErroNaConfiguracao) as erro:
        config_ranking.validar_config(_config_com("1.5"))
    assert "fator_tendencia" in str(erro.value) and len(str(erro.value)) < 200


def test_pesos_todos_iguais_nao_tem_par_distinto():
    iguais = dict(qualidade_temperatura=2, item_faltando=2, atraso=2, embalagem=2, atendimento=2)
    # qualquer fator > 1 vale, até um bem alto
    assert config_ranking.validar_config(_config_com("10", **iguais)).fator_tendencia == 10
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="fator_tendencia"):
        config_ranking.validar_config(_config_com("1", **iguais))  # continua exigindo > 1


def test_pesos_diferentes_mudam_o_limite():
    # pesos 1 e 2: o fator precisa ser < 2
    um_e_dois = dict(qualidade_temperatura=2, item_faltando=2, atraso=1, embalagem=1, atendimento=1)
    assert config_ranking.validar_config(_config_com("1.99", **um_e_dois)).fator_tendencia == Fraction(199, 100)
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="peso vence tendência"):
        config_ranking.validar_config(_config_com("2", **um_e_dois))
    # pesos 2 e 5: o limite sobe para 2,5
    dois_e_cinco = dict(qualidade_temperatura=5, item_faltando=5, atraso=2, embalagem=2, atendimento=2)
    assert config_ranking.validar_config(_config_com("2.49", **dois_e_cinco)).amostra_minima == 20
    with pytest.raises(config_ranking.ErroNaConfiguracao):
        config_ranking.validar_config(_config_com("2.5", **dois_e_cinco))


def test_tres_pesos_distintos_vale_o_par_consecutivo_mais_apertado():
    # pesos 2, 3 e 4: pares (2,3) -> < 1,5 e (3,4) -> < 1,3333; manda o mais apertado
    tres = dict(qualidade_temperatura=4, item_faltando=3, atraso=2, embalagem=2, atendimento=3)
    assert config_ranking.validar_config(_config_com("1.33", **tres))
    with pytest.raises(config_ranking.ErroNaConfiguracao, match="peso vence tendência"):
        config_ranking.validar_config(_config_com("1.34", **tres))


def test_entregador_nao_entra_na_regra():
    # entregador não tem peso; só os pesos do ranking contam
    assert config_ranking.validar_config(_config_com("1.49"))


def test_arquivo_versionado_respeita_peso_vence_tendencia():
    cfg = config_ranking.carregar_config()
    assert cfg.fator_tendencia * 2 < 3
