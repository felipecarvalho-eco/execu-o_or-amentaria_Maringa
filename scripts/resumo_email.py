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


def totais(dados):
    desp = dados.get("despesas") or []
    if not desp:
        return None
    ano = max(d["ano"] for d in desp)
    linhas = [d for d in desp if d["ano"] == ano]
    mes = max(d["mes"] for d in linhas)
    chave = lambda d: tuple(d.get(k) for k in ("orgao", "funcao", "subfuncao", "programa",
                                                "categoria", "grupo", "elemento", "fonte_recurso"))
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
            ult_r[(r.get("categoria"), r.get("origem"), r.get("fonte_recurso"))] = r
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


AZUL = "#1f4e79"
AZUL_CLARO = "#eaf1f8"
ZEBRA = "#f7f9fb"
BORDA = "#dfe5ec"
FONTE = "font-family:Arial,Helvetica,sans-serif;"


def barra(p, cor):
    largura = max(0, min(100, round(p)))
    return (f"<table role='presentation' cellpadding='0' cellspacing='0' width='100' style='width:100px;border-collapse:collapse'>"
            f"<tr><td style='background:{cor};height:8px;width:{largura}px;font-size:0;line-height:0'>&nbsp;</td>"
            f"<td style='background:#e6e9ee;height:8px;font-size:0;line-height:0'>&nbsp;</td></tr></table>")


def tabela(titulo_cols, linhas_html):
    cab = "".join(
        f"<th style='{FONTE}background:{AZUL};color:#ffffff;font-size:12px;font-weight:bold;text-align:{al};"
        f"padding:9px 10px;white-space:nowrap'>{t}</th>" for t, al in titulo_cols)
    return (f"<table role='presentation' cellpadding='0' cellspacing='0' width='100%' "
            f"style='border-collapse:collapse;border:1px solid {BORDA};width:100%'><tr>{cab}</tr>{linhas_html}</table>")


def celula(conteudo, i, al="left", extra=""):
    fundo = ZEBRA if i % 2 else "#ffffff"
    return (f"<td style='{FONTE}font-size:13px;color:#222;background:{fundo};border-top:1px solid {BORDA};"
            f"padding:8px 10px;text-align:{al};vertical-align:middle;{extra}'>{conteudo}</td>")


def data_curta(iso):
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    except Exception:
        return "—"


def resumir_texto(texto, limite=90):
    t = " ".join(str(texto or "").split())
    return t if len(t) <= limite else t[:limite].rsplit(" ", 1)[0] + "…"


def secao_licitacoes(lic):
    """Tabelas curtas: o que mudou na semana e as próximas aberturas."""
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
    nr = "white-space:nowrap;"
    cont = [("Processos publicados no ano", f"{len(itens):,}".replace(",", ".")),
            ("Em andamento agora", str(andamento)),
            ("Novos na semana", str(len(novas))),
            ("Homologados na semana", str(len(homolog)))]
    lin = "".join("<tr>" + celula(n, i) + celula(f"<b>{v}</b>", i, "right", nr) + "</tr>" for i, (n, v) in enumerate(cont))
    t1 = tabela([("Licitações e contratações diretas", "left"), ("Quantidade", "right")], lin)

    hoje_d = dt.date.today().isoformat()
    prox = sorted((l for l in itens if (l.get("abertura") or "") >= hoje_d and l.get("grupo") == "Em andamento"),
                  key=lambda l: l["abertura"])[:5]
    t2 = ""
    if prox:
        lin2 = []
        for i, l in enumerate(prox):
            obj = (f"<a href='{html.escape(l.get('link') or '')}' style='color:#1f4e79;text-decoration:none'>"
                   f"<b>{html.escape(l.get('modalidade') or '')} {html.escape(l.get('numero') or '')}</b></a>"
                   f"<div style='font-size:12px;color:#555'>{html.escape(resumir_texto(l.get('objeto')))}</div>")
            lin2.append("<tr>" + celula(data_curta(l["abertura"]), i, extra=nr + "font-weight:bold;")
                        + celula(obj, i) + celula(brl(l.get("valor_estimado") or 0), i, "right", nr) + "</tr>")
        t2 = (f"<div style='height:12px'></div>"
              + tabela([("Abertura", "left"), ("Processo", "left"), ("Valor estimado", "right")], "".join(lin2)))
    return t1 + t2


def secao_diarias(via, via_ant):
    if not via or not via.get("diarias"):
        return ""
    ano = max(v["ano"] for v in via["diarias"])
    soma = lambda lst, tipo: (sum(1 for v in lst if v["ano"] == ano and v["tipo"] == tipo),
                              sum(v.get("valor") or 0 for v in lst if v["ano"] == ano and v["tipo"] == tipo))
    ant = (via_ant or {}).get("diarias") or []
    nr = "white-space:nowrap;"
    lin, tot = [], [0, 0.0, 0.0]
    for i, tipo in enumerate(["Diária", "Passagem"]):
        n, v = soma(via["diarias"], tipo)
        sem = ""
        if ant:
            dif = v - soma(ant, tipo)[1]
            if dif > 0.005:
                sem = f"<span style='color:#2e7d32'>+{brl(dif)}</span>"
                tot[2] += dif
        tot[0] += n
        tot[1] += v
        rot = "Diárias" if tipo == "Diária" else "Passagens"
        lin.append("<tr>" + celula(f"<b>{rot}</b>", i) + celula(f"{n:,}".replace(",", "."), i, "right", nr)
                   + celula(brl(v), i, "right", nr) + celula(sem or "<span style='color:#bbb'>—</span>", i, "right", nr) + "</tr>")
    i = 2
    lin.append("<tr>" + celula("<b>Total</b>", i) + celula(f"<b>{tot[0]:,}</b>".replace(",", "."), i, "right", nr)
               + celula(f"<b>{brl(tot[1])}</b>", i, "right", nr)
               + celula(f"<b style='color:#2e7d32'>+{brl(tot[2])}</b>" if tot[2] else "<span style='color:#bbb'>—</span>", i, "right", nr) + "</tr>")
    return tabela([(f"Diárias e passagens {ano}", "left"), ("Registros", "right"), ("Valor no ano", "right"),
                   ("Na semana", "right")], "".join(lin))


def montar(dados, anterior, link, lic=None, via=None, via_ant=None, avisos_coleta=""):
    t = totais(dados)
    meta = dados.get("meta", {})
    if t is None:
        return ("Prefeitura de Maringá - Execução Orçamentária: sem dados de despesa",
                "<p>A coleta não trouxe despesas nesta semana.</p>")
    ta = totais(anterior) if anterior else None
    ref = f"{MESES[t['mes'] - 1]}/{t['ano']}"
    nr = "white-space:nowrap;"

    # Tabela 1: resumo
    itens = [("Dotação atualizada", t["dotacao"], None, None, None),
             ("Empenhado", t["empenhado"], t["dotacao"], "empenhado", "#2e75b6"),
             ("Liquidado", t["liquidado"], t["dotacao"], "liquidado", "#5b9bd5"),
             ("Pago", t["pago"], t["dotacao"], "pago", "#2e7d32")]
    if "arrecadado" in t:
        itens.append(("Receita arrecadada", t["arrecadado"], t["previsto"], None, "#c55a11"))
    linhas = []
    for i, (nome, v, base, campo, cor) in enumerate(itens):
        semana = ""
        if ta and campo and ta.get("ano") == t["ano"]:
            dif = v - ta.get(campo, 0)
            if dif > 0.005:
                semana = f"<span style='color:#2e7d32'>+{brl(dif)}</span>"
        if base:
            rot = "do previsto" if nome.startswith("Receita") else "da dotação"
            p = pct_num(v, base)
            perc = (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
                    f"<td style='{FONTE}font-size:13px;{nr}padding-right:8px;width:48px;text-align:right'><b>{pct(v, base)}</b></td>"
                    f"<td>{barra(p, cor)}</td></tr></table>"
                    f"<div style='{FONTE}font-size:11px;color:#777'>{rot}</div>")
        else:
            perc = "<span style='color:#999'>—</span>"
        linhas.append("<tr>" + celula(f"<b>{nome}</b>", i, extra=nr) + celula(brl(v), i, "right", nr + "font-weight:bold;")
                      + celula(perc, i) + celula(semana or "<span style='color:#bbb'>—</span>", i, "right", nr) + "</tr>")
    t1 = tabela([("Indicador", "left"), ("Valor acumulado", "right"), ("Execução", "left"), ("Na semana", "right")],
                "".join(linhas))

    # Tabela 2: órgãos
    lin_o = []
    for i, (o, (dot, e)) in enumerate(t["orgaos"]):
        p = pct_num(e, dot)
        exec_ = (f"<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
                 f"<td style='{FONTE}font-size:13px;{nr}padding-right:8px;width:48px;text-align:right'>{pct(e, dot)}</td>"
                 f"<td>{barra(p, '#2e75b6')}</td></tr></table>")
        lin_o.append("<tr>" + celula(f"<span style='color:#999'>{i + 1}.</span>&nbsp;{html.escape(nome_bonito(o))}", i)
                     + celula(brl(e), i, "right", nr) + celula(exec_, i, extra=nr) + "</tr>")
    t2 = tabela([("Órgão", "left"), ("Empenhado", "right"), ("% da dotação", "left")], "".join(lin_o))

    aviso = ""
    if meta.get("exemplo"):
        aviso = "<p style='background:#fff3cd;padding:8px;border-radius:4px'>Atenção: dados de exemplo, não reais.</p>"
    s_lic = secao_licitacoes(lic)
    s_via = secao_diarias(via, via_ant)
    if avisos_coleta:
        aviso += f"<p style='{FONTE}background:#fff3cd;padding:8px;border-radius:4px;font-size:13px'>{html.escape(avisos_coleta)}</p>"
    titulo = lambda x: f"<h3 style='{FONTE}color:{AZUL};font-size:15px;margin:26px 0 8px'>{x}</h3>"
    fonte = html.escape(str(meta.get("fonte", "Portal da Transparência de Maringá")))
    corpo = f"""<div style="background:#f0f3f7;padding:20px 0">
<table role="presentation" align="center" cellpadding="0" cellspacing="0" width="680" style="width:680px;max-width:100%;background:#ffffff;border-collapse:collapse;border:1px solid {BORDA}">
<tr><td style="background:{AZUL};padding:18px 24px">
  <div style="{FONTE}color:#ffffff;font-size:20px;font-weight:bold">Prefeitura de Maringá</div>
  <div style="{FONTE}color:#cfe0f1;font-size:14px;margin-top:2px">Execução Orçamentária até {ref}</div>
</td></tr>
<tr><td style="padding:18px 24px 24px">
  <p style="{FONTE}font-size:13px;color:#555;margin:0 0 4px">Valores acumulados de 1º de janeiro até {MESES_LONGOS[t['mes'] - 1]} de {t['ano']}.</p>
  <p style="{FONTE}font-size:12px;color:#888;margin:0 0 6px">Dados atualizados em {data_br(meta.get('atualizado_em'))}.</p>
  {aviso}
  {titulo("Resumo do ano")}
  {t1}
  {titulo("Órgãos com maior valor empenhado")}
  {t2}
  {(titulo("Licitações") + s_lic) if s_lic else ""}
  {(titulo("Diárias e passagens") + s_via) if s_via else ""}
  <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:24px"><tr>
    <td style="background:#1565c0;border-radius:4px"><a href="{html.escape(link)}" style="{FONTE}display:inline-block;color:#ffffff;font-size:14px;font-weight:bold;padding:11px 20px;text-decoration:none">Abrir o painel completo</a></td>
  </tr></table>
  <p style="{FONTE}color:#999;font-size:11px;margin-top:20px">Fonte: {fonte}<br>E-mail automático enviado toda segunda-feira pelo painel de execução orçamentária.</p>
</td></tr></table></div>"""
    assunto = f"Prefeitura de Maringá - Execução Orçamentária até {ref}"
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
