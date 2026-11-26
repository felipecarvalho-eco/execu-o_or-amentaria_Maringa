"""Orçamento do IPPLAM (entidade 15) mês a mês, com empenhos, contratos e pagamentos (aba IPPLAM).

O que é coletado:

    /despesapornivel/detalhada  (de 01/jan até o fim de cada mês)
        dotação inicial e atualizada, empenhado, liquidado e pago acumulados, por elemento de despesa.
        Coletamos o ano atual e os dois anteriores: os anos fechados servem para conferir a projeção
        ("se fosse junho, quanto o painel diria que ia sobrar, e quanto sobrou de fato?").
    /empenhos/lista e /empenhos/detalhe  (ano atual)
        cada empenho com elemento, credor, histórico, contrato, valores empenhado, anulado, liquidado e pago.
    /api/liquidacoes/pagas  (ano atual)
        cada pagamento com data, valor líquido de retenções e nota fiscal.
    /api/contratos/<idOxy>
        número, objeto, valor (com aditivos), vigência e modalidade do contrato citado no empenho. Contratos
        do próprio IPPLAM respondem com o cabeçalho entidade=15; atas da Prefeitura, com entidade=1.

Os valores batem com a tela Consultas > Saldo da Dotação do sistema contábil (OXY). O portal não publica a
reserva de dotação nem o pré-empenho.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

ENT = 15
ANOS_ATRAS = 2


def _fim_mes(ano: int, mes: int) -> dt.date:
    return dt.date(ano + (mes == 12), mes % 12 + 1, 1) - dt.timedelta(days=1)


def _n(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _elemento(programatica: str) -> str:
    """'60.010.04.121.0002.2.502.3.3.90.39.1001' -> '3.3.90.39' (os 4 segmentos antes da fonte).

    Em 2024 e 2025 a ação vem como '2502' e em 2026 como '2.502', por isso contamos do fim."""
    return ".".join(programatica.split(".")[-5:-1])


def orcamento(get, ano: int, ate_mes: int, ate_dia: dt.date | None, nomes: dict, log) -> dict:
    """{elemento: [[inicial, atualizada, empenhado, liquidado, pago] acumulados no fim de cada mês]}."""
    por_el: dict[str, list] = defaultdict(lambda: [[0.0] * 5 for _ in range(ate_mes)])
    for m in range(1, ate_mes + 1):
        fim = min(_fim_mes(ano, m), ate_dia) if ate_dia else _fim_mes(ano, m)
        linhas = get("/despesapornivel/detalhada", ano,
                     {"entidade": ENT, "exercicio": ano, "execucao": "true",
                      "dataInicial": f"{ano}-01-01", "dataFinal": fim.isoformat()}, entidade=ENT)
        for x in linhas:
            p = str(x.get("programatica") or "")
            seg = p.split(".")
            # nó do elemento (sem a fonte): guarda o nome
            if len(seg) >= 4 and x.get("aceitaMovimentacao") != "S" and len(seg[-1]) == 2 and len(seg[-2]) == 2 \
                    and len(seg[-3]) == 1 and len(seg[-4]) == 1 and seg[-4] in "34":
                nomes.setdefault(".".join(seg[-4:]), str(x.get("descricao") or "").strip())
            if x.get("aceitaMovimentacao") != "S":
                continue
            v = por_el[_elemento(p)][m - 1]
            v[0] += _n(x.get("valorPrevisto"))
            v[1] += _n(x.get("valorAtualizado"))
            v[2] += _n(x.get("valorEmpenhado"))
            v[3] += _n(x.get("valorLiquidado"))
            v[4] += _n(x.get("valorPago"))
    log(f"  IPPLAM {ano}: {len(por_el)} elementos, {ate_mes} meses")
    return {k: [[round(a, 2) for a in mes] for mes in v] for k, v in sorted(por_el.items())}


def empenhos(get, ano: int, log, threads: int = 6) -> list[dict]:
    lista = []
    pagina = 0
    while True:
        r = get("/empenhos/lista", ano, {"search": f"id.exercicio=='{ano}' and id.entidade=='{ENT}'",
                                          "entidade": ENT, "page": pagina, "size": 2000}, entidade=ENT)
        lista += r.get("content") or []
        if r.get("last", True) or not r.get("content"):
            break
        pagina += 1
    nums = sorted({int(x["empenho"]) for x in lista})

    def det(n):
        return get("/empenhos/detalhe", ano, {"entidade": ENT, "exercicio": ano, "empenho": n}, entidade=ENT)

    with ThreadPoolExecutor(threads) as ex:
        detalhes = list(ex.map(det, nums))

    pagos = defaultdict(list)
    pagina = 0
    while True:
        r = get("/api/liquidacoes/pagas", ano, {"entidade": ENT, "exercicio": ano, "page": pagina, "size": 2000},
                entidade=ENT)
        for p in r.get("content") or []:
            obs = p.get("notasFiscais") or p.get("observacao") or ""
            pagos[int(p["empenho"])].append([str(p.get("data") or "")[:10], round(_n(p.get("valor")), 2),
                                             " ".join(str(obs).split())[:60]])
        if r.get("last", True) or not r.get("content"):
            break
        pagina += 1

    ids = sorted({d.get("idOxyContrato") for d in detalhes if d.get("idOxyContrato")})

    def contrato(i):
        for ent in (ENT, 1):
            try:
                c = get(f"/api/contratos/{i}", ano, {}, entidade=ent)
            except RuntimeError:
                continue
            if c and c.get("idOxy"):
                return c
        return None

    with ThreadPoolExecutor(threads) as ex:
        contratos = {i: c for i, c in zip(ids, ex.map(contrato, ids)) if c}

    saida = []
    for d in detalhes:
        c = contratos.get(d.get("idOxyContrato"))
        ctr = None
        if c:
            ctr = {"n": f"{c.get('numeroContrato')}/{c.get('exercicio')}", "obj": " ".join(str(c.get("objeto") or "").split())[:200],
                   "valor": round(_n(c.get("valorContratado")) + _n(c.get("valorAditivo")), 2),
                   "fim": str(c.get("vigencia") or c.get("terminoVigencia") or "")[:10], "tipo": c.get("descricaoTipoAto"),
                   "ent": int(c.get("entidade") or ENT), "id": c.get("idOxy"), "lic": c.get("descricaoTipoLicitacao")}
        elif d.get("noContrato"):
            ctr = {"n": f"{d['noContrato']}/{d.get('anoContrato')}"}
        hist = " ".join(str(d.get("historico") or "").split()).replace("Empenho Orcamentário - ", "")
        saida.append({"n": int(d["empenho"]), "d": str(d.get("data") or "")[:10],
                      "el": ".".join(str(d.get("elemento") or "").split(".")[:4]),
                      "cr": " ".join(str(d.get("nome") or "").split()), "doc": d.get("cnpj"), "h": hist[:240],
                      "emp": round(_n(d.get("valor")) - _n(d.get("valorAnulado")), 2), "anul": round(_n(d.get("valorAnulado")), 2),
                      "liq": round(_n(d.get("valorLiquidado")), 2), "pago": round(_n(d.get("valorPago")), 2),
                      "ctr": ctr, "pg": sorted(pagos.get(int(d["empenho"]), []))})
    saida.sort(key=lambda r: -r["n"])
    log(f"  IPPLAM {ano}: {len(saida)} empenhos, {len(contratos)} contratos, "
        f"{sum(len(v) for v in pagos.values())} pagamentos")
    return saida


def coletar(get, hoje: dt.date, avisos: list, log) -> dict:
    """get(path, exercicio, params, entidade=None) -> json. Devolve o objeto gravado em ipplam.js."""
    log("IPPLAM: orçamento mês a mês, empenhos, contratos e pagamentos")
    nomes: dict = {}
    anos = {}
    for ano in range(hoje.year - ANOS_ATRAS, hoje.year + 1):
        if ano < hoje.year:
            anos[ano] = orcamento(get, ano, 12, None, nomes, log)
        else:
            anos[ano] = orcamento(get, ano, hoje.month, hoje, nomes, log)
    emps = empenhos(get, hoje.year, log)
    # conferência: soma dos empenhos = empenhado do ano por elemento
    tot_orc = sum(v[-1][2] for v in anos[hoje.year].values())
    tot_emp = sum(e["emp"] for e in emps)
    if abs(tot_orc - tot_emp) > 1:
        avisos.append(f"IPPLAM: empenhos somam R$ {tot_emp:,.2f}, mas o orçamento mostra R$ {tot_orc:,.2f} empenhados")
    return {"anos": {str(a): v for a, v in anos.items()}, "nomes": nomes,
            "parcial": {"ano": hoje.year, "mes": hoje.month, "dia": hoje.day},
            "ano_empenhos": hoje.year, "empenhos": emps}
