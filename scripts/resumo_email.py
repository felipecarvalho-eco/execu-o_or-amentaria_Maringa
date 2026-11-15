"""Gera o resumo semanal (HTML) da execução orçamentária para envio por e-mail.

Uso: python scripts/resumo_email.py painel/dados/execucao.json [anterior.json] \
        --link https://usuario.github.io/painel-orcamento-maringa/ --saida resumo.html
Lê o formato combinado (meta, despesas, receitas). Se o arquivo anterior for dado,
mostra quanto foi empenhado/pago a mais desde a última atualização.
"""
import argparse, html, json, os, sys
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
    return f"R$ {s}"


def pct(a, b):
    return f"{100 * a / b:.1f}%".replace(".", ",") if b else "—"


MESES = "jan fev mar abr mai jun jul ago set out nov dez".split()


def montar(dados, anterior, link):
    t = totais(dados)
    meta = dados.get("meta", {})
    if t is None:
        return "Execução orçamentária de Maringá: sem dados de despesa", "<p>A coleta não trouxe despesas nesta semana.</p>"
    ta = totais(anterior) if anterior else None
    linhas = []

    def linha(nome, v, base=None, campo=None):
        extra = ""
        if ta and campo and ta.get("ano") == t["ano"]:
            dif = v - ta.get(campo, 0)
            extra = f" <span style='color:#2e7d32'>(+{brl(dif)} na semana)</span>" if dif > 0 else ""
        p = f" &middot; {pct(v, base)} da dotação" if base else ""
        linhas.append(f"<tr><td style='padding:4px 12px 4px 0'>{nome}</td><td style='text-align:right'><b>{brl(v)}</b>{p}{extra}</td></tr>")

    linha("Dotação atualizada", t["dotacao"])
    linha("Empenhado", t["empenhado"], t["dotacao"], "empenhado")
    linha("Liquidado", t["liquidado"], t["dotacao"], "liquidado")
    linha("Pago", t["pago"], t["dotacao"], "pago")
    if "arrecadado" in t:
        linhas.append(f"<tr><td style='padding:4px 12px 4px 0'>Receita arrecadada</td><td style='text-align:right'><b>{brl(t['arrecadado'])}</b> &middot; {pct(t['arrecadado'], t['previsto'])} do previsto</td></tr>")

    orgaos = "".join(
        f"<tr><td style='padding:3px 12px 3px 0'>{html.escape(o)}</td><td style='text-align:right'>{brl(e)}</td><td style='text-align:right;padding-left:12px'>{pct(e, dot)}</td></tr>"
        for o, (dot, e) in t["orgaos"])
    aviso = ""
    if meta.get("exemplo"):
        aviso = "<p style='background:#fff3cd;padding:8px'>Atenção: dados de exemplo, não reais.</p>"
    ref = f"{MESES[t['mes'] - 1]}/{t['ano']}"
    corpo = f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:640px">
<h2 style="margin:0 0 4px">Execução orçamentária de Maringá</h2>
<p style="margin:0 0 16px;color:#666">Acumulado de {t['ano']} até {ref} &middot; atualizado em {html.escape(str(meta.get('atualizado_em', '')))}</p>
{aviso}
<table style="border-collapse:collapse">{''.join(linhas)}</table>
<h3 style="margin:20px 0 6px">Órgãos com maior valor empenhado</h3>
<table style="border-collapse:collapse"><tr style="color:#666"><td>Órgão</td><td style="text-align:right">Empenhado</td><td style="text-align:right;padding-left:12px">% dotação</td></tr>{orgaos}</table>
<p style="margin-top:20px"><a href="{html.escape(link)}" style="background:#1565c0;color:#fff;padding:10px 16px;border-radius:4px;text-decoration:none">Abrir o painel completo</a></p>
<p style="color:#888;font-size:12px">Fonte: {html.escape(str(meta.get('fonte', 'Portal da Transparência de Maringá')))}</p>
</div>"""
    assunto = f"Execução orçamentária de Maringá: {pct(t['empenhado'], t['dotacao'])} empenhado até {ref}"
    return assunto, corpo


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("atual")
    ap.add_argument("anterior", nargs="?")
    ap.add_argument("--link", default="")
    ap.add_argument("--saida", default="resumo.html")
    a = ap.parse_args()
    assunto, corpo = montar(carregar(a.atual), carregar(a.anterior), a.link)
    with open(a.saida, "w", encoding="utf-8") as f:
        f.write(corpo)
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"assunto={assunto}\n")
    print(assunto)
