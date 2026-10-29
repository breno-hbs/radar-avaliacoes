"""Métricas de avaliação do classificador. Funções puras sobre listas.

Dados simulados. Projeto independente, sem vínculo com o iFood.
Previsão None (saída inválida do modelo) vale como previsão vazia e é contada.
"""
from app.contrato import CATEGORIAS, CRITICIDADES


def _validar_rotulos(nome, rotulos, permitidos):
    if not isinstance(rotulos, list):
        raise ValueError(f"{nome} deve ser uma lista")
    for rotulo in rotulos:
        if not isinstance(rotulo, str):
            raise ValueError(f"{nome} contém rótulo que não é texto")
        if rotulo not in permitidos:
            raise ValueError(f"{nome} contém rótulo fora do conjunto: {rotulo!r}")


def _validar_entrada(gabaritos, previsoes, permitidos):
    """Tamanhos iguais, listas de listas e só rótulos do conjunto; previsão pode ser None."""
    if not isinstance(gabaritos, list) or not isinstance(previsoes, list):
        raise ValueError("gabaritos e previsões devem ser listas")
    if len(gabaritos) != len(previsoes):
        raise ValueError("gabaritos e previsões têm tamanhos diferentes")
    for gab, prev in zip(gabaritos, previsoes):
        _validar_rotulos("gabarito", gab, permitidos)
        if prev is not None:
            _validar_rotulos("previsão", prev, permitidos)


def _razao(num, den):
    return num / den if den else None


def _f1(tp, fp, fn):
    """None se não há ocorrência; 0.0 se há ocorrência e nenhum acerto."""
    if tp + fp + fn == 0:
        return None
    return 2 * tp / (2 * tp + fp + fn)


def f1_categorias(gabaritos, previsoes):
    """P, R e F1 por categoria, micro e macro, mais a contagem de saídas inválidas."""
    _validar_entrada(gabaritos, previsoes, CATEGORIAS)
    contagem = {c: {"tp": 0, "fp": 0, "fn": 0} for c in CATEGORIAS}
    invalidas = 0
    for gab, prev in zip(gabaritos, previsoes):
        if prev is None:
            invalidas += 1
            prev = []
        gab, prev = set(gab), set(prev)
        for c in CATEGORIAS:
            contagem[c]["tp"] += c in gab and c in prev
            contagem[c]["fp"] += c not in gab and c in prev
            contagem[c]["fn"] += c in gab and c not in prev

    por_categoria = {}
    for c, k in contagem.items():
        por_categoria[c] = {
            **k,
            "precisao": _razao(k["tp"], k["tp"] + k["fp"]),
            "recall": _razao(k["tp"], k["tp"] + k["fn"]),
            "f1": _f1(k["tp"], k["fp"], k["fn"]),
        }

    tp = sum(k["tp"] for k in contagem.values())
    fp = sum(k["fp"] for k in contagem.values())
    fn = sum(k["fn"] for k in contagem.values())
    micro = {
        "precisao": _razao(tp, tp + fp),
        "recall": _razao(tp, tp + fn),
        "f1": _f1(tp, fp, fn),
    }

    definidos = [m["f1"] for m in por_categoria.values() if m["f1"] is not None]
    return {
        "por_categoria": por_categoria,
        "micro": micro,
        "macro_f1": sum(definidos) / len(definidos) if definidos else None,
        "categorias_fora_do_macro": [c for c, m in por_categoria.items() if m["f1"] is None],
        "saidas_invalidas": invalidas,
    }


def recall_criticos(gabaritos, previsoes):
    """Recall geral (contagem) e por tipo, com falsos positivos por tipo."""
    _validar_entrada(gabaritos, previsoes, CRITICIDADES)
    if any(not gab for gab in gabaritos):
        raise ValueError("gabarito sem marcação não é um caso crítico")
    por_tipo = {t: {"acertos": 0, "total": 0, "falsos_positivos": 0} for t in CRITICIDADES}
    detectadas = 0
    invalidas = 0
    for gab, prev in zip(gabaritos, previsoes):
        if prev is None:
            invalidas += 1
            prev = []
        gab, prev = set(gab), set(prev)
        if prev:
            detectadas += 1
        for t in CRITICIDADES:
            por_tipo[t]["total"] += t in gab
            por_tipo[t]["acertos"] += t in gab and t in prev
            por_tipo[t]["falsos_positivos"] += t not in gab and t in prev
    for m in por_tipo.values():
        m["recall"] = _razao(m["acertos"], m["total"])
    return {
        "detectadas": detectadas,
        "total": len(gabaritos),
        "por_tipo": por_tipo,
        "saidas_invalidas": invalidas,
    }


def falsos_alertas(gabaritos, previsoes):
    """Avaliações não críticas com ao menos uma marcação prevista (conta 1 por avaliação)."""
    _validar_entrada(gabaritos, previsoes, CRITICIDADES)
    falsos = 0
    nao_criticas = 0
    invalidas = 0
    for gab, prev in zip(gabaritos, previsoes):
        if prev is None:
            invalidas += 1
            prev = []
        if gab:
            continue
        nao_criticas += 1
        if prev:
            falsos += 1
    return {
        "falsos_alertas": falsos,
        "total_nao_criticas": nao_criticas,
        "saidas_invalidas": invalidas,
    }
