"""Contrato de saída do classificador.

Dados simulados. Projeto independente, sem vínculo com o iFood.
Fonte: docs/ESPECIFICACAO.md ("Categorias e criticidade") e docs/GUIA_ROTULACAO.md v1.1.
"""

CATEGORIAS = (
    "atraso",
    "item_faltando",
    "qualidade_temperatura",
    "embalagem",
    "atendimento",
    "entregador",
)

CRITICIDADES = ("saude", "alergia", "cobranca_indevida")

_CHAVES = frozenset({"categorias", "criticidade"})


class SaidaInvalida(ValueError):
    """A saída do classificador não respeita o contrato."""


def _validar_lista(nome, valor, permitidos):
    if not isinstance(valor, list):
        raise SaidaInvalida(f"'{nome}' deve ser uma lista")
    for item in valor:
        if not isinstance(item, str):
            raise SaidaInvalida(f"'{nome}' contém item que não é texto")
        if item not in permitidos:
            raise SaidaInvalida(f"'{nome}' contém valor fora do conjunto: {item!r}")
    if len(set(valor)) != len(valor):
        raise SaidaInvalida(f"'{nome}' contém item repetido")


def validar_saida(obj):
    """Devolve a saída válida ou levanta SaidaInvalida."""
    if not isinstance(obj, dict):
        raise SaidaInvalida("a saída deve ser um objeto")
    if set(obj) != _CHAVES:
        raise SaidaInvalida("as chaves devem ser exatamente 'categorias' e 'criticidade'")
    _validar_lista("categorias", obj["categorias"], CATEGORIAS)
    _validar_lista("criticidade", obj["criticidade"], CRITICIDADES)
    return {"categorias": list(obj["categorias"]), "criticidade": list(obj["criticidade"])}


def texto_vazio(texto):
    """True para None, string vazia ou só espaços."""
    return texto is None or not str(texto).strip()


def saida_texto_vazio():
    """Saída válida para texto vazio: nenhuma categoria, nenhuma marcação."""
    return {"categorias": [], "criticidade": []}
