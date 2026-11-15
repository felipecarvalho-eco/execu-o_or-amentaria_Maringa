#!/usr/bin/env python3
"""Coleta da execução orçamentária da Prefeitura de Maringá-PR.

Fonte: Portal da Transparência (sistema Elotech Oxy)
  https://transparencia.maringa.pr.gov.br/portaltransparencia/1/
  API pública usada pelo próprio portal:
  https://maringa.oxy.elotech.com.br/portaltransparencia-api

Uso:
  python coleta/coletar.py --saida painel/dados
  python coleta/coletar.py --saida painel/dados --anos 2024 2025 2026
  python coleta/coletar.py --saida painel/dados --sem-credores

Gera em --saida (todos escritos de forma atômica; nada é sobrescrito se a
coleta falhar):
  despesas.csv     despesa por mês x órgão/unidade/função/subfunção/programa/
                   ação/categoria/grupo/modalidade/elemento/fonte
  receitas.csv     receita por mês x natureza da receita
  credores.csv     empenhos agregados por credor e mês de emissão
  empenhos.csv     lista de empenhos, um por linha (só com --com-empenhos)
  execucao.json    objeto lido pelo painel (meta + despesas agregadas + receitas)
  execucao.js      o mesmo objeto como `window.DADOS_EXECUCAO = {...};`
  credores.json    meta + credores (separado para não pesar o painel)
  credores.js      o mesmo objeto como `window.DADOS_CREDORES = {...};`

O esquema está documentado em coleta/ESQUEMA.md.
Sai com código != 0 em qualquer falha ou se a validação dos totais não bater.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import requests

# A mesma API é servida em dois endereços oficiais: o domínio da Prefeitura e o do fornecedor (Elotech).
# A coleta usa o primeiro que responder. PORTAL_API (variável de ambiente) força um endereço.
ENDERECOS_API = [u for u in [os.environ.get("PORTAL_API", "").rstrip("/")] if u] or [
    "https://transparencia.maringa.pr.gov.br/portaltransparencia-api",
    "https://maringa.oxy.elotech.com.br/portaltransparencia-api",
]
API = ENDERECOS_API[0]
PORTAL = "https://transparencia.maringa.pr.gov.br/portaltransparencia/1/"
ENTIDADE_PRINCIPAL = 1  # PREFEITURA DO MUNICIPIO DE MARINGA
ENTIDADE = ENTIDADE_PRINCIPAL  # entidade sendo coletada no momento (alterada no laço de main)
# Nomes curtos para o painel; entidades novas no portal usam o nome oficial.
NOMES_CURTOS = {
    1: "Prefeitura (administração direta)",
    3: "Maringá Previdência",
    4: "IAM - Instituto Ambiental",
    6: "Câmara Municipal",
    9: "AMR - Agência de Regulação",
    10: "SBMG - Terminais Aéreos",
    15: "IPPLAM - Instituto de Planejamento",
}
# Entidades municipais que não estão no portal Elotech. Os repasses da Prefeitura a elas aparecem
# como credor nos empenhos; o script resume esses valores em meta.fora_do_portal.
FORA_DO_PORTAL = [
    {"nome": "INPACTA - Instituto de Projetos Avançados", "tipo": "Serviço social autônomo",
     "cnpj": "63.783.232/0001-83", "transparencia": "https://inpacta.org.br/transparencia",
     "observacao": "Publica só documentos em PDF (contratos de gestão, atas, prestações de contas), sem "
                   "dados de execução. Os valores abaixo são o que a Prefeitura empenhou/pagou ao INPACTA."},
]
TIPOS = {"E": "Executivo", "L": "Legislativo", "R": "Previdência (RPPS)", "A": "Administração indireta"}
NAO_INF = "Não informado"
TOL = 0.05  # diferença em reais aceita sem aviso nas validações
TOL_REL = 1e-4  # diferença relativa máxima (0,01%) aceita com aviso; acima disso a coleta falha
AVISOS: list[str] = []


def conferir(rotulo: str, folhas: float, total: float) -> None:
    """O portal às vezes tem nós-pai que não batem com a soma dos filhos por centavos/reais.
    Diferenças pequenas viram aviso (registrado em meta.avisos); grandes interrompem a coleta."""
    dif = abs(folhas - total)
    if dif <= TOL:
        return
    msg = f"{rotulo}: soma das folhas ({folhas:.2f}) difere do total do portal ({total:.2f}) em {dif:.2f}"
    if dif > TOL_REL * max(abs(total), 1.0):
        raise RuntimeError(msg)
    log("  aviso: " + msg)
    AVISOS.append(msg)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "coleta-execucao-orcamentaria/1.0", "Accept": "application/json"})


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def get(path: str, exercicio: int, params: dict, tentativas: int = 5, timeout: int = 180):
    """GET na API com cabeçalhos entidade/exercicio e novas tentativas com espera crescente."""
    headers = {"entidade": str(ENTIDADE), "exercicio": str(exercicio)}
    erro = None
    for i in range(tentativas):
        try:
            r = SESSION.get(API + path, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            erro = f"HTTP {r.status_code}: {r.text[:300]}"
        except (requests.RequestException, ValueError) as e:
            erro = repr(e)
        espera = 5 * 2 ** i
        log(f"  falha em {path} ({erro}); nova tentativa em {espera}s")
        time.sleep(espera)
    raise RuntimeError(f"Falha definitiva em {path} {params}: {erro}")


def escolher_api() -> None:
    """Testa os endereços da API e fica com o primeiro que responde."""
    global API
    falhas = []
    for url in ENDERECOS_API:
        try:
            r = SESSION.get(url + "/api/entidades/lista", params={"fields": "id,nome"},
                            headers={"entidade": "1", "exercicio": str(dt.date.today().year)}, timeout=60)
            if r.status_code == 200 and isinstance(r.json(), list):
                API = url
                log(f"API: {url}")
                return
            falhas.append(f"{url}: HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            falhas.append(f"{url}: {e!r}")
    raise RuntimeError("Nenhum endereço da API respondeu: " + " | ".join(falhas))


def fim_do_mes(ano: int, mes: int) -> dt.date:
    prox = dt.date(ano + (mes == 12), mes % 12 + 1, 1)
    return prox - dt.timedelta(days=1)


def meses_do_ano(ano: int, hoje: dt.date) -> list[int]:
    if ano < hoje.year:
        return list(range(1, 13))
    if ano == hoje.year:
        return list(range(1, hoje.month + 1))
    return []


def num(v) -> float:
    return float(v or 0)


def r2(v: float) -> float:
    v = round(v, 2)
    return 0.0 if v == 0 else v


# --------------------------------------------------------------------------- despesas
# A API devolve uma árvore; cada nó tem `programatica` com segmentos separados por ponto:
# órgão . unidade . função . subfunção . programa . ação . categoria . grupo . modalidade . elemento . fonte
SEG_DESPESA = ["orgao", "unidade", "funcao", "subfuncao", "programa", "acao",
               "categoria", "grupo", "modalidade", "elemento", "fonte_recurso"]


def despesa_acumulada(ano: int, mes: int) -> dict:
    """Despesa de 01/jan até o fim de `mes` (valores acumulados no ano) por folha."""
    dados = get("/despesapornivel/detalhada", ano, {
        "entidade": ENTIDADE, "exercicio": ano, "execucao": "true",
        "dataInicial": f"{ano}-01-01", "dataFinal": fim_do_mes(ano, mes).isoformat()})
    if not isinstance(dados, list):
        raise RuntimeError(f"Despesa {ano}-{mes:02d}: resposta inesperada")
    if not dados and ENTIDADE == ENTIDADE_PRINCIPAL:
        raise RuntimeError(f"Despesa {ano}-{mes:02d}: resposta vazia")
    desc = {x["programatica"]: (x.get("descricao") or "").strip() for x in dados}
    folhas = {}
    for x in dados:
        cod = x["programatica"]
        segs = cod.split(".")
        if x.get("aceitaMovimentacao") != "S":
            continue
        if len(segs) != len(SEG_DESPESA):
            raise RuntimeError(f"Programática inesperada: {cod}")
        folhas[cod] = {
            "dotacao_inicial": num(x["valorPrevisto"]),
            "dotacao_atualizada": num(x["valorAtualizado"]),
            "empenhado": num(x["valorEmpenhado"]),
            "liquidado": num(x["valorLiquidado"]),
            "pago": num(x["valorPago"]),
        }
    # validação: soma das folhas == soma dos órgãos
    for k, campo in [("empenhado", "valorEmpenhado"), ("pago", "valorPago"), ("dotacao_atualizada", "valorAtualizado")]:
        a = sum(f[k] for f in folhas.values())
        b = sum(num(x[campo]) for x in dados if x["nivel"] == 1)
        conferir(f"Despesa {ano}-{mes:02d} {k}", a, b)
    return {"folhas": folhas, "desc": desc}


def coletar_despesas(ano: int, meses: list[int]) -> list[dict]:
    linhas = []
    anterior = {}
    descricoes = {}
    for mes in meses:
        log(f"Despesas {ENTIDADE}/{ano}-{mes:02d}")
        acum = despesa_acumulada(ano, mes)
        descricoes.update(acum["desc"])
        for cod, f in acum["folhas"].items():
            p = anterior.get(cod, {})
            mov = {k: f[k] - p.get(k, 0.0) for k in ("empenhado", "liquidado", "pago")}
            mudou_dotacao = abs(f["dotacao_atualizada"] - p.get("dotacao_atualizada", 0.0)) > 0.004
            if mes == meses[0] or mudou_dotacao or any(abs(v) > 0.004 for v in mov.values()):
                linhas.append({"cod": cod, "ano": ano, "mes": mes,
                               "dotacao_inicial": f["dotacao_inicial"],
                               "dotacao_atualizada": f["dotacao_atualizada"], **mov})
        # folhas que sumiram (raro): zera a posição
        for cod in set(anterior) - set(acum["folhas"]):
            p = anterior[cod]
            linhas.append({"cod": cod, "ano": ano, "mes": mes, "dotacao_inicial": p["dotacao_inicial"],
                           "dotacao_atualizada": 0.0,
                           **{k: -p[k] for k in ("empenhado", "liquidado", "pago")}})
        anterior = acum["folhas"]

    saida = []
    for l in linhas:
        segs = l["cod"].split(".")
        reg = {"ano": l["ano"], "mes": l["mes"]}
        for i, nome in enumerate(SEG_DESPESA):
            prefixo = ".".join(segs[: i + 1])
            reg[nome + "_codigo"] = codigo_despesa(nome, segs, i)
            reg[nome] = descricoes.get(prefixo) or NAO_INF
        reg["programatica"] = l["cod"]
        for k in ("dotacao_inicial", "dotacao_atualizada", "empenhado", "liquidado", "pago"):
            reg[k] = r2(l[k])
        saida.append(reg)
    return saida


def codigo_despesa(nome: str, segs: list[str], i: int) -> str:
    if nome == "unidade":
        return ".".join(segs[:2])
    if nome in ("grupo", "modalidade", "elemento"):  # natureza da despesa: 3.1.90.07
        return ".".join(segs[6: i + 1])
    return segs[i]


# --------------------------------------------------------------------------- receitas
def receita_acumulada(ano: int, mes: int) -> dict:
    dados = get("/api/receitas/detalhada", ano, {
        "entidade": ENTIDADE, "exercicio": ano,
        "dataInicial": f"{ano}-01-01", "dataFinal": fim_do_mes(ano, mes).isoformat()})
    if not isinstance(dados, list):
        raise RuntimeError(f"Receita {ano}-{mes:02d}: resposta inesperada")
    if not dados and ENTIDADE == ENTIDADE_PRINCIPAL:
        raise RuntimeError(f"Receita {ano}-{mes:02d}: resposta vazia")
    codigos = [x["receita"] for x in dados]
    desc = {x["receita"]: (x.get("descricao") or "").strip() for x in dados}
    nivel = {x["receita"]: x["nivel"] for x in dados}
    folhas = defaultdict(lambda: defaultdict(float))
    conj = set(codigos)
    for x in dados:
        c = x["receita"]
        # folha = nó sem filhos (a árvore usa prefixos de código)
        if any(o != c and o.startswith(c) for o in conj if len(o) > len(c)):
            continue
        f = folhas[c]
        f["previsto_inicial"] += num(x["valorOrcado"]) + num(x["valorDeducaoInicial"])
        f["previsto"] += num(x["valorOrcadoAtualizado"])
        f["arrecadado_bruto"] += num(x["valorArrecadado"])
        f["deducoes"] += num(x["valorDeducao"])
        f["arrecadado"] += num(x["valorRealizadoLiquido"])
    a = sum(f["arrecadado_bruto"] for f in folhas.values())
    b = sum(num(x["valorArrecadado"]) for x in dados if x["nivel"] == 1)
    conferir(f"Receita {ano}-{mes:02d} arrecadado_bruto", a, b)
    return {"folhas": folhas, "desc": desc, "nivel": nivel}


def coletar_receitas(ano: int, meses: list[int]) -> list[dict]:
    linhas, anterior, desc, nivel = [], {}, {}, {}
    fluxos = ("arrecadado_bruto", "deducoes", "arrecadado")
    for mes in meses:
        log(f"Receitas {ENTIDADE}/{ano}-{mes:02d}")
        acum = receita_acumulada(ano, mes)
        desc.update(acum["desc"]); nivel.update(acum["nivel"])
        for c, f in acum["folhas"].items():
            p = anterior.get(c, {})
            mov = {k: f[k] - p.get(k, 0.0) for k in fluxos}
            if mes == meses[0] or abs(f["previsto"] - p.get("previsto", 0.0)) > 0.004 or any(abs(v) > 0.004 for v in mov.values()):
                linhas.append({"codigo": c, "ano": ano, "mes": mes, "previsto_inicial": f["previsto_inicial"],
                               "previsto": f["previsto"], **mov})
        anterior = {c: dict(f) for c, f in acum["folhas"].items()}

    # ancestrais por prefixo, ordenados por nível
    todos = sorted(desc, key=lambda c: (nivel.get(c, 99), c))
    saida = []
    for l in linhas:
        c = l["codigo"]
        anc = sorted((o for o in todos if c.startswith(o)), key=lambda o: nivel.get(o, 99))
        nomes = [desc[o] for o in anc]
        reg = {
            "ano": l["ano"], "mes": l["mes"],
            "categoria": nomes[0] if len(nomes) > 0 else NAO_INF,
            "origem": nomes[1] if len(nomes) > 1 else NAO_INF,
            "especie": nomes[2] if len(nomes) > 2 else NAO_INF,
            "rubrica": nomes[-1] if nomes else NAO_INF,
            "natureza_codigo": c,
            "fonte_recurso": NAO_INF,
        }
        for k in ("previsto_inicial", "previsto", "arrecadado_bruto", "deducoes", "arrecadado"):
            reg[k] = r2(l[k])
        saida.append(reg)
    return saida


# --------------------------------------------------------------------------- empenhos / credores
def coletar_empenhos(ano: int, ate: dt.date) -> list[dict]:
    busca = (f"id.exercicio=='{ano}' and id.entidade=='{ENTIDADE}' and "
             f"data>='{ano}-01-01' and data<='{ate.isoformat()}'")
    pagina, total_paginas, saida = 0, 1, []
    while pagina < total_paginas:
        d = get("/empenhos/lista", ano, {"search": busca, "entidade": ENTIDADE, "page": pagina, "size": 2000})
        total_paginas = d["totalPages"]
        if pagina == 0:
            log(f"Empenhos {ENTIDADE}/{ano}: {d['totalElements']} registros")
            esperado = d["totalElements"]
        for e in d["content"]:
            nome = (e.get("nome") or "").strip()
            doc, _, razao = nome.partition(" - ")
            if not razao:
                doc, razao = "", nome
            saida.append({
                "ano": ano, "empenho": e.get("empenhoExercicio"), "data": e.get("data"),
                "mes": int(str(e.get("data"))[5:7]),
                "credor_documento": doc.strip(), "credor": razao.strip() or NAO_INF,
                "fonte_recurso": e.get("fonteRecurso") or NAO_INF,
                "historico": (e.get("historico") or "").replace("\r", " ").replace("\n", " ").strip(),
                "empenhado": r2(num(e.get("valorEmpenhado"))), "anulado": r2(num(e.get("valorAnulado"))),
                "liquidado": r2(num(e.get("valorLiquidado"))), "pago": r2(num(e.get("valorPago"))),
                "a_pagar": r2(num(e.get("valorAPagar"))),
            })
        pagina += 1
    if len(saida) != esperado:
        raise RuntimeError(f"Empenhos {ENTIDADE}/{ano}: recebidos {len(saida)} de {esperado}")
    return saida


def agregar_credores(empenhos: list[dict]) -> list[dict]:
    ag = defaultdict(lambda: {"qtd_empenhos": 0, "empenhado": 0.0, "anulado": 0.0, "liquidado": 0.0, "pago": 0.0})
    for e in empenhos:
        k = (e["entidade"], e["ano"], e["mes"], e["credor_documento"], e["credor"])
        a = ag[k]
        a["qtd_empenhos"] += 1
        for c in ("empenhado", "anulado", "liquidado", "pago"):
            a[c] += e[c]
    return [{"entidade": k[0], "ano": k[1], "mes": k[2], "credor_documento": k[3], "credor": k[4],
             "qtd_empenhos": v["qtd_empenhos"],
             **{c: r2(v[c]) for c in ("empenhado", "anulado", "liquidado", "pago")}}
            for k, v in sorted(ag.items())]


# --------------------------------------------------------------------------- objeto do painel
def agregar_painel(linhas_folha: list[dict], id_folha: str, dims: list[str],
                   posicoes: list[str], fluxos: list[str]) -> list[dict]:
    """Agrega linhas de folha nas dimensões do painel.

    Fluxos (movimento do mês) são somados. Posições (dotação/previsão) são recompostas com a
    última posição conhecida de cada folha, para que a linha do mês traga o saldo correto do grupo
    (o painel usa a linha do último mês de cada combinação de dimensões)."""
    por_ano = defaultdict(list)
    for d in linhas_folha:
        por_ano[d["ano"]].append(d)
    saida = []
    for ano in sorted(por_ano):
        linhas = sorted(por_ano[ano], key=lambda d: d["mes"])
        chave_folha, folhas_da_chave = {}, defaultdict(set)
        for d in linhas:
            k = tuple(d[c] for c in dims)
            chave_folha[d[id_folha]] = k
            folhas_da_chave[k].add(d[id_folha])
        posicao = {}
        for mes in sorted({d["mes"] for d in linhas}):
            fluxo = defaultdict(lambda: defaultdict(float))
            for d in (x for x in linhas if x["mes"] == mes):
                posicao[d[id_folha]] = {c: d[c] for c in posicoes}
                f = fluxo[chave_folha[d[id_folha]]]
                for c in fluxos:
                    f[c] += d[c]
            for k, f in fluxo.items():
                pos = [posicao[c] for c in folhas_da_chave[k] if c in posicao]
                saida.append({"ano": ano, "mes": mes, **dict(zip(dims, k)),
                              **{c: r2(sum(p[c] for p in pos)) for c in posicoes},
                              **{c: r2(f[c]) for c in fluxos}})
    return saida


DIM_DESPESA_PAINEL = ["entidade", "orgao", "funcao", "subfuncao", "programa", "categoria", "grupo", "elemento", "fonte_recurso"]
DIM_RECEITA_PAINEL = ["entidade", "categoria", "origem", "fonte_recurso"]


def despesas_painel(despesas: list[dict]) -> list[dict]:
    return agregar_painel(despesas, "id_folha", DIM_DESPESA_PAINEL,
                          ["dotacao_inicial", "dotacao_atualizada"], ["empenhado", "liquidado", "pago"])


def receitas_painel(receitas: list[dict]) -> list[dict]:
    return agregar_painel(receitas, "id_folha", DIM_RECEITA_PAINEL,
                          ["previsto_inicial", "previsto"], ["arrecadado_bruto", "deducoes", "arrecadado"])


# --------------------------------------------------------------------------- saída
def escrever_atomico(destino: Path, conteudo: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix="." + destino.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(conteudo)
    os.chmod(tmp, 0o644)  # mkstemp cria com 0600
    os.replace(tmp, destino)


def csv_texto(linhas: list[dict]) -> str:
    buf = io.StringIO()
    if linhas:
        w = csv.DictWriter(buf, fieldnames=list(linhas[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(linhas)
    return buf.getvalue()


def main() -> int:
    hoje = dt.date.today()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", default="painel/dados", help="pasta de saída (padrão: painel/dados)")
    ap.add_argument("--anos", type=int, nargs="+", default=[hoje.year - 1, hoje.year],
                    help="exercícios a coletar (padrão: ano anterior e ano atual)")
    ap.add_argument("--entidades", type=int, nargs="+",
                    help="ids das entidades (padrão: todas as do portal; 1 = Prefeitura)")
    ap.add_argument("--sem-credores", action="store_true", help="não baixa empenhos/credores")
    ap.add_argument("--com-empenhos", action="store_true",
                    help="grava também empenhos.csv (um empenho por linha, ~20 MB por ano)")
    args = ap.parse_args()

    saida = Path(args.saida)
    anos = sorted(set(args.anos))
    global ENTIDADE
    escolher_api()
    lista = get("/api/entidades/lista", hoje.year, {"fields": "id,nome,cnpj,tipo"})
    entidades = [e for e in lista if not args.entidades or e["id"] in args.entidades]
    if not any(e["id"] == ENTIDADE_PRINCIPAL for e in entidades) and not args.entidades:
        raise RuntimeError("A Prefeitura não aparece na lista de entidades do portal")
    log("Entidades: " + "; ".join(f"{e['id']} {e['nome']}" for e in entidades))

    despesas, receitas, empenhos, info_ent = [], [], [], []
    for ent in entidades:
        ENTIDADE = ent["id"]
        nome = NOMES_CURTOS.get(ENTIDADE, ent["nome"].title())
        disponiveis = {x["id"]["exercicio"] for x in get(f"/api/exercicios/entidade/{ENTIDADE}", hoje.year, {})}
        cont = [len(despesas), len(receitas), len(empenhos)]
        for ano in anos:
            meses = meses_do_ano(ano, hoje)
            if not meses or ano not in disponiveis:
                if ano not in disponiveis:
                    AVISOS.append(f"{nome}: exercício {ano} não existe no portal")
                continue
            d = coletar_despesas(ano, meses)
            r = coletar_receitas(ano, meses)
            e = [] if args.sem_credores else coletar_empenhos(ano, min(hoje, dt.date(ano, 12, 31)))
            for lst, idf in ((d, "programatica"), (r, "natureza_codigo"), (e, None)):
                for x in lst:
                    if idf:
                        x["id_folha"] = f"{ENTIDADE}:{x[idf]}"
            despesas += [{"entidade": nome, "entidade_id": ENTIDADE, **x} for x in d]
            receitas += [{"entidade": nome, "entidade_id": ENTIDADE, **x} for x in r]
            empenhos += [{"entidade": nome, "entidade_id": ENTIDADE, **x} for x in e]
        n = [len(despesas) - cont[0], len(receitas) - cont[1], len(empenhos) - cont[2]]
        if n[0] == 0:
            AVISOS.append(f"{nome}: o portal não publica execução orçamentária desta entidade")
        info_ent.append({"id": ENTIDADE, "nome": nome, "nome_oficial": ent["nome"], "cnpj": ent.get("cnpj"),
                         "tipo": TIPOS.get(ent.get("tipo"), ent.get("tipo")),
                         "linhas_despesa": n[0], "linhas_receita": n[1], "empenhos": n[2]})
    ENTIDADE = ENTIDADE_PRINCIPAL

    if not despesas or not receitas:
        raise RuntimeError("Coleta vazia: nada será gravado")

    def ult(path):
        try:
            return get(path, hoje.year, {"entidade": ENTIDADE, "exercicio": hoje.year})
        except Exception:
            return None

    credores = agregar_credores(empenhos)
    fora = []
    for f in FORA_DO_PORTAL:
        por_ano = defaultdict(lambda: defaultdict(float))
        for e in empenhos:
            if e["credor_documento"] == f["cnpj"]:
                for c in ("empenhado", "anulado", "liquidado", "pago"):
                    por_ano[e["ano"]][c] += e[c]
                por_ano[e["ano"]]["qtd_empenhos"] += 1
        fora.append({**f, "repasses_por_ano": [{"ano": a, **{c: (int(v) if c == "qtd_empenhos" else r2(v)) for c, v in sorted(d.items())}}
                                              for a, d in sorted(por_ano.items())]})
    meta = {
        "municipio": "Maringá-PR",
        "entidades": info_ent,
        "fora_do_portal": fora,
        "fonte": PORTAL,
        "api": API,
        "atualizado_em": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "portal_atualizado_despesas": ult("/api/ultimas-atualizacoes/last-update-despesas"),
        "portal_atualizado_receitas": ult("/api/ultimas-atualizacoes/last-update-receitas"),
        "anos": anos,
        "mes_corrente_parcial": f"{hoje.year}-{hoje.month:02d}" if hoje.year in anos else None,
        "observacoes": (
            "Valores em reais. empenhado/liquidado/pago/arrecadado são o movimento do mês (podem ser "
            "negativos por anulações/estornos). dotacao_* e previsto são a posição acumulada no fim do mês. "
            "Receita: arrecadado = líquido de deduções (FUNDEB etc.), comparável a previsto. "
            "Entidades: somar todas pode contar duas vezes operações entre elas (ex.: a contribuição patronal "
            "que a Prefeitura paga à Maringá Previdência é despesa de uma e receita da outra). "
            "Credores: agrupados pelo mês de emissão do empenho; liquidado/pago dos credores são a posição "
            "atual de cada empenho (não do mês). Não inclui restos a pagar de exercícios anteriores."),
        "avisos": AVISOS,
        "linhas_csv": {"despesas": len(despesas), "receitas": len(receitas), "credores": len(credores)},
    }

    obj = {"meta": meta, "despesas": despesas_painel(despesas), "receitas": receitas_painel(receitas)}
    js = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    js_cred = json.dumps({"meta": meta, "credores": credores}, ensure_ascii=False, separators=(",", ":"))

    sem_id = lambda L: [{k: v for k, v in x.items() if k != "id_folha"} for x in L]
    escrever_atomico(saida / "despesas.csv", csv_texto(sem_id(despesas)))
    escrever_atomico(saida / "receitas.csv", csv_texto(sem_id(receitas)))
    if not args.sem_credores:
        escrever_atomico(saida / "credores.csv", csv_texto(credores))
        if args.com_empenhos:
            escrever_atomico(saida / "empenhos.csv", csv_texto(empenhos))
        escrever_atomico(saida / "credores.json", js_cred)
        escrever_atomico(saida / "credores.js", "window.DADOS_CREDORES = " + js_cred + ";\n")
    escrever_atomico(saida / "execucao.json", js)
    escrever_atomico(saida / "execucao.js", "window.DADOS_EXECUCAO = " + js + ";\n")
    log(f"OK: {len(despesas)} linhas de despesa, {len(receitas)} de receita, {len(credores)} de credores -> {saida}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"ERRO: {e}")
        sys.exit(1)
