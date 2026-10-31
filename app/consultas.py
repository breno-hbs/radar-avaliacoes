"""Consultas de leitura para o resumo semanal (bloco 2.6).

Fonte da verdade: docs/ESPECIFICACAO.md ("`mencoes`", "Avaliações críticas" e a linha de cobertura).
"""

import json
from collections import Counter
from datetime import datetime, timezone

from app import semana
from app.ranking import ContagemSemana

NOTA_NEGATIVA_MAXIMA = 2  # negativa = nota <= 2 (decisão de 31/10/2026 sobre o ranking)


def _utc_texto(instante: datetime, nome: str) -> str:
    if not isinstance(instante, datetime) or instante.utcoffset() is None:
        raise ValueError(f"{nome} precisa ter fuso")
    return instante.astimezone(timezone.utc).isoformat()


def contar_semana(conn, inicio_utc: datetime, fim_utc: datetime) -> ContagemSemana:
    """Contagens das avaliações com inicio <= recebida_em < fim.

    recebida_em está gravado em UTC com isoformat(); os limites são convertidos para o mesmo formato,
    e a comparação por texto segue a ordem dos instantes (ver app/semana.py). Uma avaliação conta uma vez
    por categoria; as críticas contam normalmente nas suas categorias operacionais.
    """
    inicio, fim = _utc_texto(inicio_utc, "inicio_utc"), _utc_texto(fim_utc, "fim_utc")
    if inicio_utc >= fim_utc:
        raise ValueError("o intervalo precisa ter início antes do fim")
    linhas = conn.execute(
        "SELECT categorias, nota, status_classificacao FROM avaliacoes "
        "WHERE recebida_em >= ? AND recebida_em < ?",
        (inicio, fim),
    ).fetchall()
    mencoes = Counter()
    invalidas = negativas = 0
    for categorias, nota, status in linhas:
        mencoes.update(set(json.loads(categorias)))
        invalidas += status == "invalida"
        negativas += nota <= NOTA_NEGATIVA_MAXIMA
    return ContagemSemana(total=len(linhas), mencoes=dict(mencoes), invalidas=invalidas, negativas=negativas)


def listar_criticos(conn, inicio_utc: datetime, fim_utc: datetime) -> list:
    """Avaliações da semana com marcação crítica: id_externo, tipos e estado do alerta (ou None).

    Sem o texto da avaliação. A semana segue a mesma regra de contar_semana.
    """
    inicio, fim = _utc_texto(inicio_utc, "inicio_utc"), _utc_texto(fim_utc, "fim_utc")
    if inicio_utc >= fim_utc:
        raise ValueError("o intervalo precisa ter início antes do fim")
    linhas = conn.execute(
        "SELECT a.id_externo, a.criticidade, al.estado, al.reconhecido_em FROM avaliacoes a "
        "LEFT JOIN alertas al ON al.avaliacao_id = a.id "
        "WHERE a.recebida_em >= ? AND a.recebida_em < ? ORDER BY a.recebida_em, a.id",
        (inicio, fim),
    ).fetchall()
    criticos = []
    for id_externo, criticidade, estado, reconhecido_em in linhas:
        tipos = json.loads(criticidade)
        if tipos:
            criticos.append({
                "id_externo": id_externo, "tipos": tipos, "estado_alerta": estado,
                "reconhecido": reconhecido_em is not None,
            })
    return criticos


def listar_invalidas(conn, inicio_utc: datetime, fim_utc: datetime) -> list:
    """Avaliações `invalida` da semana (id_externo e nota, sem texto), em ordem de recebida_em."""
    inicio, fim = _utc_texto(inicio_utc, "inicio_utc"), _utc_texto(fim_utc, "fim_utc")
    if inicio_utc >= fim_utc:
        raise ValueError("o intervalo precisa ter início antes do fim")
    linhas = conn.execute(
        "SELECT id_externo, nota FROM avaliacoes WHERE status_classificacao = 'invalida' "
        "AND recebida_em >= ? AND recebida_em < ? ORDER BY recebida_em, id",
        (inicio, fim),
    ).fetchall()
    return [{"id_externo": e, "nota": n} for e, n in linhas]


def listar_alertas_falhos(conn) -> list:
    """Alertas `falhou` ainda não reconhecidos, de QUALQUER semana, em ordem de criado_em.

    criado_em sai em ISO com o fuso de America/Sao_Paulo, como o `periodo` do resumo.
    """
    linhas = conn.execute(
        "SELECT v.id_externo, al.tipos, al.criado_em FROM alertas al "
        "JOIN avaliacoes v ON v.id = al.avaliacao_id "
        "WHERE al.estado = 'falhou' AND al.reconhecido_em IS NULL ORDER BY al.criado_em, al.id"
    ).fetchall()
    return [
        {
            "id_externo": e,
            "tipos": json.loads(t),
            "criado_em": datetime.fromisoformat(c).astimezone(semana.FUSO).isoformat(),
        }
        for e, t, c in linhas
    ]


ESTADOS_ACAO_PENDENTE = ("pendente", "em_andamento")


def listar_acoes_pendentes(conn) -> list:
    """Ações ainda não concluídas (pendente ou em_andamento), de qualquer semana."""
    linhas = conn.execute(
        "SELECT categoria, semana_inicio, estado FROM acoes WHERE estado IN (?, ?) ORDER BY semana_inicio, id",
        ESTADOS_ACAO_PENDENTE,
    ).fetchall()
    return [{"categoria": c, "semana_inicio": s, "estado": e} for c, s, e in linhas]


# ---------- painel (bloco 4.1) ----------

MAX_EVIDENCIAS = 3
MAX_SEMANAS_HISTORICO = 12


def listar_evidencias(conn, inicio_utc: datetime, fim_utc: datetime, categoria: str) -> list:
    """Até 3 avaliações da semana com a categoria: menor nota, depois recebida_em mais recente, depois id.

    Críticas entram, com `critica` verdadeiro (relato a verificar). Devolve o texto inteiro: quem chama
    deriva o trecho com app/trechos.py.
    """
    inicio, fim = _utc_texto(inicio_utc, "inicio_utc"), _utc_texto(fim_utc, "fim_utc")
    linhas = conn.execute(
        "SELECT id, texto, nota, recebida_em, categorias, criticidade FROM avaliacoes "
        "WHERE recebida_em >= ? AND recebida_em < ?",
        (inicio, fim),
    ).fetchall()
    da_categoria = [l for l in linhas if categoria in json.loads(l["categorias"])]
    # menor nota; entre iguais, a mais recente (recebida_em decrescente); entre iguais, o menor id
    da_categoria.sort(key=lambda l: l["id"])
    da_categoria.sort(key=lambda l: l["recebida_em"], reverse=True)
    da_categoria.sort(key=lambda l: l["nota"])
    return [
        {"avaliacao_id": l["id"], "texto": l["texto"], "nota": l["nota"], "critica": bool(json.loads(l["criticidade"]))}
        for l in da_categoria[:MAX_EVIDENCIAS]
    ]


def acao_da_semana(conn, categoria: str, semana_inicio_utc: datetime):
    """{id, estado} da ação mais recente da categoria na semana, ou None.

    acoes.semana_inicio guarda o início da semana em UTC (ISO com +00:00), como o limite inicial da semana.
    """
    linha = conn.execute(
        "SELECT id, estado FROM acoes WHERE categoria = ? AND semana_inicio = ? ORDER BY id DESC LIMIT 1",
        (categoria, _utc_texto(semana_inicio_utc, "semana_inicio_utc")),
    ).fetchone()
    return None if linha is None else {"id": linha[0], "estado": linha[1]}


def ultima_leitura(conn):
    """recebida_em mais recente do banco, em ISO com fuso America/Sao_Paulo, ou None sem avaliações."""
    valor = conn.execute("SELECT MAX(recebida_em) FROM avaliacoes").fetchone()[0]
    return None if valor is None else datetime.fromisoformat(valor).astimezone(semana.FUSO).isoformat()


def listar_historico(conn) -> list:
    """Semanas com pelo menos uma ação, da mais recente à mais antiga, no máximo 12.

    Cada ação traz as evidências gravadas (trecho_exibido NUNCA recalculado) e o texto original da avaliação.
    """
    semanas = [l[0] for l in conn.execute(
        "SELECT DISTINCT semana_inicio FROM acoes ORDER BY semana_inicio DESC LIMIT ?", (MAX_SEMANAS_HISTORICO,)
    )]
    resultado = []
    for semana_inicio in semanas:
        acoes = []
        for a in conn.execute(
            "SELECT id, categoria, estado, criada_em, concluida_em, nota FROM acoes WHERE semana_inicio = ? "
            "ORDER BY id", (semana_inicio,),
        ).fetchall():
            evidencias = [
                {"posicao": p, "trecho_exibido": t, "texto_original": o}
                for p, t, o in conn.execute(
                    "SELECT ae.posicao, ae.trecho_exibido, v.texto FROM acao_avaliacoes ae "
                    "LEFT JOIN avaliacoes v ON v.id = ae.avaliacao_id WHERE ae.acao_id = ? ORDER BY ae.posicao",
                    (a["id"],),
                )
            ]
            acoes.append({
                "id": a["id"], "categoria": a["categoria"], "estado": a["estado"], "criada_em": a["criada_em"],
                "concluida_em": a["concluida_em"], "nota": a["nota"], "evidencias": evidencias,
            })
        resultado.append({"semana_inicio": semana_inicio, "acoes": acoes})
    return resultado
