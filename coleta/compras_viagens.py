"""Licitações (inclusive contratação direta) e diárias/passagens do Portal da Transparência de Maringá.

Usa a mesma API do resto da coleta (cabeçalhos entidade/exercicio). Funções recebem `get` (a função
de requisição de coletar.py) para reaproveitar as novas tentativas e o endereço escolhido.

Endpoints:
- licitações:  /api/licitacoes?entidade=E&exercicio=A&page=0&size=5000
- diárias:     /api/diarias?entidade=E&exercicio=A&dataInicial=A-01-01&dataFinal=A-12-31&page=..&size=..
- passagens:   /api/passagens?entidade=E&exercicio=A&page=..&size=..
- órgão das diárias: /empenhos/lista filtrado por `programatica.orgao=<código>` (a API de diárias não
  traz a secretaria; o órgão vem do empenho que pagou a diária).
"""
from __future__ import annotations

import re
from collections import Counter

PORTAL = "https://transparencia.maringa.pr.gov.br/portaltransparencia"
NAO_INF = "Não informado"

GRUPOS = {
    "HOMOLOGADA": "Homologada",
    "SUSPENSA": "Suspensa",
    "REVOGADA": "Sem sucesso", "FRACASSADA": "Sem sucesso", "DESERTA": "Sem sucesso",
    "ANULADA": "Sem sucesso", "CANCELADA": "Sem sucesso",
}
_SECRETARIA = re.compile(r"(Secretaria Municipal (?:de |da |do |das |dos )?[A-ZÀ-Úa-zà-ú ,]+?)(?=\s*(?:\(|[-–,.;:]|$| para | com | e (?:da|do) | no | na | em | que ))")


def _pagina_tudo(get, path: str, ano: int, entidade: int, params: dict, tamanho: int = 2000) -> list[dict]:
    pagina, total, saida = 0, 1, []
    while pagina < total:
        d = get(path, ano, {**params, "page": pagina, "size": tamanho}, entidade=entidade)
        total = d.get("totalPages", 0)
        saida += d.get("content") or []
        pagina += 1
    esperado = d.get("totalElements", len(saida)) if pagina else 0
    if len(saida) != esperado:
        raise RuntimeError(f"{path} entidade {entidade}/{ano}: recebidos {len(saida)} de {esperado}")
    return saida


def _secretaria(texto: str) -> str:
    m = _SECRETARIA.search(texto or "")
    return m.group(1).strip() if m else NAO_INF


def _grupo(situacao: str) -> str:
    return GRUPOS.get((situacao or "").strip().upper(), "Em andamento")


# --------------------------------------------------------------------------- licitações
def coletar_licitacoes(get, ano: int, entidade: int, nome_entidade: str) -> list[dict]:
    brutos = _pagina_tudo(get, "/api/licitacoes", ano, entidade, {"entidade": entidade, "exercicio": ano})
    saida = []
    for x in brutos:
        modalidade = (x.get("descricao") or NAO_INF).strip()
        numero = x.get("displayLicitacao") or f"{x.get('licitacao')}/{ano}"
        pub = x.get("dataPublicacao") or ""
        situacao = (x.get("tipoCancelamento") or NAO_INF).strip()
        link = (f"{PORTAL}/{entidade}/licitacoes/detalhes?entidade={entidade}&exercicio={x.get('exercicio')}"
                f"&licitacao={x.get('licitacao')}&tipoLicitacao={x.get('tipoLicitacao')}"
                + (f"&oxyId={x['idOxy']}" if x.get("idOxy") else ""))
        objeto = " ".join((x.get("objeto") or "").split())
        saida.append({
            "id": f"{nome_entidade}|{modalidade}|{numero}",
            "ano": ano,
            "mes": int(pub[5:7]) if len(pub) >= 7 else None,
            "entidade": nome_entidade,
            "secretaria": _secretaria(objeto + " " + (x.get("observacao") or "")),
            "numero": numero,
            "modalidade": modalidade,
            "contratacao_direta": modalidade.upper().startswith(("DISPENSA", "INEXIGIBILIDADE")),
            "natureza": x.get("natureza") or NAO_INF,
            "objeto": objeto or NAO_INF,
            "valor_estimado": round(float(x.get("valorMaximo") or 0), 2),
            "valor_contratado": round(float(x.get("totalVencido") or 0), 2),
            "situacao": situacao,
            "grupo": _grupo(situacao),
            "publicacao": pub or None,
            "abertura": x.get("dataAbertura"),
            "processo": x.get("displayProtocolo"),
            "link": link,
        })
    return saida


def calcular_mudancas(atuais: list[dict], anteriores: list[dict] | None, historico: list[dict],
                      hoje: str, max_hist: int = 500) -> list[dict]:
    """Compara com a coleta anterior: licitações novas, que mudaram de situação ou de valor contratado.
    Mantém o histórico das semanas anteriores (as mais recentes primeiro, até `max_hist` itens).
    Na primeira coleta (sem arquivo anterior) não marca tudo como novo."""
    if anteriores is None:
        return historico[:max_hist]
    ant = {l["id"]: l for l in anteriores}
    novas = []
    for l in atuais:
        base = {k: l[k] for k in ("id", "entidade", "numero", "modalidade", "objeto", "valor_estimado",
                                  "valor_contratado")}
        a = ant.get(l["id"])
        if a is None:
            novas.append({"atualizacao": hoje, **base, "tipo": "Nova", "de": "", "para": l["situacao"]})
            continue
        if a.get("situacao") != l["situacao"]:
            novas.append({"atualizacao": hoje, **base, "tipo": "Mudou de situação",
                          "de": a.get("situacao", ""), "para": l["situacao"]})
        if abs((a.get("valor_contratado") or 0) - l["valor_contratado"]) > 0.004:
            novas.append({"atualizacao": hoje, **base, "tipo": "Mudou valor contratado",
                          "de": f"{a.get('valor_contratado') or 0:.2f}", "para": f"{l['valor_contratado']:.2f}"})
    # coleta repetida no mesmo dia substitui as mudanças daquele dia
    resto = [m for m in historico if m.get("atualizacao") != hoje]
    return (novas + resto)[:max_hist]


# --------------------------------------------------------------------------- diárias e passagens
def mapa_orgao_empenhos(get, ano: int, entidade: int, orgaos: dict[str, str], avisos: list[str],
                        nome_entidade: str) -> dict[int, str]:
    """{número do empenho: nome do órgão} para os empenhos do ano, consultando órgão a órgão."""
    busca = (f"id.exercicio=='{ano}' and id.entidade=='{entidade}' and "
             f"data>='{ano}-01-01' and data<='{ano}-12-31'")
    mapa = {}
    for cod, nome in sorted(orgaos.items()):
        for e in _pagina_tudo(get, "/empenhos/lista", ano, entidade,
                              {"search": busca, "entidade": entidade, "programatica.orgao": cod}):
            mapa[e["empenho"]] = nome
    total = get("/empenhos/lista", ano, {"search": busca, "entidade": entidade, "page": 0, "size": 1},
                entidade=entidade).get("totalElements", 0)
    if total and len(mapa) < total:
        avisos.append(f"{nome_entidade} {ano}: órgão identificado para {len(mapa)} de {total} empenhos")
    return mapa


def coletar_diarias(get, ano: int, entidade: int, nome_entidade: str, orgao_de_empenho) -> list[dict]:
    """orgao_de_empenho(exercicio_empenho, numero) -> nome do órgão ou None."""
    brutos = _pagina_tudo(get, "/api/diarias", ano, entidade, {
        "entidade": entidade, "exercicio": ano, "dataInicial": f"{ano}-01-01", "dataFinal": f"{ano}-12-31"})
    saida = []
    for x in brutos:
        saida_d = x.get("dataSaida") or ""
        orgao = orgao_de_empenho(x.get("exercicioEmpenho"), x.get("empenho")) or NAO_INF
        saida.append({
            "id": f"{entidade}|D|{x.get('idEmpenho') or x.get('displayEmpenho')}",
            "ano": int(saida_d[:4]) if saida_d else ano,
            "mes": int(saida_d[5:7]) if saida_d else None,
            "entidade": nome_entidade,
            "orgao": orgao,
            "tipo": "Diária",
            "beneficiario": (x.get("nome") or NAO_INF).strip(),
            "cargo": (x.get("cargo") or x.get("cargoDiaria") or NAO_INF).strip() or NAO_INF,
            "destino": ", ".join(p for p in [(x.get("destino") or "").strip(), (x.get("uf") or "").strip()] if p) or NAO_INF,
            "motivo": " ".join((x.get("motivo") or "").split()) or NAO_INF,
            "objetivo": x.get("tipoObjetivoDiaria") or NAO_INF,
            "data_saida": x.get("dataSaida"),
            "data_retorno": x.get("dataRetorno"),
            "quantidade": float(x.get("quantidadeDiarias") or 0),
            # valor líquido: empenhado menos o que foi anulado (diárias canceladas ficam com 0)
            "valor": round(float(x.get("gastosTotais") if x.get("gastosTotais") is not None
                                 else (x.get("valorEmpenho") or 0) - (x.get("valorAnulado") or 0)), 2),
            "valor_empenhado": round(float(x.get("valorEmpenho") or x.get("valorDiarias") or 0), 2),
            "situacao": x.get("situacaoCancelamento") or NAO_INF,
            "meio_transporte": x.get("meioTransporte") or NAO_INF,
            "documento": f"Empenho {x.get('displayEmpenho')}" if x.get("displayEmpenho") else NAO_INF,
            "empresa": NAO_INF,
        })
    return saida


def coletar_passagens(get, ano: int, entidade: int, nome_entidade: str) -> list[dict]:
    brutos = _pagina_tudo(get, "/api/passagens", ano, entidade, {"entidade": entidade, "exercicio": ano})
    saida = []
    for x in brutos:
        ida = x.get("dataInicio") or x.get("dataSolicitacao") or ""
        saida.append({
            "id": f"{entidade}|P|{x.get('id')}",
            "ano": int(ida[:4]) if ida else ano,
            "mes": int(ida[5:7]) if ida else None,
            "entidade": nome_entidade,
            "orgao": NAO_INF,
            "tipo": "Passagem",
            "beneficiario": (x.get("nomeServidor") or NAO_INF).strip(),
            "cargo": (x.get("cargoServidor") or NAO_INF).strip() or NAO_INF,
            "destino": ", ".join(p for p in [(x.get("destinoViagem") or "").strip().title(), (x.get("uf") or "").strip()] if p) or NAO_INF,
            "motivo": " ".join((x.get("justificativa") or "").split()) or NAO_INF,
            "objetivo": NAO_INF,
            "data_saida": x.get("dataInicio"),
            "data_retorno": x.get("dataFim"),
            "quantidade": 1.0,
            "valor": round(float(x.get("valorAdiantamento") or 0), 2),
            "valor_empenhado": round(float(x.get("valorAdiantamento") or 0), 2),
            "situacao": NAO_INF,
            "meio_transporte": x.get("tipoPassagem") or NAO_INF,
            "documento": f"Solicitação {x.get('numeroSolicitacao')}" if x.get("numeroSolicitacao") else NAO_INF,
            "empresa": (x.get("nomeEmpresaFornecedora") or "").strip() or NAO_INF,
        })
    return saida


def resumo(lista: list[dict], campo: str) -> dict:
    return dict(Counter(x[campo] for x in lista).most_common())
