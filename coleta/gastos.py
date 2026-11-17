"""Para onde vai o dinheiro: cada empenho com secretaria, elemento, fornecedor, licitação e contrato.

A lista de empenhos (/empenhos/lista, já baixada para os credores) traz fornecedor, histórico e valores.
Secretaria, unidade, programa, ação, elemento, licitação, processo, contrato e itens só aparecem no
detalhe de cada empenho:

    /empenhos/detalhe?entidade=E&exercicio=A&empenho=N      (uma consulta por empenho, ~0,3 s)

Empenhos de folha de pagamento (histórico começa com "Folha de pagamento", quase metade do total) não
têm licitação nem contrato; para eles o órgão e o elemento vêm da própria lista, filtrada por
`programatica.orgao=<código>` e por `programatica.elemento=<6 dígitos>`, sem abrir o detalhe.

Esses campos não mudam depois que o empenho é emitido. Por isso o detalhe é baixado uma vez só e
reaproveitado nas coletas seguintes a partir do gastos.js anterior ("cache"). Os valores (empenhado,
anulado, liquidado, pago) vêm sempre da lista, atualizados a cada coleta.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

NAO_INF = "Não informado"
VERSAO_CACHE = 1
# campos que vêm do detalhe (fixos) e são reaproveitados de uma coleta para outra
FIXOS = ["orgao_codigo", "orgao", "unidade", "funcao", "programa", "acao", "elemento_codigo", "elemento",
         "desdobramento", "modalidade", "licitacao", "licitacao_oxy", "processo", "contrato", "itens"]


def _txt(s, n: int | None = None) -> str:
    s = " ".join(str(s or "").split())
    if n and len(s) > n:
        s = s[: n - 1].rstrip() + "…"
    return s


def _num_ano(n, a) -> str:
    return f"{n}/{a}" if n not in (None, "") and a else ""


def fixos_do_detalhe(d: dict, nome_orgao: dict, nome_elemento: dict) -> dict:
    """Campos fixos a partir da resposta de /empenhos/detalhe. Os nomes de órgão e elemento seguem os
    da despesa (mesmos nomes do resto do painel) quando existem."""
    elem = (d.get("elemento") or "").split(".")
    elem_cod = ".".join(elem[:4]) if len(elem) >= 4 else ""
    orgao_cod = str(d.get("orgao") or "")
    itens = [_txt(i.get("especificacao")) for i in (d.get("itens") or []) if i.get("especificacao")]
    return {
        "orgao_codigo": orgao_cod,
        "orgao": nome_orgao.get(orgao_cod) or _txt(d.get("descricaoOrgao")).title() or NAO_INF,
        "unidade": _txt(d.get("descricaoUnidade")).title() or NAO_INF,
        "funcao": _txt(d.get("descricaoFuncao")).title() or NAO_INF,
        "programa": _txt(d.get("descricaoPrograma")).title() or NAO_INF,
        "acao": _txt(d.get("descricaoProjeto")).title() or NAO_INF,
        "elemento_codigo": elem_cod,
        "elemento": nome_elemento.get(elem_cod) or NAO_INF,
        "desdobramento": _txt(d.get("descricaoDesdobramento") or d.get("descricaoElemento")) or NAO_INF,
        "modalidade": _txt(d.get("modalidade") or d.get("tipoLicitacao")) or NAO_INF,
        "licitacao": _num_ano(d.get("noLicitacao") or d.get("licitacao"), d.get("anoLicitacao")),
        "licitacao_oxy": d.get("oxyIdLicitacao") or "",
        "processo": _num_ano(d.get("noProcesso"), d.get("anoProcesso")),
        "contrato": _num_ano(d.get("noContrato") or d.get("contrato"), d.get("anoContrato")),
        "itens": _txt("; ".join(itens[:3]) + (f" (+{len(itens) - 3} itens)" if len(itens) > 3 else ""), 200),
    }


def e_folha(e: dict) -> bool:
    return (e.get("historico") or "").lower().startswith("folha de pagamento")


def _paginas(get, ano: int, ent: int, extra: dict) -> list[dict]:
    busca = f"id.exercicio=='{ano}' and id.entidade=='{ent}' and data>='{ano}-01-01' and data<='{ano}-12-31'"
    pagina, total, saida = 0, 1, []
    while pagina < total:
        d = get("/empenhos/lista", ano, {"search": busca, "entidade": ent, "page": pagina, "size": 2000, **extra},
                entidade=ent)
        total = d.get("totalPages", 0)
        saida += d.get("content") or []
        pagina += 1
    return saida


def mapa_pela_lista(get, ano: int, ent: int, orgaos: list[str], elementos: list[str]) -> dict[int, list[str]]:
    """{número do empenho: [código do órgão, código do elemento]} consultando a lista filtrada."""
    mapa: dict[int, list[str]] = {}
    for o in orgaos:
        for e in _paginas(get, ano, ent, {"programatica.orgao": o}):
            mapa.setdefault(e["empenho"], ["", ""])[0] = o
    for el in elementos:
        for e in _paginas(get, ano, ent, {"programatica.elemento": el.replace(".", "")}):
            mapa.setdefault(e["empenho"], ["", ""])[1] = el
    return mapa


def coletar(get, empenhos: list[dict], despesas: list[dict], licitacoes: list[dict], anos: list[int],
            cache: dict[str, dict], avisos: list[str], log, threads: int = 4,
            max_minutos: float = 0) -> list[dict]:
    """Uma linha por empenho dos `anos` pedidos. `cache` = {id: campos fixos} da coleta anterior.
    max_minutos > 0 limita o tempo gasto baixando detalhes novos (o resto fica para a próxima coleta)."""
    nome_orgao, nome_elemento = {}, {}
    for d in despesas:
        nome_orgao.setdefault(d["entidade_id"], {}).setdefault(d["orgao_codigo"], d["orgao"])
        nome_elemento.setdefault(d["entidade_id"], {}).setdefault(d["elemento_codigo"], d["elemento"])
    link_lic = {}
    for l in licitacoes:
        if "oxyId=" in (l.get("link") or ""):
            link_lic[l["link"].split("oxyId=")[1].split("&")[0]] = l["link"]

    alvo = [e for e in empenhos if e["ano"] in anos]
    ids = {e_id(e): e for e in alvo}
    novos = {}
    # folha: órgão e elemento pela lista filtrada (sem detalhe)
    folha = [i for i, e in ids.items() if i not in cache and e_folha(e)]
    for ent, ano in sorted({(ids[i]["entidade_id"], ids[i]["ano"]) for i in folha}):
        try:
            mapa = mapa_pela_lista(get, ano, ent, sorted(nome_orgao.get(ent, {})),
                                   sorted(c for c in nome_elemento.get(ent, {}) if c.count(".") == 3))
        except Exception as ex:  # noqa: BLE001
            avisos.append(f"Gastos: órgão/elemento da folha não identificados para a entidade {ent}/{ano} ({ex})")
            continue
        for i in folha:
            e = ids[i]
            if (e["entidade_id"], e["ano"]) != (ent, ano):
                continue
            o, el = mapa.get(int(str(e["empenho"]).split("/")[0]), ["", ""])
            novos[i] = {k: "" for k in FIXOS} | {
                "orgao_codigo": o, "orgao": nome_orgao.get(ent, {}).get(o, NAO_INF),
                "unidade": NAO_INF, "funcao": NAO_INF, "programa": NAO_INF, "acao": NAO_INF,
                "elemento_codigo": el, "elemento": nome_elemento.get(ent, {}).get(el, NAO_INF),
                "desdobramento": NAO_INF, "modalidade": "Folha de pagamento"}
    faltam = [i for i in ids if i not in cache and i not in novos and not e_folha(ids[i])]
    log(f"Gastos: {len(ids)} empenhos, {len(cache)} já conhecidos, {len(novos)} de folha pela lista, "
        f"{len(faltam)} detalhes a baixar")

    falhas, inicio, n_folha = 0, time.monotonic(), len(novos)

    def baixar(i):
        e = ids[i]
        numero = int(str(e["empenho"]).split("/")[0])
        d = get("/empenhos/detalhe", e["ano"], {"entidade": e["entidade_id"], "exercicio": e["ano"],
                                                "empenho": numero}, entidade=e["entidade_id"], tentativas=3)
        if not isinstance(d, dict) or d.get("empenho") != numero:
            raise RuntimeError("resposta inesperada")
        ent = e["entidade_id"]
        return i, fixos_do_detalhe(d, nome_orgao.get(ent, {}), nome_elemento.get(ent, {}))

    parou = False
    with ThreadPoolExecutor(max_workers=threads) as ex:
        pendentes = iter(faltam)
        futuros = set()

        def encher():
            nonlocal parou
            while len(futuros) < threads * 4 and not parou:
                if max_minutos and time.monotonic() - inicio > max_minutos * 60:
                    parou = True
                    break
                try:
                    futuros.add(ex.submit(baixar, next(pendentes)))
                except StopIteration:
                    break

        encher()
        while futuros:
            pronto = next(as_completed(futuros))
            futuros.discard(pronto)
            try:
                i, f = pronto.result()
                novos[i] = f
                if (len(novos) - n_folha) % 2000 == 0:
                    log(f"  detalhes: {len(novos) - n_folha} de {len(faltam)}")
            except Exception:  # noqa: BLE001
                falhas += 1
            encher()

    sem = len(faltam) - (len(novos) - n_folha)
    if falhas:
        avisos.append(f"Gastos: {falhas} empenhos sem detalhe por falha na consulta (tentará de novo na próxima coleta)")
    if parou and sem - falhas > 0:
        avisos.append(f"Gastos: {sem - falhas} empenhos ainda sem detalhe (limite de tempo); completa na próxima coleta")

    vazio = {k: NAO_INF for k in FIXOS} | {"orgao_codigo": "", "elemento_codigo": "", "licitacao": "",
                                           "licitacao_oxy": "", "processo": "", "contrato": "", "itens": ""}
    saida = []
    for i, e in ids.items():
        f = cache.get(i) or novos.get(i)
        saida.append({
            "id": i,
            "entidade_id": e["entidade_id"],
            "entidade": e["entidade"],
            "ano": e["ano"], "mes": e["mes"], "data": e["data"],
            "empenho": e["empenho"],
            **(f or vazio),
            "detalhe": bool(f),
            "link_licitacao": link_lic.get((f or {}).get("licitacao_oxy") or "", ""),
            "credor_documento": e["credor_documento"], "credor": e["credor"],
            "fonte_recurso": e["fonte_recurso"],
            "historico": _txt(e["historico"], 250),
            "empenhado": e["empenhado"], "anulado": e["anulado"],
            "valor": round(e["empenhado"] - e["anulado"], 2),
            "liquidado": e["liquidado"], "pago": e["pago"],
        })
    saida.sort(key=lambda g: (g["data"] or "", g["id"]))
    log(f"Gastos: {len(saida)} linhas ({len(novos)} novos, {sem} sem detalhe)")
    return saida


def e_id(e: dict) -> str:
    return f"{e['entidade_id']}|{e['empenho']}"


# ----------------------------------------------------------------------------- formato compacto
# 48 mil empenhos em objetos JSON dariam ~55 MB. O arquivo usa tabela + dicionários: cada coluna de
# texto guarda um índice para a lista `dicionarios[coluna]` (nomes de secretaria, elemento, credor,
# histórico etc. se repetem muito). Assim fica ~4 vezes menor.
COLUNAS = ["entidade_id", "entidade", "data", "empenho", "orgao_codigo", "orgao", "unidade", "funcao", "programa",
           "acao", "elemento_codigo", "elemento", "desdobramento", "modalidade", "licitacao", "licitacao_oxy",
           "processo", "contrato", "itens", "link_licitacao", "credor_documento", "credor", "fonte_recurso",
           "historico", "detalhe", "empenhado", "anulado", "liquidado", "pago"]
NAO_CODIFICADAS = {"entidade_id", "empenho", "detalhe", "empenhado", "anulado", "liquidado", "pago"}


def tabela(gastos: list[dict]) -> dict:
    dic = {c: [] for c in COLUNAS if c not in NAO_CODIFICADAS}
    pos = {c: {} for c in dic}
    linhas = []
    for g in gastos:
        lin = []
        for c in COLUNAS:
            v = int(g[c]) if c == "detalhe" else g[c]
            if c in dic:
                i = pos[c].get(v)
                if i is None:
                    i = pos[c][v] = len(dic[c])
                    dic[c].append(v)
                v = i
            lin.append(v)
        linhas.append(lin)
    return {"colunas": COLUNAS, "dicionarios": dic, "linhas": linhas}


def linhas_de(obj: dict) -> list[dict]:
    cols, dic = obj["colunas"], obj["dicionarios"]
    return [{c: (dic[c][v] if c in dic else v) for c, v in zip(cols, lin)} for lin in obj["linhas"]]


def cache_de(prev: dict | None) -> dict[str, dict]:
    """Campos fixos da coleta anterior (só das linhas que tinham detalhe)."""
    if not prev or prev.get("meta", {}).get("versao_cache") != VERSAO_CACHE:
        return {}
    return {f"{g['entidade_id']}|{g['empenho']}": {k: g[k] for k in FIXOS}
            for g in linhas_de(prev) if g.get("detalhe")}
