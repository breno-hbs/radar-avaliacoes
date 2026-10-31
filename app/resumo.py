"""Montagem do resumo semanal (bloco 2.7). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md (contrato de GET /resumo-semanal, "Regras de negócio", tabela de
categorias e a decisão de 31/10/2026 sobre o resumo). Recebe o que já foi calculado (ranking, críticos,
ações) e monta o JSON e o texto; não lê o banco nem cria token.
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app import ranking, semana

# Coluna "Ação recomendada no card" da tabela de categorias, copiada da especificação.
# entregador não tem ação interna e por isso não aparece aqui.
RECOMENDACOES = {
    "atraso": "Investigar o gargalo nos horários das reclamações, sem presumir a causa",
    "item_faltando": "Criar checklist de conferência antes de fechar a sacola",
    "qualidade_temperatura": "Revisar o preparo ou a conservação do prato citado",
    "embalagem": "Testar melhoria da embalagem do item citado",
    "atendimento": "Orientar a equipe responsável",
}

# O problema em uma linha: nome curto por categoria (a coluna "O que cobre" da tabela é uma descrição longa).
# Até 45 caracteres, sem "_" e sem vírgula: aparecem no título do card, em "Outros temas" e nas ações.
PROBLEMAS = {
    "atraso": "Atraso na entrega",
    "item_faltando": "Item ausente ou errado",
    "qualidade_temperatura": "Qualidade ou temperatura da comida",
    "embalagem": "Embalagem inadequada ou com vazamento",
    "atendimento": "Atendimento do restaurante",
}

# Nomes legíveis para a mensagem, que o dono lê como texto simples: sem identificadores com "_".
NOMES_CRITICOS = {"saude": "saúde", "alergia": "alergia", "cobranca_indevida": "cobrança indevida"}
NOMES_ESTADO_ACAO = {"pendente": "pendente", "em_andamento": "em andamento", "concluida": "concluída"}
NOMES_SEM_ACAO = {"entregador": "Entregador"}

TEXTO_TENDENCIA = {
    ranking.MOTIVO_AMOSTRA_PEQUENA: "tendência não calculada: amostra pequena",
    ranking.MOTIVO_SEM_SEMANA_ANTERIOR: "tendência não calculada: sem semana anterior",
}


def _limpo(texto: str) -> str:
    """Tira quebras de linha e caracteres de controle de um valor vindo de fora (id_externo)."""
    return "".join(c if c.isprintable() else " " for c in str(texto))


def _uma_casa(fracao) -> Decimal:
    """Arredonda só na borda: a fração exata vira decimal com 1 casa (metade para cima)."""
    return (Decimal(fracao.numerator) / Decimal(fracao.denominator)).quantize(Decimal("0.1"), ROUND_HALF_UP)


def _dia_mes(instante: datetime) -> str:
    return instante.astimezone(semana.FUSO).strftime("%d/%m")


def periodo(inicio: datetime) -> dict:
    fim = datetime.combine(inicio.date() + timedelta(days=7), inicio.timetz())
    ultimo_dia = fim - timedelta(days=1)
    return {
        "inicio": inicio.isoformat(),
        "fim_exclusivo": fim.isoformat(),
        "rotulo": f"{_dia_mes(inicio)} a {_dia_mes(ultimo_dia)}",
    }


def deve_enviar(resultado, criticos, acoes_pendentes, config) -> bool:
    """Há algo a decidir: card, crítico ou ação pendente; ou volume pequeno (aviso curto)."""
    return bool(resultado.cards or criticos or acoes_pendentes or resultado.total < config.amostra_minima)


def _cobertura(resultado) -> dict:
    temas = [{"categoria": t.categoria, "mencoes": t.mencoes} for t in resultado.temas_sem_acao]
    tendencia = TEXTO_TENDENCIA.get(resultado.motivo_sem_tendencia)
    sem_acao = ", ".join(f"{NOMES_SEM_ACAO.get(t['categoria'], t['categoria'])} ({t['mencoes']})"
                         for t in temas) or "nenhum"
    texto = (
        f"Cobertura: {resultado.total} avaliações lidas, {len(resultado.cards)} cards, "
        f"temas monitorados sem ação: {sem_acao}, "
        f"{resultado.invalidas} avaliações que a IA não conseguiu classificar (saída inválida)"
    )
    texto += f"; {tendencia}." if tendencia else "."  # a frase da especificação, sem mudar
    return {
        "total_lido": resultado.total,
        "cards": len(resultado.cards),
        "temas_sem_acao": temas,
        "nao_classificadas": resultado.invalidas,
        "tendencia": tendencia,
        "texto": texto,
    }


def _semana_da_acao(texto: str) -> str:
    try:
        return _dia_mes(datetime.fromisoformat(texto))
    except (TypeError, ValueError):
        return texto


def montar(inicio: datetime, resultado, criticos, acoes_pendentes, *, amostra_minima: int, enviar: bool,
           link_painel) -> dict:
    per = periodo(inicio)
    cards = [
        {
            "categoria": c.categoria,
            "mencoes": c.mencoes,
            "taxa_por_100": float(_uma_casa(c.taxa_por_100)),
            "tendencia_em_alta": c.fator_tendencia > 1,
            "problema": PROBLEMAS[c.categoria],
            "recomendacao": RECOMENDACOES[c.categoria],
        }
        for c in resultado.cards
    ]
    cobertura = _cobertura(resultado)

    linhas = [f"Resumo semanal do Radar (dados simulados): {per['rotulo']}"]
    if cards:
        linhas.append("O padrão sugere investigar estes problemas:")
        for i, (card, original) in enumerate(zip(cards, resultado.cards), start=1):
            taxa = str(_uma_casa(original.taxa_por_100)).replace(".", ",")
            alta = ", em alta" if card["tendencia_em_alta"] else ""
            linhas.append(
                f"{i}. {card['problema']}: {card['mencoes']} avaliações de {per['rotulo']} "
                f"({taxa} por 100{alta}). Recomendação: {card['recomendacao']}."
            )
    elif resultado.total < amostra_minima:  # volume pequeno sem cards: aviso curto
        linhas.append(f"Semana com volume pequeno: {resultado.total} avaliações lidas.")
    outros = resultado.outros_temas
    if outros:
        linhas.append("Outros temas: " + "; ".join(f"{PROBLEMAS[c.categoria]} ({c.mencoes})" for c in outros) + ".")
    if criticos:
        linhas.append("Relatos a verificar:")
        for c in criticos:
            estado = c["estado_alerta"] or "sem alerta registrado"
            tipos = ", ".join(NOMES_CRITICOS.get(t, t) for t in c["tipos"])
            linhas.append(f"- {_limpo(c['id_externo'])}: {tipos}, relato a verificar, alerta {estado}")
    if acoes_pendentes:
        linhas.append("Ações pendentes:")
        for a in acoes_pendentes:
            problema = PROBLEMAS.get(a["categoria"], _limpo(a["categoria"]))
            estado = NOMES_ESTADO_ACAO.get(a["estado"], _limpo(a["estado"]))
            linhas.append(f"- {problema} (semana de {_semana_da_acao(a['semana_inicio'])}): {estado}")
    linhas.append(cobertura["texto"])
    if link_painel:
        linhas.append(f"Painel: {link_painel}")

    return {
        "periodo": per,
        "total": resultado.total,
        "negativas": resultado.negativas,
        "invalidas": resultado.invalidas,
        "cards": cards,
        "outros_temas": [{"categoria": c.categoria, "mencoes": c.mencoes} for c in outros],
        "temas_sem_acao": cobertura["temas_sem_acao"],
        "criticos": criticos,
        "acoes_pendentes": acoes_pendentes,
        "cobertura": cobertura,
        "enviar": enviar,
        "mensagem": "\n".join(linhas),
        "link_painel": link_painel,
    }
