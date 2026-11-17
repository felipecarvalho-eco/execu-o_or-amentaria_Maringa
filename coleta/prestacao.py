"""Prestação de contas das diárias: quando o servidor entregou o relatório de viagem.

O portal não tem um campo com a data da prestação de contas. Ele publica, para cada diária, os arquivos
da prestação (em geral o "Relatório de Viagem" assinado no SEI):

    /api/diarias-prestacao-conta/arquivos?idEmpenho=<idEmpenho>     lista nome e id de cada PDF (sem data)
    /api/files/arquivo/<idArquivo>                                  o PDF (não precisa de cabeçalhos)

A data é lida do texto do PDF, na linha da assinatura eletrônica do SEI:
"Documento assinado eletronicamente por <nome>, <cargo>, em DD/MM/AAAA". Usamos a primeira assinatura
feita no dia do retorno ou depois (a do próprio servidor; as da chefia vêm depois). Quando há um arquivo
que é o relatório de viagem, só ele conta; senão, todos os arquivos.

Incremental: a data lida não muda. Por isso cada coleta reaproveita o resultado da anterior (campo
`prestacao` do diarias.json) e só consulta o portal para as diárias ainda sem data. Arquivos já lidos
sem data legível (ex.: PDF escaneado) também não são baixados de novo.

Campo `prestacao` gravado em cada diária:
  data      "AAAA-MM-DD" ou null   primeira assinatura do relatório no retorno ou depois
  arquivos  int                    quantos arquivos de prestação o portal publica
  link      url ou ""              PDF do relatório no portal
  lidos     [ids]                  arquivos já lidos sem data legível (só quando data é null)
  nao_lido  true                   o tempo da coleta acabou antes; completa na próxima
"""
from __future__ import annotations

import datetime as dt
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ASSINATURA = re.compile(r"assinado\s+eletronicamente\s+por\s.{0,250}?\sem\s+(\d{2}/\d{2}/\d{4})", re.S | re.I)
RELATORIO = re.compile(r"relat[óo]rio\s+de\s+viagem|presta[çc][ãa]o\s+de\s+contas", re.I)
MAX_PAGINAS = 30


def texto_pdf(conteudo: bytes) -> str:
    from pypdf import PdfReader  # importado aqui para a coleta principal não depender do pypdf
    try:
        paginas = PdfReader(io.BytesIO(conteudo)).pages
        return "\n".join((p.extract_text() or "") for p in list(paginas)[:MAX_PAGINAS])
    except Exception:  # noqa: BLE001  (PDF corrompido ou protegido: fica sem data)
        return ""


def datas_assinatura(texto: str) -> list[dt.date]:
    saida = set()
    for s in ASSINATURA.findall(texto):
        try:
            saida.add(dt.datetime.strptime(s, "%d/%m/%Y").date())
        except ValueError:
            pass
    return sorted(saida)


def escolher_data(lidos: list[tuple[str, str]], retorno: dt.date) -> tuple[dt.date | None, str | None]:
    """lidos = [(id_arquivo, texto)] -> (data da prestação, id do arquivo do relatório)."""
    rel = [(i, t) for i, t in lidos if RELATORIO.search(t)]
    base = rel or lidos
    melhor = None
    for i, t in base:
        d = next((x for x in datas_assinatura(t) if x >= retorno), None)
        if d and (melhor is None or d < melhor[0]):
            melhor = (d, i)
    if melhor:
        return melhor
    return None, (rel[0][0] if rel else None)


def e_estimativa(v: dict) -> bool:
    """Diária 'por estimativa': cobre um período (ex.: motoristas que levam pacientes o mês inteiro),
    não uma viagem só. Fica fora da conta do prazo."""
    try:
        dias = (dt.date.fromisoformat(v["data_retorno"]) - dt.date.fromisoformat(v["data_saida"])).days
    except (TypeError, ValueError, KeyError):
        return False
    return dias > 20 or bool(re.search(r"\bestimativa\b", v.get("motivo") or "", re.I))


def completar(get_json, get_arquivo, viagens: list[dict], anteriores: dict[str, dict], api: str,
              avisos: list[str], log, threads: int = 4, max_minutos: float = 20, hoje: dt.date | None = None) -> None:
    """Preenche v['prestacao'] e v['estimativa'] nas diárias (tipo 'Diária') de `viagens`.

    get_json(path, exercicio, entidade) -> json ; get_arquivo(path) -> bytes
    anteriores = {id da viagem: prestacao da coleta anterior}
    """
    hoje = hoje or dt.date.today()
    inicio = time.monotonic()
    so_reaproveitar = max_minutos is not None and max_minutos < 0  # --sem-prestacao
    limite = max_minutos * 60 if max_minutos and max_minutos > 0 else None
    pendentes = []
    reaproveitadas = 0
    for v in viagens:
        if v.get("tipo") != "Diária":
            continue
        v["estimativa"] = e_estimativa(v)
        ant = anteriores.get(v["id"]) or {}
        if ant.get("data"):
            v["prestacao"] = {k: ant[k] for k in ("data", "arquivos", "link") if k in ant}
            reaproveitadas += 1
            continue
        try:
            retorno = dt.date.fromisoformat(v.get("data_retorno") or "")
        except ValueError:
            retorno = None
        if not v.get("id_empenho") or retorno is None or retorno >= hoje:
            # viagem ainda não terminou: nada para procurar
            v["prestacao"] = {"data": None, "arquivos": ant.get("arquivos", 0), "link": ant.get("link", "")}
            continue
        v["prestacao"] = {"data": None, "arquivos": ant.get("arquivos", 0), "link": ant.get("link", ""),
                          "lidos": list(ant.get("lidos") or [])}
        if not so_reaproveitar:
            pendentes.append((v, retorno))

    def tratar(item):
        v, retorno = item
        p = v["prestacao"]
        if limite and time.monotonic() - inicio > limite:
            p["nao_lido"] = True
            return 0
        arqs = get_json(f"/api/diarias-prestacao-conta/arquivos?idEmpenho={v['id_empenho']}",
                        v["ano"], v["entidade_id"]) or []
        p["arquivos"] = len(arqs)
        if not arqs:
            p.pop("lidos", None)
            return 0
        ja = set(p.get("lidos") or [])
        lidos, baixados = [], 0
        for a in arqs:
            i = a.get("idArquivo")
            if not i or i in ja:
                continue
            lidos.append((i, texto_pdf(get_arquivo(f"/api/files/arquivo/{i}"))))
            baixados += 1
        data, arq = escolher_data(lidos, retorno)
        if arq is None and not lidos and arqs:
            arq = arqs[0].get("idArquivo")
        if arq:
            p["link"] = f"{api}/api/files/arquivo/{arq}"
        if data:
            p["data"] = data.isoformat()
            p.pop("lidos", None)
        else:
            p["lidos"] = sorted(ja | {i for i, _ in lidos})
        return baixados

    pdfs = 0
    falhas = 0
    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        futuros = [ex.submit(tratar, it) for it in pendentes]
        for n, f in enumerate(as_completed(futuros), 1):
            try:
                pdfs += f.result()
            except Exception:  # noqa: BLE001  (uma diária com erro não derruba a coleta)
                falhas += 1
            if n % 200 == 0:
                log(f"  prestação de contas: {n}/{len(pendentes)} diárias verificadas, {pdfs} PDFs lidos")
    for v in viagens:
        p = v.get("prestacao")
        if p is not None and not p.get("lidos"):
            p.pop("lidos", None)
    nao = sum(1 for v in viagens if (v.get("prestacao") or {}).get("nao_lido"))
    log(f"  prestação de contas: {reaproveitadas} reaproveitadas, {len(pendentes)} verificadas, "
        f"{pdfs} PDFs lidos, {falhas} com erro, {nao} para a próxima coleta")
    if nao or falhas:
        avisos.append(f"Prestação de contas: {nao + falhas} diárias ainda não verificadas; completam na próxima "
                      "atualização")
