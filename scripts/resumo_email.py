"""Gera o resumo semanal (HTML) da execução orçamentária para envio por e-mail.

Uso: python scripts/resumo_email.py painel/dados/execucao.json [anterior.json] \
        --link https://usuario.github.io/painel-orcamento-maringa/ --saida resumo.html
Lê o formato combinado (meta, despesas, receitas). Se o arquivo anterior for dado,
mostra quanto foi empenhado/pago a mais desde a última atualização.
"""
import argparse, datetime as dt, html, json, os, sys
from collections import defaultdict


def carregar(caminho):
    if not caminho or not os.path.exists(caminho):
        return None
    with open(caminho, encoding="utf-8") as f:
        texto = f.read().strip()
    if texto.startswith("window."):  # aceita também o execucao.js
        texto = texto.split("=", 1)[1].strip().rstrip(";")
    return json.loads(texto)


# Dotação e previsão são saldos: vale a linha do último mês de cada combinação. A combinação
# precisa ser a mesma da coleta (DIM_DESPESA_PAINEL / DIM_RECEITA_PAINEL em coleta/coletar.py)
# e do painel; se faltar uma dimensão (ex.: modalidade de aplicação), linhas diferentes caem na
# mesma chave, uma apaga a outra e a dotação fica menor do que a real. Campos ausentes nas
# bases antigas viram None e não alteram o resultado.
CHAVE_DESPESA = ("entidade", "orgao", "funcao", "subfuncao", "programa", "categoria", "grupo",
                 "modalidade_aplicacao", "elemento", "fonte_recurso")
CHAVE_RECEITA = ("entidade", "categoria", "origem", "fonte_recurso", "intra_orcamentaria")


def totais(dados):
    desp = dados.get("despesas") or []
    if not desp:
        return None
    ano = max(d["ano"] for d in desp)
    linhas = [d for d in desp if d["ano"] == ano]
    mes = max(d["mes"] for d in linhas)
    chave = lambda d: tuple(d.get(k) for k in CHAVE_DESPESA)
    ultima = {}
    for d in sorted(linhas, key=lambda d: d["mes"]):
        ultima[chave(d)] = d  # dotação = posição do último mês de cada combinação
    t = {"ano": ano, "mes": mes,
         "dotacao": sum(d.get("dotacao_atualizada") or 0 for d in ultima.values())}
    for k in ("empenhado", "liquidado", "pago"):
        t[k] = sum(d.get(k) or 0 for d in linhas)
    por_orgao = defaultdict(lambda: [0.0, 0.0])
    for d in linhas:
        por_orgao[d.get("orgao") or "—"][1] += d.get("empenhado") or 0
    for d in ultima.values():
        por_orgao[d.get("orgao") or "—"][0] += d.get("dotacao_atualizada") or 0
    t["orgaos"] = sorted(por_orgao.items(), key=lambda x: -x[1][1])[:5]
    rec = [r for r in (dados.get("receitas") or []) if r["ano"] == ano]
    if rec:
        ult_r = {}
        for r in sorted(rec, key=lambda r: r["mes"]):
            ult_r[tuple(r.get(k) for k in CHAVE_RECEITA)] = r
        t["previsto"] = sum(r.get("previsto") or 0 for r in ult_r.values())
        t["arrecadado"] = sum(r.get("arrecadado") or 0 for r in rec)
    return t


def brl(v):
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$&nbsp;{s}"


def pct_num(a, b):
    return 100 * a / b if b else 0.0


def pct(a, b):
    return f"{pct_num(a, b):.1f}%".replace(".", ",") if b else "—"


MESES = "jan fev mar abr mai jun jul ago set out nov dez".split()
MESES_LONGOS = ("janeiro fevereiro março abril maio junho julho agosto setembro "
                "outubro novembro dezembro").split()

# O portal publica nomes em CAIXA ALTA e sem acento; corrigimos as palavras mais comuns.
ACENTOS = {
    "saude": "Saúde", "educacao": "Educação", "previdencia": "Previdência", "publicos": "Públicos",
    "publicas": "Públicas", "maringa": "Maringá", "agencia": "Agência", "inovacao": "Inovação",
    "regulacao": "Regulação", "camara": "Câmara", "municipio": "Município", "seguranca": "Segurança",
    "crianca": "Criança", "deficiencia": "Deficiência", "aceleracao": "Aceleração", "economica": "Econômica",
    "assistencia": "Assistência", "politica": "Política", "politicas": "Políticas", "comunicacao": "Comunicação",
    "gestao": "Gestão", "logistica": "Logística", "protecao": "Proteção", "habitacao": "Habitação",
    "administracao": "Administração", "servicos": "Serviços", "tecnologia": "Tecnologia",
}
MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "a", "o", "com", "para", "sobre", "em"}


def nome_bonito(texto):
    palavras = " ".join(str(texto).split()).split(" ")
    saida = []
    for i, p in enumerate(palavras):
        base = p.strip(",").lower()
        sufixo = "," if p.endswith(",") else ""
        if base in ACENTOS:
            w = ACENTOS[base]
        elif i > 0 and base in MINUSCULAS:
            w = base
        elif p.isupper() and len(base) <= 6 and base.isalpha() and base not in MINUSCULAS and i > 0 and palavras[i - 1] == "-":
            w = base.upper()  # siglas depois de " - ", ex.: AMR, IPPLAM
        else:
            w = "-".join(x.capitalize() for x in base.split("-"))
        saida.append(w + sufixo)
    return " ".join(saida)


def data_br(iso):
    try:
        d = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        d = d.astimezone(dt.timezone(dt.timedelta(hours=-3)))
        return f"{d.day} de {MESES_LONGOS[d.month - 1]} de {d.year}, às {d:%H:%M}"
    except Exception:
        return str(iso or "")


# Identidade visual: a mesma do painel (docs/index.html, brandbook do IPPLAM). Os nomes seguem os
# tokens CSS do painel. E-mail não aceita variáveis CSS e o Gmail ignora boa parte de <style>, por
# isso as cores ficam aqui, todo estilo vai inline e o layout usa tabelas.
COR = {
    "pagina": "#f2f2f2", "superficie": "#ffffff", "superficie2": "#f2f2f2",
    "tinta": "#1d1d1d", "tinta2": "#4f4a46", "mudo": "#77706b",
    "titulo": "#145d66", "marca": "#3aa4aa", "laranja": "#f6a63a",
    "grade": "#e6e2de", "borda": "#ddd8d3", "trilha": "#ebe8e5",
    "orcado": "#a9a19b", "emp": "#a8dadd", "liq": "#3aa4aa", "pag": "#145d66", "arr": "#3aa4aa",
    "link": "#1f7c83", "bom": "#1b7a3d", "aviso_fundo": "#fdf1de", "aviso_tinta": "#5c3d00",
}
FONTE = "font-family:Arial,Helvetica,sans-serif;"
# Jost aparece no Apple Mail e no iPhone; o Gmail não carrega fontes externas e usa Arial no lugar.
JOST = "font-family:Jost,Arial,Helvetica,sans-serif;"
CAIXA_ALTA = "text-transform:uppercase;letter-spacing:.06em;"
NR = "white-space:nowrap;"


def brl_curto(v):
    """Como o painel: R$ 2,97 bi / R$ 517,9 mi / R$ 12,3 mil."""
    a = abs(v)
    if a >= 1e9:
        s, suf = f"{v / 1e9:,.2f}", " bi"
    elif a >= 1e6:
        s, suf = f"{v / 1e6:,.1f}", " mi"
    elif a >= 1e3:
        s, suf = f"{v / 1e3:,.1f}", " mil"
    else:
        s, suf = f"{v:,.0f}", ""
    return "R$&nbsp;" + s.replace(",", "X").replace(".", ",").replace("X", ".") + suf


def medidor(p, cor, altura=6):
    """Barra de progresso como a do painel: trilha cinza-clara e preenchimento sem cantos arredondados."""
    p = max(0, min(100, round(p)))
    cel = f"height:{altura}px;font-size:0;line-height:0;"
    if p >= 100:
        partes = f"<td width='100%' style='{cel}background:{cor}'>&nbsp;</td>"
    elif p <= 0:
        partes = f"<td width='100%' style='{cel}background:{COR['trilha']}'>&nbsp;</td>"
    else:
        partes = (f"<td width='{p}%' style='{cel}background:{cor}'>&nbsp;</td>"
                  f"<td width='{100 - p}%' style='{cel}background:{COR['trilha']}'>&nbsp;</td>")
    return (f"<table role='presentation' cellpadding='0' cellspacing='0' width='100%' "
            f"style='width:100%;border-collapse:collapse'><tr>{partes}</tr></table>")


def cartao(conteudo, padding="16px"):
    return (f"<table role='presentation' cellpadding='0' cellspacing='0' width='100%' "
            f"style='width:100%;border-collapse:separate;background:{COR['superficie']};"
            f"border:1px solid {COR['borda']};border-radius:4px'>"
            f"<tr><td style='padding:{padding}'>{conteudo}</td></tr></table>")


def titulo_cartao(texto, desc=""):
    d = (f"<div style='{FONTE}font-size:14px;line-height:1.4;color:{COR['tinta2']};margin:2px 0 12px'>{desc}</div>"
         if desc else "<div style='height:10px;line-height:10px;font-size:0'>&nbsp;</div>")
    return (f"<div style='{JOST}{CAIXA_ALTA}font-size:15px;font-weight:600;color:{COR['titulo']};"
            f"letter-spacing:.07em'>{texto}</div>{d}")


def grade(blocos, largura=321):
    """Cartões lado a lado que viram uma coluna no celular, sem media query (o Gmail nem sempre lê).
    Cada bloco tem largura máxima fixa; quando a tela não comporta dois, o seguinte desce."""
    itens = "".join(
        f"<div style='display:inline-block;vertical-align:top;width:100%;max-width:{largura}px;"
        f"padding:0 5px 10px;box-sizing:border-box;font-size:16px;line-height:1.4'>{b}</div>" for b in blocos)
    return f"<div style='font-size:0;line-height:0;text-align:left'>{itens}</div>"


def bloco(conteudo, depois=10, antes=0):
    """Conteúdo de largura total, com o mesmo recuo lateral dos cartões da grade."""
    return f"<div style='padding:{antes}px 5px {depois}px'>{conteudo}</div>"


def kpi(rotulo, cor, valor, completo, sub, p=None, cor_barra=None):
    sw = (f"<span style='display:inline-block;width:10px;height:10px;background:{cor};"
          f"border-radius:3px;margin-right:6px'></span>")
    barra = medidor(p, cor_barra or cor) if p is not None else ""
    return cartao(
        f"<div style='{JOST}{CAIXA_ALTA}font-size:12.5px;font-weight:500;color:{COR['tinta2']};line-height:1.35;{NR}'>{sw}{rotulo}</div>"
        f"<div style='{JOST}font-size:26px;font-weight:500;color:{COR['tinta']};line-height:1.25;margin:4px 0 0;{NR}'>{valor}</div>"
        f"<div style='{FONTE}font-size:12px;color:{COR['mudo']};line-height:1.4;{NR}'>{completo}</div>"
        f"<div style='{FONTE}font-size:14px;color:{COR['tinta2']};line-height:1.4;margin:6px 0 {12 if barra else 0}px;{NR}'>{sub}</div>"
        f"{barra}")


def tabela(titulo_cols, linhas_html):
    cab = "".join(
        f"<th style='{JOST}{CAIXA_ALTA}background:{COR['superficie2']};color:{COR['tinta']};font-size:12px;"
        f"font-weight:500;letter-spacing:.05em;text-align:{al};padding:9px 10px;{NR}"
        f"border-bottom:1px solid {COR['grade']}'>{t}</th>" for t, al in titulo_cols)
    return (f"<table role='presentation' cellpadding='0' cellspacing='0' width='100%' "
            f"style='border-collapse:separate;border-spacing:0;border:1px solid {COR['borda']};border-radius:3px;"
            f"width:100%;background:{COR['superficie']}'><tr>{cab}</tr>{linhas_html}</table>")


def celula(conteudo, i, al="left", extra=""):
    borda = f"border-top:1px solid {COR['grade']};" if i else ""
    return (f"<td style='{FONTE}font-size:14px;color:{COR['tinta']};background:{COR['superficie']};{borda}"
            f"padding:9px 10px;text-align:{al};vertical-align:top;{extra}'>{conteudo}</td>")


def mais(v):
    return f"<span style='color:{COR['bom']};{NR}'>+{brl_curto(v)}</span>"


TRACO = f"<span style='color:{COR['orcado']}'>—</span>"


def data_curta(iso):
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    except Exception:
        return "—"


def resumir_texto(texto, limite=90):
    t = " ".join(str(texto or "").split())
    return t if len(t) <= limite else t[:limite].rsplit(" ", 1)[0] + "…"


def contador(rotulo, valor, cor):
    """Cartão pequeno de contagem (licitações), no estilo dos indicadores do painel."""
    return cartao(
        f"<div style='{JOST}{CAIXA_ALTA}font-size:11.5px;font-weight:500;color:{COR['tinta2']};line-height:1.35;"
        f"border-left:3px solid {cor};padding-left:7px;height:34px'>{rotulo}</div>"
        f"<div style='{JOST}font-size:24px;font-weight:500;color:{COR['tinta']};line-height:1.2;margin-top:6px'>{valor}</div>",
        padding="12px 14px")


def secao_licitacoes(lic):
    """Contagens da semana e as próximas aberturas."""
    if not lic or not lic.get("licitacoes"):
        return ""
    itens = lic["licitacoes"]
    mud = lic.get("mudancas") or []
    hoje = max((m.get("atualizacao") or "" for m in mud), default="") or dt.date.today().isoformat()
    inicio = (dt.date.fromisoformat(hoje) - dt.timedelta(days=6)).isoformat()
    semana = [m for m in mud if (m.get("atualizacao") or "") >= inicio]
    novas = {m["id"] for m in semana if m.get("tipo") == "Nova"}
    homolog = {m["id"] for m in semana if m.get("tipo") == "Mudou de situação" and m.get("para") == "Homologada"}
    andamento = sum(1 for l in itens if l.get("grupo") == "Em andamento")
    cont = [("Publicados no ano", f"{len(itens):,}".replace(",", "."), COR["orcado"]),
            ("Em andamento", str(andamento), COR["marca"]),
            ("Novos na semana", str(len(novas)), COR["laranja"]),
            ("Homologados na semana", str(len(homolog)), COR["titulo"])]
    t1 = grade([contador(n, v, c) for n, v, c in cont], largura=160)

    hoje_d = dt.date.today().isoformat()
    prox = sorted((l for l in itens if (l.get("abertura") or "") >= hoje_d and l.get("grupo") == "Em andamento"),
                  key=lambda l: l["abertura"])[:5]
    t2 = ""
    if prox:
        lin2 = []
        for i, l in enumerate(prox):
            obj = (f"<a href='{html.escape(l.get('link') or '')}' style='color:{COR['link']};text-decoration:none'>"
                   f"<b>{html.escape(l.get('modalidade') or '')} {html.escape(l.get('numero') or '')}</b></a>"
                   f"<div style='font-size:13px;line-height:1.4;color:{COR['tinta2']};margin-top:2px'>"
                   f"{html.escape(resumir_texto(l.get('objeto')))}</div>"
                   f"<div style='font-size:13px;color:{COR['mudo']};margin-top:2px'>Valor estimado: "
                   f"<b style='color:{COR['tinta']}'>{brl(l.get('valor_estimado') or 0)}</b></div>")
            lin2.append("<tr>" + celula(f"<b>{data_curta(l['abertura'])}</b>", i, extra=NR + "width:1%;")
                        + celula(obj, i) + "</tr>")
        t2 = bloco(cartao(titulo_cartao("Próximas aberturas", "Processos em andamento com abertura marcada.")
                          + tabela([("Abertura", "left"), ("Processo", "left")], "".join(lin2))))
    return t1 + t2


def secao_diarias(via, via_ant):
    if not via or not via.get("diarias"):
        return ""
    ano = max(v["ano"] for v in via["diarias"])
    soma = lambda lst, tipo: (sum(1 for v in lst if v["ano"] == ano and v["tipo"] == tipo),
                              sum(v.get("valor") or 0 for v in lst if v["ano"] == ano and v["tipo"] == tipo))
    ant = (via_ant or {}).get("diarias") or []
    cards = []
    for tipo, rot, cor in (("Diária", "Diárias", COR["marca"]), ("Passagem", "Passagens", COR["titulo"])):
        n, v = soma(via["diarias"], tipo)
        sub = f"{n:,}".replace(",", ".") + " registros no ano"
        if ant:
            dif = v - soma(ant, tipo)[1]
            if dif > 0.005:
                sub += f" · {mais(dif)}"
        cards.append(kpi(f"{rot} {ano}", cor, brl_curto(v), brl(v), sub))
    return grade(cards)


def eyebrow(texto, tamanho=12):
    """Rótulo com traço laranja à esquerda, como o 'Dados públicos · Maringá · Paraná' do painel."""
    return (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
            f"<td style='width:22px;padding-right:8px;vertical-align:middle'>"
            f"<div style='height:2px;line-height:2px;font-size:0;background:{COR['laranja']}'>&nbsp;</div></td>"
            f"<td style='{JOST}{CAIXA_ALTA}font-size:{tamanho}px;font-weight:600;color:{COR['tinta2']};"
            f"letter-spacing:.16em'>{texto}</td></tr></table>")


def titulo_secao(texto):
    return bloco(eyebrow(texto, 13), depois=10, antes=14)


def montar(dados, anterior, link, lic=None, via=None, via_ant=None, avisos_coleta=""):
    t = totais(dados)
    meta = dados.get("meta", {})
    if t is None:
        return ("Prefeitura de Maringá - Painel de Transparência: sem dados de despesa",
                "<p>A coleta não trouxe despesas nesta semana.</p>")
    ta = totais(anterior) if anterior else None
    ref = f"{MESES[t['mes'] - 1]}/{t['ano']}"

    def semana(campo):
        if ta and ta.get("ano") == t["ano"]:
            dif = t[campo] - ta.get(campo, 0)
            if dif > 0.005:
                return f" · {mais(dif)}"
        return ""

    # Indicadores com as mesmas cores do painel: orçado (cinza), empenhado, liquidado e pago (tons de turquesa)
    cards = [kpi("Dotação atualizada", COR["orcado"], brl_curto(t["dotacao"]), brl(t["dotacao"]),
                 "orçamento autorizado no ano", 100)]
    for rot, campo, cor in (("Empenhado", "empenhado", COR["emp"]), ("Liquidado", "liquidado", COR["liq"]),
                            ("Pago", "pago", COR["pag"])):
        cards.append(kpi(rot, cor, brl_curto(t[campo]), brl(t[campo]),
                         f"{pct(t[campo], t['dotacao'])} da dotação{semana(campo)}", pct_num(t[campo], t["dotacao"])))
    if "arrecadado" in t:
        cards.append(kpi("Receita prevista", COR["orcado"], brl_curto(t["previsto"]), brl(t["previsto"]),
                         "previsão atualizada para o ano", 100))
        cards.append(kpi("Receita arrecadada", COR["arr"], brl_curto(t["arrecadado"]), brl(t["arrecadado"]),
                         f"{pct(t['arrecadado'], t['previsto'])} do previsto", pct_num(t["arrecadado"], t["previsto"])))
    nota_semana = (bloco(f"<div style='{FONTE}font-size:13px;color:{COR['mudo']}'>Em verde, quanto cada valor "
                         f"cresceu desde o e-mail da semana passada.</div>") if "+R$" in "".join(cards) else "")
    s_kpis = grade(cards) + nota_semana

    # Órgãos: barras como as do painel, preenchidas pela parte da dotação já empenhada
    barras = []
    for i, (o, (dot, e)) in enumerate(t["orgaos"]):
        barras.append(
            f"<table role='presentation' cellpadding='0' cellspacing='0' width='100%' style='width:100%;margin-top:{0 if i == 0 else 12}px'><tr>"
            f"<td style='{FONTE}font-size:14px;color:{COR['tinta']};line-height:1.35;padding:0 10px 5px 0'>{html.escape(nome_bonito(o))}</td>"
            f"<td style='{FONTE}font-size:13px;color:{COR['tinta2']};text-align:right;vertical-align:top;{NR}padding-bottom:5px'>"
            f"<b style='color:{COR['tinta']};font-size:14px'>{brl_curto(e)}</b> · {pct(e, dot)}</td></tr>"
            f"<tr><td colspan='2'>{medidor(pct_num(e, dot), COR['liq'], 8)}</td></tr></table>")
    s_org = bloco(cartao(titulo_cartao("Órgãos com maior valor empenhado",
                                       "Valor empenhado no ano e quanto ele representa da dotação de cada órgão.")
                         + "".join(barras)))

    estilo_aviso = (f"{FONTE}background:{COR['aviso_fundo']};color:{COR['aviso_tinta']};border-left:4px solid {COR['laranja']};"
                    f"border-radius:3px;padding:10px 14px;font-size:14px;margin:12px 0 0")
    aviso = ""
    if meta.get("exemplo"):
        aviso += f"<p style='{estilo_aviso}'>Atenção: dados de exemplo, não reais.</p>"
    if avisos_coleta:
        aviso += f"<p style='{estilo_aviso}'>{html.escape(avisos_coleta)}</p>"
    s_lic = secao_licitacoes(lic)
    s_via = secao_diarias(via, via_ant)
    fonte = html.escape(str(meta.get("fonte", "Portal da Transparência de Maringá")))
    base = link if link.endswith("/") else link + "/"
    # Logo acima do título (como o painel no celular): ao lado do título ele espremia o texto em telas estreitas
    logo = (f"<a href='{html.escape(link)}' style='display:inline-block;margin:0 0 12px'><img src='{html.escape(base)}img/logo-ipplam.jpg' "
            f"width='120' alt='IPPLAM - Instituto de Pesquisa e Planejamento Urbano de Maringá' "
            f"style='display:block;width:120px;height:auto;border:1px solid {COR['borda']};padding:5px 6px;background:#ffffff'></a>"
            if link else "")
    botao = (f"<table role='presentation' cellpadding='0' cellspacing='0' style='margin-top:8px'><tr>"
             f"<td style='background:{COR['titulo']};border-radius:3px'><a href='{html.escape(link)}' "
             f"style='{JOST}{CAIXA_ALTA}display:inline-block;color:#ffffff;font-size:14px;font-weight:600;"
             f"padding:12px 22px;text-decoration:none'>Abrir o painel completo</a></td></tr></table>")
    rodape = (f"<p style='{FONTE}color:{COR['mudo']};font-size:12px;line-height:1.5;margin:16px 0 0;"
              f"border-top:1px solid {COR['borda']};padding-top:12px'>Fonte: {fonte}<br>"
              f"E-mail automático enviado toda segunda-feira pelo Painel de Transparência.</p>")
    corpo = f"""<link href="https://fonts.googleapis.com/css2?family=Jost:wght@400;500;600&display=swap" rel="stylesheet">
<div style="background:{COR['pagina']};padding:16px 0;margin:0">
<table role="presentation" align="center" cellpadding="0" cellspacing="0" width="660" style="width:660px;max-width:100%;border-collapse:collapse">
<tr><td style="padding:8px 14px 16px">
  {logo}
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="width:100%"><tr>
    <td style="border-left:6px solid {COR['marca']};padding:2px 0 2px 16px;vertical-align:top">
      {eyebrow("Dados públicos · Maringá · Paraná", 11.5)}
      <div style="{JOST}{CAIXA_ALTA}font-size:22px;line-height:1.2;font-weight:600;color:{COR['titulo']};margin:6px 0 4px">Prefeitura de Maringá - Painel de Transparência</div>
      <div style="{FONTE}font-size:15px;line-height:1.45;color:{COR['tinta2']}">Resumo semanal: valores acumulados de 1º de janeiro até {MESES_LONGOS[t['mes'] - 1]} de {t['ano']}.</div>
      <div style="{FONTE}font-size:13px;color:{COR['mudo']};margin-top:4px">Dados atualizados em {data_br(meta.get('atualizado_em'))}.</div>
    </td>
  </tr></table>
  {aviso}
</td></tr>
<tr><td style="background:{COR['titulo']};border-radius:3px 3px 0 0;padding:6px 6px 0">
  <table role="presentation" cellpadding="0" cellspacing="0"><tr>
    <td style="background:{COR['pagina']};border-top:3px solid {COR['laranja']};border-radius:2px 2px 0 0;padding:8px 14px;{JOST}{CAIXA_ALTA}font-size:13px;font-weight:600;color:{COR['titulo']};line-height:1.2">Resumo até {ref}</td>
  </tr></table>
</td></tr>
<tr><td style="padding:6px 9px 8px">
  {titulo_secao("Orçamento do ano")}
  {s_kpis}
  {s_org}
  {(titulo_secao("Licitações") + s_lic) if s_lic else ""}
  {(titulo_secao("Diárias e passagens") + s_via) if s_via else ""}
  {bloco(botao + rodape)}
</td></tr></table></div>"""
    assunto = f"Prefeitura de Maringá - Painel de Transparência até {ref}"
    return assunto, corpo


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("atual")
    ap.add_argument("anterior", nargs="?")
    ap.add_argument("--link", default="")
    ap.add_argument("--saida", default="resumo.html")
    ap.add_argument("--licitacoes", help="licitacoes.js/.json da coleta")
    ap.add_argument("--diarias", help="diarias.js/.json da coleta")
    ap.add_argument("--diarias-anterior", help="diarias.js da semana anterior")
    ap.add_argument("--aviso", default="", help="texto de aviso para o topo do e-mail")
    a = ap.parse_args()
    assunto, corpo = montar(carregar(a.atual), carregar(a.anterior), a.link,
                           carregar(a.licitacoes), carregar(a.diarias), carregar(a.diarias_anterior), a.aviso)
    with open(a.saida, "w", encoding="utf-8") as f:
        f.write(corpo)
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"assunto={assunto}\n")
    print(assunto)
