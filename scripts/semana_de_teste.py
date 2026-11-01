"""Gera a semana sintética fixa do teste de 2 minutos (critério 8). Dados SIMULADOS.

Fonte da verdade: docs/ESPECIFICACAO.md ("Teste dos 2 minutos"): a mesma semana, com 3 cards, para todos os
testadores. O conteúdo é sempre o mesmo (24 avaliações, rotuladas à mão segundo docs/GUIA_ROTULACAO.md); só as
datas andam, porque a semana é a última semana completa (segunda a domingo, America/Sao_Paulo) do momento da
execução, a mesma que o painel abre sem ?semana=.

Pesos de config/ranking.json: item_faltando 5 menções x 3 = 15, qualidade_temperatura 4 x 3 = 12,
atendimento 4 x 2 = 8. Sem semana anterior, a tendência não é calculada. Sem avaliação crítica: nenhum alerta.

Cria um banco NOVO (recusa um arquivo que já existe) e imprime o link do painel na última linha. O token é
novo a cada execução, só o hash vai para o banco, e o token em claro sai UMA vez, no link: não vai para arquivo
nem para log.

Uso: RADAR_URL_BASE=http://localhost:8000 python scripts/semana_de_teste.py caminho_do_banco.db
"""

import argparse
import os
import sys
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import banco, semana, tokens  # noqa: E402

# (dia da semana, hora, nota, categorias, texto). Dia 0 = segunda-feira.
AVALIACOES = (
    (0, 11, 2, ["item_faltando"], "Pedi dois combos e veio só um. Faltou a batata."),
    (0, 12, 5, [], "Chegou quentinho e no horário. Muito bom, recomendo."),
    (0, 19, 1, ["qualidade_temperatura"], "O hambúrguer chegou frio e o pão estava duro."),
    (1, 12, 4, [], "Lanche saboroso, bem embalado. Só achei o preço um pouco alto."),
    (1, 13, 2, ["item_faltando"], "Pedi sem cebola e veio com cebola."),
    (1, 20, 5, [], "Pizza ótima, massa fininha do jeito que eu gosto."),
    (2, 12, 1, ["atendimento"], "Mandei mensagem para a loja e ninguém respondeu."),
    (2, 13, 3, ["qualidade_temperatura"], "A batata veio murcha e sem gosto."),
    (2, 19, 5, [], "Tudo certo, comida deliciosa."),
    (3, 12, 2, ["item_faltando"], "Veio faltando o refrigerante que eu pedi."),
    (3, 13, 4, [], "Gostei bastante, porção generosa."),
    (3, 20, 1, ["atendimento"], "Liguei para o restaurante e fui mal atendido."),
    (4, 12, 2, ["qualidade_temperatura"], "A carne veio crua por dentro."),
    (4, 19, 5, [], "Sobremesa maravilhosa, voltarei a pedir."),
    (4, 21, 2, ["item_faltando"], "Meu pedido veio incompleto, faltou um lanche."),
    (5, 12, 1, ["atendimento"], "O restaurante ignorou minha reclamação."),
    (5, 13, 5, [], "Saiu rápido e estava tudo no ponto."),
    (5, 20, 3, ["qualidade_temperatura"], "O refrigerante veio quente."),
    (5, 21, 4, [], "Boa comida, embalagem caprichada."),
    (6, 12, 2, ["item_faltando"], "Pedi pizza grande e veio média."),
    (6, 13, 5, [], "Hambúrguer perfeito, carne no ponto."),
    (6, 19, 2, ["atendimento"], "Péssimo atendimento, o funcionário foi grosseiro."),
    (6, 20, 4, [], "Pedido certo, sabor muito bom."),
    (6, 21, 5, [], "Comida quentinha, chegou antes do previsto."),
)


class ErroDeUso(Exception):
    """Entrada recusada antes de qualquer escrita (a mensagem vai para o stderr)."""


def _url_base(valor: Optional[str]) -> str:
    """http ou https, com host, sem barra final, sem query nem fragmento (a mesma regra do servidor)."""
    valor = valor or ""
    partes = urlsplit(valor)
    if (
        partes.scheme not in ("http", "https")
        or not partes.netloc
        or valor.endswith("/")
        or "?" in valor
        or "#" in valor
        or any(c.isspace() for c in valor)
    ):
        raise ErroDeUso("RADAR_URL_BASE ausente ou inválida (use http ou https, sem barra final)")
    return valor


def gerar(caminho, *, url_base: str, agora: Optional[datetime] = None) -> str:
    """Cria o banco novo com a semana fixa e devolve o link do painel.

    `agora` só escolhe a semana (a última completa antes dele); o token usa sempre o relógio real.
    """
    url_base = _url_base(url_base)
    caminho = Path(caminho)
    if caminho.exists():
        raise ErroDeUso(f"o banco já existe, use um caminho novo: {caminho}")
    if not caminho.resolve().parent.is_dir():
        raise ErroDeUso(f"a pasta do banco não existe: {caminho.resolve().parent}")
    inicio = semana.ultima_semana_completa(agora if agora is not None else datetime.now(timezone.utc))
    try:
        with closing(banco.abrir(caminho)) as conn:
            for i, (dia, hora, nota, categorias, texto) in enumerate(AVALIACOES, start=1):
                recebida = datetime.combine(inicio.date() + timedelta(days=dia), time(hora), semana.FUSO)
                banco.gravar_avaliacao(
                    conn, id_externo=f"teste2min-{i:02d}", texto=texto, nota=nota, recebida_em=recebida.isoformat(),
                    categorias=categorias, criticidade=[], status_classificacao="ok",
                )
            token = tokens.criar_token_painel(conn, agora=datetime.now(timezone.utc))
    except BaseException:
        caminho.unlink(missing_ok=True)  # nada de banco pela metade
        raise
    return f"{url_base}/painel/{token}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gera a semana sintética fixa do teste de 2 minutos.")
    parser.add_argument("banco", help="caminho do banco NOVO (não pode existir)")
    args = parser.parse_args(argv)
    agora = datetime.now(timezone.utc)  # um só relógio: a semana impressa é a gravada
    try:
        link = gerar(args.banco, url_base=os.environ.get("RADAR_URL_BASE", ""), agora=agora)
    except ErroDeUso as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 2
    inicio = semana.ultima_semana_completa(agora)
    fim = inicio + timedelta(days=6)
    print("Semana sintética fixa do teste de 2 minutos (dados simulados).")
    print(f"Semana: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}. Avaliações: {len(AVALIACOES)}. Cards esperados: 3.")
    print(f"Banco: {args.banco}")
    print("Link do painel (vale 7 dias; um token novo a cada execução):")
    print(link)
    return 0


if __name__ == "__main__":
    sys.exit(main())
