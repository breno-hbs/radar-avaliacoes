"""Trecho de avaliação mostrado como evidência no painel (bloco 4.1).

Fonte da verdade: docs/ESPECIFICACAO.md (decisões de 31/10/2026 sobre as rotas de leitura do painel e as
evidências do POST /acoes). É a função ÚNICA de trecho: o GET /cards a usa para mostrar a evidência e o
POST /acoes a usará para gravar o `trecho_exibido`, para que o que o dono vê seja o que fica gravado.
"""

LIMITE = 140
RETICENCIAS = "…"


def trecho(texto: str) -> str:
    """Espaços e quebras de linha normalizados; até 140 caracteres, cortados em limite de palavra.

    Se o texto normalizado cabe em 140 caracteres, sai inteiro. Senão, corta no último espaço até a posição
    140 (um espaço logo depois do 140º caractere conta: as 140 primeiras já formam palavras inteiras) e
    acrescenta "…". Uma palavra maior que 140 sem espaço é cortada em 140.
    """
    normalizado = " ".join(texto.split())
    if len(normalizado) <= LIMITE:
        return normalizado
    corte = normalizado.rfind(" ", 0, LIMITE + 1)
    base = normalizado[:corte] if corte > 0 else normalizado[:LIMITE]
    return base.rstrip() + RETICENCIAS
