/* Painel do Radar de Avaliações (bloco 4.3). Dados SIMULADOS.
   Fonte da verdade: docs/ESPECIFICACAO.md (decisão de 31/10/2026 sobre a página do painel).

   Regras desta página:
   - Só conversa com /cards, /historico e o POST e o PATCH de ações.
   - O token é lido do endereço a cada carga; não vai para armazenamento do navegador, cookie, query nem log.
   - Todo texto vindo da API entra com textContent (função el abaixo); nada é interpretado como HTML.
   - Textos fixos (rótulos, botões, nomes das categorias) ficam aqui; os valores vêm todos da API. */
"use strict";

(() => {
  // N da amostra mínima (config/ranking.json); um teste confere que é o mesmo valor.
  const AMOSTRA_MINIMA = 20;
  const LIMITE_NOTA = 500;

  // Nome curto e recomendação por categoria, iguais aos de app/resumo.py (um teste confere).
  // Outros temas e histórico trazem só a categoria.
  const PROBLEMAS = Object.freeze({
    atraso: "Atraso na entrega",
    item_faltando: "Item ausente ou errado",
    qualidade_temperatura: "Qualidade ou temperatura da comida",
    embalagem: "Embalagem inadequada ou com vazamento",
    atendimento: "Atendimento do restaurante",
  });
  const RECOMENDACOES = Object.freeze({
    atraso: "Investigar o gargalo nos horários das reclamações, sem presumir a causa",
    item_faltando: "Criar checklist de conferência antes de fechar a sacola",
    qualidade_temperatura: "Revisar o preparo ou a conservação do prato citado",
    embalagem: "Testar melhoria da embalagem do item citado",
    atendimento: "Orientar a equipe responsável",
  });
  const NOMES_SEM_ACAO = Object.freeze({
    entregador: "Entregador",
  });

  const SELOS = Object.freeze({
    pendente: { texto: "Decidida", classe: "selo-pendente" },
    em_andamento: { texto: "Em andamento", classe: "selo-andamento" },
    concluida: { texto: "Concluída", classe: "selo-concluida" },
  });

  const AVISO_SIMULADO = "Dados simulados · Projeto independente, sem vínculo com o iFood";

  const FORMATO_TOKEN = /^[A-Za-z0-9_-]{43}$/;
  const FUSO = "America/Sao_Paulo";
  const FORMATO_DIA = new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO, day: "2-digit", month: "2-digit" });
  const FORMATO_HORA = new Intl.DateTimeFormat("pt-BR", {
    timeZone: FUSO, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });

  const app = document.getElementById("app");
  const telaLinkInvalido = document.getElementById("tela-link-invalido");

  const estado = { cards: null, historico: null, semana: null, tela: "semana", folha: null };

  // ---------- utilitários ----------

  function tokenDoEndereco() {
    const partes = window.location.pathname.split("/");
    return partes.length === 3 && partes[1] === "painel" && FORMATO_TOKEN.test(partes[2]) ? partes[2] : null;
  }

  const token = tokenDoEndereco();
  const BASE = token === null ? null : "/api/painel/" + encodeURIComponent(token);

  /** Cria um elemento. O texto entra sempre por textContent. */
  function el(tag, opcoes, filhos) {
    const no = document.createElement(tag);
    const o = opcoes || {};
    if (o.classe) no.className = o.classe;
    if (o.texto !== undefined && o.texto !== null) no.textContent = String(o.texto);
    if (o.attrs) {
      for (const [nome, valor] of Object.entries(o.attrs)) no.setAttribute(nome, String(valor));
    }
    for (const filho of filhos || []) {
      if (filho) no.append(filho);
    }
    return no;
  }

  function botao(texto, classe, aoClicar, attrs) {
    const b = el("button", { classe, texto, attrs: Object.assign({ type: "button" }, attrs || {}) });
    b.addEventListener("click", aoClicar);
    return b;
  }

  function plural(n, um, varios) {
    return n === 1 ? um : varios;
  }

  function numero(n) {
    return Number(n).toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  }

  /** ISO da API -> Date. O servidor manda microssegundos; o formato de data do JS só garante 3 casas. */
  function paraData(iso) {
    return new Date(String(iso).replace(/(\.\d{3})\d+/, "$1"));
  }

  function diaMes(iso) {
    return FORMATO_DIA.format(paraData(iso));
  }

  function horaMinuto(iso) {
    return FORMATO_HORA.format(paraData(iso));
  }

  function nomeDoProblema(categoria) {
    return PROBLEMAS[categoria] || NOMES_SEM_ACAO[categoria] || categoria;
  }

  let proximoId = 0;
  function novoId(prefixo) {
    proximoId += 1;
    return prefixo + "-" + proximoId;
  }

  /** Chama a API. Lança erro em falha de rede. O corpo só é lido nas respostas de sucesso e quando
      `lerCorpo` é verdadeiro: o POST e o PATCH decidem só pelo status e recarregam os dados. */
  async function pedir(metodo, caminho, corpo, lerCorpo = true) {
    const opcoes = {
      method: metodo,
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      headers: { Accept: "application/json" },
    };
    if (corpo !== undefined) {
      opcoes.headers["Content-Type"] = "application/json";
      opcoes.body = JSON.stringify(corpo);
    }
    const resposta = await fetch(BASE + caminho, opcoes);
    const dados = resposta.ok && lerCorpo ? await resposta.json() : null;
    return { status: resposta.status, dados };
  }

  // ---------- carga e navegação ----------

  async function carregar(aposDesenhar) {
    if (BASE === null) {
      mostrarLinkInvalido();
      return;
    }
    // Depois da primeira carga, a semana fica fixa: se a semana virar com a página aberta, o card que o
    // dono acabou de decidir não some da tela.
    const caminhoCards = estado.semana === null ? "/cards" : "/cards?semana=" + encodeURIComponent(estado.semana);
    let cards;
    let historico;
    try {
      [cards, historico] = await Promise.all([pedir("GET", caminhoCards), pedir("GET", "/historico")]);
    } catch (erro) {
      mostrarFalhaDeCarga();
      return;
    }
    if (cards.status === 404 || historico.status === 404) {
      mostrarLinkInvalido();
      return;
    }
    if (cards.dados === null || historico.dados === null) {
      mostrarFalhaDeCarga();
      return;
    }
    estado.cards = cards.dados;
    estado.historico = historico.dados;
    estado.semana = cards.dados.periodo.inicio.slice(0, 10); // segunda-feira local (AAAA-MM-DD)
    try {
      desenhar();
    } catch (erro) {
      mostrarFalhaDeCarga(); // nunca deixar a página parada em "Lendo as avaliações…"
      return;
    }
    if (aposDesenhar) aposDesenhar();
  }

  function mostrarCarregando() {
    app.replaceChildren(el("p", { classe: "carregando", texto: "Lendo as avaliações…", attrs: { role: "status" } }));
  }

  function mostrarFalhaDeCarga() {
    const titulo = el("h1", { classe: "titulo", texto: "Não consegui carregar. Tente de novo.", attrs: { tabindex: "-1" } });
    app.replaceChildren(el("div", { classe: "falha-carga", attrs: { role: "alert" } }, [
      el("p", { classe: "aviso-simulado", texto: AVISO_SIMULADO }),
      titulo,
      botao("Tentar de novo", "botao botao-principal", () => {
        mostrarCarregando();
        carregar();
      }),
    ]));
    titulo.focus();
  }

  function mostrarLinkInvalido() {
    fecharFolha(false);
    app.replaceChildren();
    app.hidden = true;
    telaLinkInvalido.hidden = false;
    document.getElementById("titulo-link-invalido").focus();
  }

  function irPara(tela) {
    estado.tela = tela;
    desenhar();
    window.scrollTo(0, 0);
    const titulo = app.querySelector("h1");
    if (titulo) titulo.focus();
  }

  function desenhar() {
    if (estado.tela === "outros") app.replaceChildren(...telaOutrosTemas());
    else if (estado.tela === "anteriores") app.replaceChildren(...telaSemanasAnteriores());
    else if (estado.cards.cards.length === 0) app.replaceChildren(...telaSemanaTranquila());
    else app.replaceChildren(...telaSemana());
  }

  /** Ação do histórico pelo id (os cards só trazem id e estado; nota e datas vêm daqui). */
  function acaoDoHistorico(id) {
    for (const semana of estado.historico.semanas) {
      for (const acao of semana.acoes) {
        if (acao.id === id) return acao;
      }
    }
    return null;
  }

  // ---------- peças comuns ----------

  function cabecalho(titulo, subtitulo, extra) {
    return el("header", { classe: "cabecalho" }, [
      el("p", { classe: "aviso-simulado", texto: AVISO_SIMULADO }),
      el("h1", { classe: "titulo", texto: titulo, attrs: { tabindex: "-1" } }),
      subtitulo ? el("p", { classe: "subtitulo", texto: subtitulo }) : null,
      extra || null,
    ]);
  }

  function linhaUltimaLeitura() {
    const iso = estado.cards.ultima_leitura;
    const texto = iso
      ? "Última leitura: " + diaMes(iso) + ", às " + horaMinuto(iso)
      : "Última leitura: nenhuma avaliação recebida ainda";
    return el("p", { classe: "ultima-leitura", texto });
  }

  /** "Como o Radar leu esta semana": o texto técnico do servidor fica atrás de um toque, fechado a cada carga. */
  function detalheDeCobertura() {
    const id = novoId("cobertura");
    const texto = el("p", { classe: "cobertura", texto: estado.cards.cobertura.texto, attrs: { id } });
    texto.hidden = true;
    const b = el("button", {
      classe: "link link-centro", texto: "Como o Radar leu esta semana",
      attrs: { type: "button", "aria-expanded": "false", "aria-controls": id },
    });
    b.addEventListener("click", () => {
      const abrir = texto.hidden;
      texto.hidden = !abrir;
      b.setAttribute("aria-expanded", String(abrir));
    });
    return el("div", { classe: "detalhe-cobertura" }, [b, texto]);
  }

  /** Faixa de atenção acima dos cartões quando a semana tem menos avaliações que o mínimo. */
  function avisoDeAmostraPequena(total) {
    if (total >= AMOSTRA_MINIMA) return null;
    return el("p", {
      classe: "aviso-amostra", attrs: { role: "note" },
      texto: "Li só " + total + " " + plural(total, "avaliação", "avaliações") + ", menos das " + AMOSTRA_MINIMA
        + " que preciso para ter segurança. Trate o que aparece abaixo como sinal, não como certeza.",
    });
  }

  function cartaoLink(titulo, subtitulo, aoClicar) {
    const textos = el("span", { classe: "cartao-link-textos" }, [
      el("span", { classe: "cartao-link-titulo", texto: titulo }),
      subtitulo ? el("span", { classe: "cartao-link-sub", texto: subtitulo }) : null,
    ]);
    const b = el("button", { classe: "cartao-link", attrs: { type: "button" } }, [
      textos,
      el("span", { classe: "cartao-link-seta", texto: "→", attrs: { "aria-hidden": "true" } }),
    ]);
    b.addEventListener("click", aoClicar);
    return b;
  }

  function linkVoltar() {
    return el("div", { classe: "voltar" }, [
      botao("← Voltar para a semana", "link", () => irPara("semana")),
    ]);
  }

  function selo(texto, classe) {
    return el("span", { classe: "selo " + classe, texto });
  }

  function seloDoEstado(estadoAcao) {
    const s = SELOS[estadoAcao] || { texto: estadoAcao, classe: "selo-pendente" };
    return selo(s.texto, s.classe);
  }

  function linhaNota(nota) {
    if (!nota) return null;
    return el("p", { classe: "nota" }, [el("strong", { texto: "Sua nota: " }), document.createTextNode(nota)]);
  }

  /** Bloco que abre e fecha uma lista (somente leitura). */
  function alternador(textoFechado, textoAberto, conteudo) {
    const id = novoId("lista");
    conteudo.id = id;
    conteudo.hidden = true;
    const b = botao(textoFechado, "link", () => {
      const abrir = conteudo.hidden;
      conteudo.hidden = !abrir;
      b.setAttribute("aria-expanded", String(abrir));
      b.textContent = abrir ? textoAberto : textoFechado;
    }, { "aria-expanded": "false", "aria-controls": id });
    return b;
  }

  function relato(trecho, critica) {
    return el("div", { classe: "relato" }, [
      critica ? selo("relato a verificar", "selo-verificar") : null,
      el("blockquote", { classe: "relato-texto", texto: "“" + trecho + "”" }),
    ]);
  }

  // ---------- 01 Semana e 03 Card com ação ----------

  function telaSemana() {
    const d = estado.cards;
    const cards = d.cards;
    const n = cards.length;
    const decididos = cards.filter((c) => c.acao !== null).length;
    const tudo = decididos === n;
    const total = d.cobertura.total_lido;

    const titulo = tudo
      ? "Tudo decidido por esta semana. Bom trabalho!"
      : "Olá! Esta semana, " + n + " " + plural(n, "coisa merece", "coisas merecem") + " a sua atenção.";
    let subtitulo;
    if (tudo) {
      subtitulo = n === 1
        ? "Você tomou uma decisão para o problema desta semana."
        : "Você tomou uma decisão para cada um dos " + n + " problemas.";
    } else {
      subtitulo = "Li " + total + " " + plural(total, "avaliação", "avaliações") + " de " + d.periodo.rotulo + "."
        + (n > 1 ? " Comece pelo primeiro item: é o que mais pesa." : "");
    }

    const segmentos = cards.map((c) => el("span", {
      classe: "progresso-segmento" + (c.acao !== null ? " feito" : ""),
    }));
    const progresso = el("div", { classe: "progresso" }, [
      el("div", { classe: "progresso-barra", attrs: { "aria-hidden": "true" } }, segmentos),
      el("p", {
        classe: "progresso-texto" + (tudo ? " completo" : ""),
        texto: decididos + " de " + n + " " + plural(decididos, "decidido", "decididos"),
      }),
    ]);

    const partes = [cabecalho(titulo, subtitulo, progresso)];
    const aviso = avisoDeAmostraPequena(total);
    if (aviso) partes.push(aviso); // sem faixa com a amostra suficiente
    cards.forEach((card, i) => partes.push(cartaoDoCard(card, i + 1, n)));
    partes.push(rodapeDaSemana());
    return partes;
  }

  function cartaoDoCard(card, posicao, total) {
    const tituloId = "card-" + card.categoria;
    const acao = card.acao;
    const doHistorico = acao ? acaoDoHistorico(acao.id) : null;
    const estadoAcao = acao ? acao.estado : null;
    const concluida = estadoAcao === "concluida";

    let seloDoCard = null;
    if (acao) seloDoCard = seloDoEstado(estadoAcao);
    else if (card.tendencia_em_alta) seloDoCard = selo("Piorou esta semana", "selo-atencao");

    const frase = el("p", { classe: "card-frase" }, [
      el("strong", { texto: numero(card.taxa_por_100) + " em cada 100 avaliações" }),
      document.createTextNode(
        " citam este problema" + (card.tendencia_em_alta ? ", mais do que na semana passada." : "."),
      ),
    ]);

    let caixa;
    if (!acao) {
      caixa = el("div", { classe: "caixa caixa-sugestao" }, [
        el("p", { classe: "rotulo", texto: "O que fazer" }),
        el("p", { classe: "caixa-texto", texto: card.recomendacao }),
      ]);
    } else {
      const quando = concluida && doHistorico && doHistorico.concluida_em
        ? "Concluída em " + diaMes(doHistorico.concluida_em) + ", às " + horaMinuto(doHistorico.concluida_em)
        : null;
      caixa = el("div", { classe: "caixa caixa-ok" }, [
        el("p", { classe: "rotulo", texto: concluida ? "Você concluiu" : "Você decidiu" }),
        el("p", { classe: "caixa-texto", texto: card.recomendacao }),
        quando ? el("p", { classe: "caixa-data", texto: quando }) : null,
      ]);
    }

    const erro = el("div", { classe: "erro", attrs: { role: "alert" } });
    erro.hidden = true;

    const artigo = el("article", { classe: "card", attrs: { "aria-labelledby": tituloId } }, [
      el("div", { classe: "card-topo" }, [
        el("p", { classe: "rotulo", texto: "Problema " + posicao + " de " + total }),
        seloDoCard,
      ]),
      el("div", { classe: "card-cabeca" }, [
        el("h2", { classe: "card-titulo", texto: card.problema, attrs: { id: tituloId, tabindex: "-1" } }),
        frase,
      ]),
      caixa,
      doHistorico ? linhaNota(doHistorico.nota) : null,
      concluida ? null : relatosDoCard(card),
      erro,
      acoesDoCard(card, acao, doHistorico, erro),
    ]);
    return artigo;
  }

  function relatosDoCard(card) {
    const evidencias = card.evidencias;
    if (evidencias.length === 0) return null;
    const [primeira, ...demais] = evidencias;
    const filhos = [
      el("p", { classe: "rotulo", texto: "O que os clientes disseram" }),
      relato(primeira.trecho, primeira.critica),
    ];
    if (demais.length > 0) {
      const lista = el("div", { classe: "relatos-lista" },
        demais.map((e) => el("div", { classe: "relato-extra" }, [relato(e.trecho, e.critica)])));
      const texto = "Ver mais " + demais.length + " " + plural(demais.length, "relato", "relatos") + " →";
      filhos.push(lista);
      filhos.splice(2, 0, alternador(texto, "Ocultar relatos", lista));
    }
    return el("div", { classe: "relatos" }, filhos);
  }

  function acoesDoCard(card, acao, doHistorico, erro) {
    if (!acao) {
      const b = botao("Vou fazer isso", "botao botao-principal", () => abrirRegistro(card, b));
      return el("div", { classe: "acoes-card" }, [
        b,
        el("p", { classe: "apoio", texto: "O Radar só sugere. A decisão é sua, e você acompanha o andamento por aqui." }),
      ]);
    }
    const filhos = [];
    if (acao.estado !== "concluida") {
      const concluir = botao("Marcar como concluída", "botao botao-contorno", () => concluirAcao(card, acao, concluir, erro));
      filhos.push(concluir);
    }
    const editar = botao("Editar nota", "botao-texto botao-texto-acao", () => abrirEdicaoDeNota(card, acao, doHistorico, editar));
    filhos.push(editar);
    if (acao.estado !== "concluida" && doHistorico && doHistorico.criada_em) {
      filhos.push(el("p", {
        classe: "apoio", texto: "Combinado! Você registrou esta ação em " + diaMes(doHistorico.criada_em) + ".",
      }));
    }
    return el("div", { classe: "acoes-card" }, filhos);
  }

  /** Temas da tela 04. Os sem ação vêm sempre na resposta (até com 0 menções); só os mencionados aparecem. */
  function temasDaSemana() {
    const d = estado.cards;
    return { outros: d.outros_temas, semAcao: d.temas_sem_acao.filter((t) => t.mencoes > 0) };
  }

  /** Cartão "Outros N temas" do rodapé, ou null quando não há tema para mostrar. */
  function cartaoOutrosTemas(comNomes) {
    const { outros, semAcao } = temasDaSemana();
    const todos = outros.concat(semAcao);
    if (todos.length === 0) return null;
    const titulo = todos.length === 1 ? "Outro tema" : "Outros " + todos.length + " temas";
    const nomes = comNomes ? todos.map((t) => nomeDoProblema(t.categoria)).join(", ") : null;
    return cartaoLink(titulo, nomes, () => irPara("outros"));
  }

  function rodapeDaSemana() {
    const filhos = [cartaoOutrosTemas(true)];
    filhos.push(cartaoLink("Semanas anteriores", null, () => irPara("anteriores")));
    filhos.push(detalheDeCobertura());
    filhos.push(linhaUltimaLeitura());
    return el("footer", { classe: "rodape" }, filhos);
  }

  // ---------- 06 Semana tranquila ----------

  function telaSemanaTranquila() {
    const d = estado.cards;
    const total = d.cobertura.total_lido;
    const pouco = total < AMOSTRA_MINIMA;
    const partes = [];
    if (pouco) {
      partes.push(cabecalho(
        "Nada para decidir esta semana",
        "Li " + total + " " + plural(total, "avaliação", "avaliações") + ". Preciso de pelo menos "
          + AMOSTRA_MINIMA + " para apontar um problema com segurança.",
      ));
      const medidorTexto = total + " de " + AMOSTRA_MINIMA;
      partes.push(el("section", { classe: "card", attrs: { "aria-labelledby": "por-que" } }, [
        el("div", { classe: "medidor" }, [
          el("progress", { attrs: { max: AMOSTRA_MINIMA, value: total, "aria-label": medidorTexto + " avaliações" } }),
          el("span", { classe: "medidor-texto", texto: medidorTexto, attrs: { "aria-hidden": "true" } }),
        ]),
        el("h2", { classe: "card-subtitulo", texto: "Por que não mostro problemas?", attrs: { id: "por-que" } }),
        el("p", {
          classe: "card-texto",
          texto: "Com poucas avaliações, uma reclamação isolada parece um padrão. Prefiro esperar a próxima "
            + "leitura a te mandar decidir em cima de pouco dado.",
        }),
      ]));
    } else {
      partes.push(cabecalho(
        "Nada para decidir esta semana",
        "Li " + total + " " + plural(total, "avaliação", "avaliações") + " de " + d.periodo.rotulo + ".",
      ));
    }
    const filhos = [cartaoOutrosTemas(false)];
    const anteriores = el("button", { classe: "cartao-link cartao-link-centro", texto: "Ver semanas anteriores",
      attrs: { type: "button" } });
    anteriores.addEventListener("click", () => irPara("anteriores"));
    filhos.push(anteriores, detalheDeCobertura(), linhaUltimaLeitura());
    partes.push(el("footer", { classe: "rodape" }, filhos));
    return partes;
  }

  // ---------- 04 Outros temas ----------

  function telaOutrosTemas() {
    const { outros, semAcao } = temasDaSemana();
    const partes = [
      linkVoltar(),
      cabecalho(
        "Outros temas",
        "Apareceram nas avaliações desta semana, mas ficaram fora dos problemas principais. Só para você saber.",
      ),
    ];
    if (outros.length > 0) {
      partes.push(el("ul", { classe: "lista-temas" }, outros.map((t) => el("li", { classe: "tema" }, [
        el("span", { texto: nomeDoProblema(t.categoria) }),
        el("span", { classe: "tema-mencoes", texto: t.mencoes + " " + plural(t.mencoes, "menção", "menções") }),
      ]))));
    }
    for (const t of semAcao) {
      partes.push(el("section", { classe: "card card-atencao", attrs: { "aria-label": "Sem ação sugerida" } }, [
        el("p", { classe: "rotulo", texto: "Sem ação sugerida" }),
        el("div", { classe: "card-compacto-cabeca" }, [
          el("h2", { classe: "card-compacto-titulo", texto: nomeDoProblema(t.categoria) }),
          el("span", { classe: "tema-mencoes", texto: t.mencoes + " " + plural(t.mencoes, "menção", "menções") }),
        ]),
        el("p", { texto: "O Radar mostra este tema, mas não sugere ação." }),
      ]));
    }
    if (outros.length === 0 && semAcao.length === 0) {
      partes.push(el("p", { classe: "vazio", texto: "Nenhum outro tema apareceu nesta semana." }));
    }
    partes.push(el("footer", { classe: "rodape" }, [linhaUltimaLeitura()]));
    return partes;
  }

  // ---------- 05 Semanas anteriores ----------

  function telaSemanasAnteriores() {
    const semanas = estado.historico.semanas;
    const partes = [
      linkVoltar(),
      cabecalho("Semanas anteriores", "O que você decidiu nas últimas semanas."),
    ];
    if (semanas.length === 0) {
      partes.push(el("p", { classe: "vazio", texto: "Você ainda não registrou ações." }));
      return partes;
    }
    for (const semana of semanas) {
      partes.push(el("h2", { classe: "rotulo secao-titulo", texto: "Semana de " + semana.rotulo }));
      for (const acao of semana.acoes) partes.push(cartaoDoHistorico(acao));
    }
    partes.push(el("footer", { classe: "rodape" }, [
      el("p", { classe: "ultima-leitura", texto: "Mostrando até 12 semanas com ação registrada" }),
    ]));
    return partes;
  }

  function cartaoDoHistorico(acao) {
    const filhos = [
      el("div", { classe: "card-compacto-cabeca" }, [
        el("h3", { classe: "card-compacto-titulo", texto: nomeDoProblema(acao.categoria) }),
        seloDoEstado(acao.estado),
      ]),
      RECOMENDACOES[acao.categoria] ? el("p", { texto: RECOMENDACOES[acao.categoria] }) : null,
      linhaNota(acao.nota),
    ];
    const n = acao.evidencias.length;
    if (n > 0) {
      const lista = el("ul", { classe: "relatos-lista" }, acao.evidencias.map((e) => {
        const original = el("p", { classe: "texto-original", texto: e.texto_original });
        return el("li", { classe: "relato-extra" }, [
          relato(e.trecho_exibido, false),
          alternador("Ver texto completo", "Ocultar texto completo", original),
          original,
        ]);
      }));
      filhos.push(alternador("Ver os " + n + " " + plural(n, "relato", "relatos") + " →", "Ocultar relatos", lista), lista);
    }
    return el("article", { classe: "card card-compacto" }, filhos);
  }

  // ---------- 02 Registrar ação, edição de nota e 08 Erro ao salvar ----------

  /** Abre a folha (role=dialog). O fundo fica inerte; Esc fecha; o foco volta ao botão de origem. */
  function abrirFolha(conteudo, gatilho) {
    fecharFolha(false);
    const fundo = el("div", { classe: "folha-fundo" });
    const folha = el("div", {
      classe: "folha", attrs: { role: "dialog", "aria-modal": "true", "aria-labelledby": "folha-titulo" },
    }, [el("div", { classe: "pegador", attrs: { "aria-hidden": "true" } })].concat(conteudo));
    fundo.addEventListener("click", () => {
      if (!estado.folha.enviando) fecharFolha(true);
    });
    app.inert = true;
    document.body.classList.add("com-folha");
    document.body.append(fundo, folha);
    estado.folha = { fundo, folha, gatilho, enviando: false };
    document.getElementById("folha-titulo").focus();
  }

  function fecharFolha(devolverFoco) {
    const f = estado.folha;
    if (!f) return;
    f.fundo.remove();
    f.folha.remove();
    estado.folha = null;
    app.inert = false;
    document.body.classList.remove("com-folha");
    if (devolverFoco && f.gatilho && f.gatilho.isConnected) f.gatilho.focus();
  }

  document.addEventListener("keydown", (evento) => {
    if (evento.key === "Escape" && estado.folha && !estado.folha.enviando) {
      evento.preventDefault();
      fecharFolha(true);
    }
  });

  function campoNota(rotulo, valorInicial) {
    const idCampo = novoId("nota");
    const idContador = novoId("contador");
    const area = el("textarea", {
      attrs: { id: idCampo, maxlength: LIMITE_NOTA, rows: "3", "aria-describedby": idContador },
    });
    area.value = valorInicial || "";
    const contador = el("p", { classe: "contador", texto: area.value.length + " / " + LIMITE_NOTA, attrs: { id: idContador } });
    area.addEventListener("input", () => {
      contador.textContent = area.value.length + " / " + LIMITE_NOTA;
    });
    const bloco = el("div", { classe: "campo" }, [
      el("label", { classe: "rotulo", texto: rotulo, attrs: { for: idCampo } }),
      area,
      contador,
    ]);
    return { bloco, area };
  }

  function faixaDeErro(titulo, texto) {
    const faixa = el("div", { classe: "erro", attrs: { role: "alert" } });
    faixa.hidden = true;
    faixa.dataset.titulo = titulo;
    faixa.dataset.texto = texto;
    return faixa;
  }

  function mostrarErro(faixa) {
    faixa.replaceChildren(el("strong", { texto: faixa.dataset.titulo }), el("span", { texto: faixa.dataset.texto }));
    faixa.hidden = false;
  }

  function cabecaDaFolha(rotulo, titulo) {
    return el("div", { classe: "folha-cabeca" }, [
      el("p", { classe: "rotulo", texto: rotulo }),
      el("h2", { classe: "folha-titulo", texto: titulo, attrs: { id: "folha-titulo", tabindex: "-1" } }),
    ]);
  }

  /** Envia e trata a resposta como a especificação manda. Enquanto envia, nada na folha responde a toque. */
  async function enviarDaFolha({ metodo, caminho, corpo, botaoEnviar, cancelar, faixa, categoria }) {
    estado.folha.enviando = true;
    botaoEnviar.disabled = true;
    cancelar.disabled = true;
    botaoEnviar.textContent = "Salvando…";
    let resposta = null;
    try {
      resposta = await pedir(metodo, caminho, corpo, false);
    } catch (erro) {
      resposta = null;
    }
    if (!estado.folha) return; // a folha já foi fechada por outra resposta (por exemplo, a tela 07)
    if (resposta && (resposta.status === 200 || resposta.status === 201 || resposta.status === 409)) {
      // 409: a ação já existe ou o estado já mudou; recarrega e mostra o estado real, sem erro.
      fecharFolha(false);
      await carregar(() => focarCard(categoria));
      return;
    }
    if (resposta && resposta.status === 404) {
      if (metodo === "POST") {
        mostrarLinkInvalido();
      } else {
        // no PATCH, 404 pode ser a ação: recarregar decide (link inválido também dá 404 no /cards)
        fecharFolha(false);
        await carregar(() => focarCard(categoria));
      }
      return;
    }
    // 422, 500 ou falha de rede: nada de ler o corpo; a nota digitada fica no campo.
    estado.folha.enviando = false;
    mostrarErro(faixa);
    botaoEnviar.disabled = false;
    cancelar.disabled = false;
    botaoEnviar.textContent = "Tentar de novo";
    botaoEnviar.focus(); // o botão desabilitado perdeu o foco; ele volta para dentro da folha
  }

  function focarCard(categoria) {
    const titulo = document.getElementById("card-" + categoria);
    if (titulo) titulo.focus();
  }

  function abrirRegistro(card, gatilho) {
    const n = card.evidencias.length;
    const faixa = faixaDeErro("Não foi possível salvar.", "Nada foi registrado. Confira sua conexão e tente de novo.");
    const lista = el("div", { classe: "relatos-lista" },
      card.evidencias.map((e) => el("div", { classe: "relato-extra" }, [relato(e.trecho, e.critica)])));
    const nota = campoNota("Quer anotar como vai fazer? (opcional)", "");
    nota.area.setAttribute("placeholder", "Ex.: quem cuida disso e quando");
    const confirmar = el("button", { classe: "botao botao-principal", texto: "Confirmar ação", attrs: { type: "button" } });
    const cancelar = botao("Cancelar", "botao-texto", () => fecharFolha(true));

    confirmar.addEventListener("click", () => {
      const corpo = {
        categoria: card.categoria,
        semana_inicio: estado.cards.periodo.inicio.slice(0, 10), // segunda-feira local (AAAA-MM-DD)
        evidencias: card.evidencias.map((e) => ({ avaliacao_id: e.avaliacao_id, posicao: e.posicao })),
      };
      if (nota.area.value.trim() !== "") corpo.nota = nota.area.value;
      enviarDaFolha({
        metodo: "POST", caminho: "/acoes", corpo, botaoEnviar: confirmar, cancelar, faixa, categoria: card.categoria,
      });
    });

    abrirFolha([
      faixa,
      cabecaDaFolha("Registrar ação", card.problema),
      el("div", { classe: "caixa caixa-sugestao" }, [
        el("p", { classe: "rotulo", texto: "Você vai fazer" }),
        el("p", { classe: "caixa-texto", texto: card.recomendacao }),
      ]),
      el("div", { classe: "relatos" }, [
        el("p", { classe: "rotulo", texto: "Baseado em " + n + " " + plural(n, "avaliação", "avaliações") + " desta semana" }),
        alternador("Ver " + plural(n, "o relato", "os " + n + " relatos") + " →", "Ocultar relatos", lista),
        lista,
      ]),
      nota.bloco,
      confirmar,
      cancelar,
    ], gatilho);
  }

  function abrirEdicaoDeNota(card, acao, doHistorico, gatilho) {
    const faixa = faixaDeErro("A mudança não foi salva.", "Confira sua conexão e tente de novo.");
    const nota = campoNota("Sua nota (opcional)", doHistorico ? doHistorico.nota : "");
    const salvar = el("button", { classe: "botao botao-principal", texto: "Salvar nota", attrs: { type: "button" } });
    const cancelar = botao("Cancelar", "botao-texto", () => fecharFolha(true));
    salvar.addEventListener("click", () => {
      enviarDaFolha({
        metodo: "PATCH", caminho: "/acoes/" + encodeURIComponent(acao.id), corpo: { nota: nota.area.value },
        botaoEnviar: salvar, cancelar, faixa, categoria: card.categoria,
      });
    });
    abrirFolha([
      faixa,
      cabecaDaFolha("Editar nota", card.problema),
      el("div", { classe: "caixa caixa-ok" }, [
        el("p", { classe: "rotulo", texto: acao.estado === "concluida" ? "Você concluiu" : "Você decidiu" }),
        el("p", { classe: "caixa-texto", texto: card.recomendacao }),
      ]),
      nota.bloco,
      salvar,
      cancelar,
    ], gatilho);
  }

  async function concluirAcao(card, acao, botaoConcluir, faixa) {
    const textoOriginal = botaoConcluir.textContent;
    botaoConcluir.disabled = true;
    botaoConcluir.textContent = "Salvando…";
    faixa.hidden = true;
    let resposta = null;
    try {
      resposta = await pedir("PATCH", "/acoes/" + encodeURIComponent(acao.id), { estado: "concluida" }, false);
    } catch (erro) {
      resposta = null;
    }
    if (resposta && [200, 404, 409].includes(resposta.status)) {
      // 409: estado não volta atrás; 404: a ação ou o link. Recarregar mostra o estado real (ou a tela 07).
      await carregar(() => focarCard(card.categoria));
      return;
    }
    faixa.replaceChildren(
      el("strong", { texto: "A mudança não foi salva." }),
      el("span", { texto: "Confira sua conexão e tente de novo." }),
    );
    faixa.hidden = false;
    botaoConcluir.disabled = false;
    botaoConcluir.textContent = textoOriginal;
    botaoConcluir.focus();
  }

  // ---------- início ----------

  carregar();
})();
