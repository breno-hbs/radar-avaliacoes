"""Configuração do ranking dos cards semanais (bloco 2.6).

Fonte da verdade: docs/ESPECIFICACAO.md (decisões de 31/10/2026 sobre os pesos e o complemento do
ranking). Os valores são hipóteses de priorização da v1 e ficam em config/ranking.json, versionado no
código; aqui eles só são lidos e validados.

Formato:
  {"pesos": {<categoria>: <inteiro positivo> | null, ...}, "fator_tendencia": <número > 1>,
   "amostra_minima": <inteiro >= 1>}
Todas as seis categorias precisam aparecer em "pesos". null = fora do ranking (só `entregador`, que
nunca gera ação interna). O fator é lido como fração exata, sem passar por float.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType

from app import contrato

CAMINHO_PADRAO = Path(__file__).resolve().parent.parent / "config" / "ranking.json"
SEM_ACAO = ("entregador",)  # docs/ESPECIFICACAO.md: "Entregador nunca gera ação interna"
_CHAVES = ("pesos", "fator_tendencia", "amostra_minima")
_EXPOENTE_MAXIMO = 6  # fator até a ordem de 10**6; muito além de qualquer configuração razoável


class ErroNaConfiguracao(ValueError):
    """Configuração do ranking ausente ou inválida."""


@dataclass(frozen=True)
class ConfigRanking:
    pesos: MappingProxyType  # categoria -> peso inteiro, só as que entram no ranking
    fora_do_ranking: tuple  # categorias monitoradas sem ação
    fator_tendencia: Fraction
    amostra_minima: int


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def validar_config(dados) -> ConfigRanking:
    if not isinstance(dados, dict):
        raise ErroNaConfiguracao("a configuração deve ser um objeto JSON")
    for chave in _CHAVES:
        if chave not in dados:
            raise ErroNaConfiguracao(f"chave obrigatória ausente: {chave}")
    extras = sorted(set(dados) - set(_CHAVES))
    if extras:
        raise ErroNaConfiguracao(f"chave desconhecida: {extras[0]}")

    pesos = dados["pesos"]
    if not isinstance(pesos, dict):
        raise ErroNaConfiguracao("pesos deve ser um objeto categoria -> peso")
    desconhecidas = sorted(set(pesos) - set(contrato.CATEGORIAS))
    if desconhecidas:
        raise ErroNaConfiguracao(f"categoria desconhecida em pesos: {desconhecidas[0]}")
    no_ranking = {}
    for categoria in contrato.CATEGORIAS:
        if categoria not in pesos:
            raise ErroNaConfiguracao(f"peso ausente para a categoria: {categoria}")
        peso = pesos[categoria]
        if categoria in SEM_ACAO:
            if peso is not None:
                raise ErroNaConfiguracao(f"{categoria} não participa do ranking: o peso deve ser null")
            continue
        if not _inteiro(peso) or peso < 1:
            raise ErroNaConfiguracao(f"peso de {categoria} deve ser inteiro positivo")
        no_ranking[categoria] = peso

    fator = dados["fator_tendencia"]
    if isinstance(fator, bool) or not isinstance(fator, (int, Decimal, Fraction)):
        raise ErroNaConfiguracao("fator_tendencia deve ser um número")
    # Antes de virar Fraction: NaN/Infinity quebram a conversão e um expoente enorme travaria a montagem
    # do inteiro (1e999999999 = 10 elevado a quase um bilhão).
    if isinstance(fator, Decimal) and (not fator.is_finite() or abs(fator.adjusted()) > _EXPOENTE_MAXIMO):
        raise ErroNaConfiguracao("fator_tendencia deve ser um número finito de tamanho razoável")
    fator = Fraction(fator)
    if fator <= 1:
        raise ErroNaConfiguracao("fator_tendencia deve ser maior que 1")

    amostra = dados["amostra_minima"]
    if not _inteiro(amostra) or amostra < 1:
        raise ErroNaConfiguracao("amostra_minima deve ser inteiro maior ou igual a 1")

    return ConfigRanking(
        pesos=MappingProxyType(no_ranking),
        fora_do_ranking=SEM_ACAO,
        fator_tendencia=fator,
        amostra_minima=amostra,
    )


def carregar_config(caminho=CAMINHO_PADRAO) -> ConfigRanking:
    """Lê o JSON (números decimais como Decimal, nunca float) e valida."""
    try:
        texto = Path(caminho).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ErroNaConfiguracao(f"arquivo de configuração do ranking não encontrado: {caminho}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ErroNaConfiguracao(f"não foi possível ler a configuração do ranking ({type(e).__name__})") from None
    try:
        dados = json.loads(texto, parse_float=Decimal, object_pairs_hook=_sem_chave_repetida)
    except ErroNaConfiguracao:
        raise
    except ValueError:  # JSONDecodeError e inteiro acima do limite de dígitos do Python
        raise ErroNaConfiguracao("configuração do ranking não é JSON válido") from None
    return validar_config(dados)


def _sem_chave_repetida(pares):
    dados = {}
    for chave, valor in pares:
        if chave in dados:
            raise ErroNaConfiguracao(f"chave repetida na configuração: {chave}")
        dados[chave] = valor
    return dados
