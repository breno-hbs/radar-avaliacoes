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


def ha_algo_a_decidir(resultado, criticos, acoes_pendentes, invalidas=(), alertas_falhos=()) -> bool:
    """Card, crítico, avaliação `invalida` da semana, alerta `falhou` não reconhecido ou ação pendente."""
    return bool(resultado.cards or criticos or acoes_pendentes or invalidas or alertas_falhos)


def deve_enviar(resultado, criticos, acoes_pendentes, config, invalidas=(), alertas_falhos=()) -> bool:
    """Há algo a decidir, ou a semana tem volume pequeno (aviso curto)."""
    return (ha_algo_a_decidir(resultado, criticos, acoes_pendentes, invalidas, alertas_falhos)
            or resultado.total < config.amostra_minima)


def aviso_curto(resultado, criticos, acoes_pendentes, config, invalidas=(), alertas_falhos=()) -> bool:
    """Só o volume pequeno motivou o envio: sai o aviso curto, sem link e sem token."""
    return (not ha_algo_a_decidir(resultado, criticos, acoes_pendentes, invalidas, alertas_falhos)
            and resultado.total < config.amostra_minima)


def _hora_local(texto_iso: str) -> str:
    return datetime.fromisoformat(texto_iso).astimezone(semana.FUSO).strftime("%d/%m %H:%M")


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


def _secoes(outros, criticos, invalidas, alertas_falhos, acoes_pendentes) -> list:
    """Seções do formato normal. Nenhuma leva texto de avaliação; os ids passam por _limpo."""
    linhas = []
    if outros:
        linhas.append("Outros temas: " + "; ".join(f"{PROBLEMAS[c.categoria]} ({c.mencoes})" for c in outros) + ".")
    if criticos:
        linhas.append("Relatos a verificar:")
        for c in criticos:
            estado = f"alerta {c['estado_alerta']}" if c["estado_alerta"] else "sem alerta registrado"
            reconhecido = ", reconhecido" if c.get("reconhecido") else ""
            tipos = ", ".join(NOMES_CRITICOS.get(t, t) for t in c["tipos"])
            linhas.append(f"- {_limpo(c['id_externo'])}: {tipos}, relato a verificar, {estado}{reconhecido}")
    if invalidas:
        linhas.append("Classificação falhou, verificar manualmente:")
        for inv in invalidas:
            linhas.append(f"- {_limpo(inv['id_externo'])}: nota {inv['nota']}")
    # Os `falhou` desta semana já estão em "Relatos a verificar": a mensagem não os repete (o JSON pode).
    da_semana = {c["id_externo"] for c in criticos}
    de_outras = [f for f in alertas_falhos if f["id_externo"] not in da_semana]
    if de_outras:
        linhas.append("Alertas que não chegaram:")
        for f in de_outras:
            tipos = ", ".join(NOMES_CRITICOS.get(t, t) for t in f["tipos"])
            linhas.append(
                f"- {_limpo(f['id_externo'])}: {tipos}, relato a verificar, alerta criado em {_hora_local(f['criado_em'])}"
            )
    if acoes_pendentes:
        linhas.append("Ações pendentes:")
        for a in acoes_pendentes:
            problema = PROBLEMAS.get(a["categoria"], _limpo(a["categoria"]))
            estado = NOMES_ESTADO_ACAO.get(a["estado"], _limpo(a["estado"]))
            linhas.append(f"- {problema} (semana de {_semana_da_acao(a['semana_inicio'])}): {estado}")
    return linhas


def _semana_da_acao(texto: str) -> str:
    try:
        return _dia_mes(datetime.fromisoformat(texto))
    except (TypeError, ValueError):
        return texto


def montar(inicio: datetime, resultado, criticos, acoes_pendentes, *, amostra_minima: int, enviar: bool,
           link_painel, invalidas=(), alertas_falhos=(), curto=False) -> dict:
    """JSON e mensagem do resumo. `curto`: só o volume pequeno motivou o envio (aviso curto, sem link)."""
    per = periodo(inicio)
    invalidas, alertas_falhos = list(invalidas), list(alertas_falhos)
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

    linhas = ["(dados simulados)", f"Resumo semanal do Radar: {per['rotulo']}"]
    if curto:  # aviso curto: só o total lido, sem seções nem link
        linhas.append(f"Semana com volume pequeno: {resultado.total} avaliações lidas.")
    elif cards:
        linhas.append("O padrão sugere investigar estes problemas:")
        for i, (card, original) in enumerate(zip(cards, resultado.cards), start=1):
            taxa = str(_uma_casa(original.taxa_por_100)).replace(".", ",")
            alta = ", em alta" if card["tendencia_em_alta"] else ""
            linhas.append(
                f"{i}. {card['problema']}: {card['mencoes']} avaliações de {per['rotulo']} "
                f"({taxa} por 100{alta}). Recomendação: {card['recomendacao']}."
            )
    outros = resultado.outros_temas
    if not curto:
        linhas.extend(_secoes(outros, criticos, invalidas, alertas_falhos, acoes_pendentes))
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
        "invalidas_a_verificar": invalidas,
        "alertas_falhos": alertas_falhos,
        "acoes_pendentes": acoes_pendentes,
        "cobertura": cobertura,
        "enviar": enviar,
        "mensagem": "\n".join(linhas),
        "link_painel": link_painel,
    }
