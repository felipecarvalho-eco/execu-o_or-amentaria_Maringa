"""Prestação de contas das diárias: quando o servidor entregou o relatório de viagem.

O portal não tem um campo com a data da prestação de contas. Ele publica, para cada diária, os arquivos
da prestação (em geral o "Relatório de Viagem" assinado no SEI):

    /api/diarias-prestacao-conta/arquivos?idEmpenho=<idEmpenho>     lista nome e id de cada PDF (sem data)
    /api/files/arquivo/<idArquivo>                                  o PDF (não precisa de cabeçalhos)

A data é lida do texto do PDF, na linha da assinatura eletrônica do SEI:
"Documento assinado eletronicamente por <nome>, <cargo>, em DD/MM/AAAA". Usamos a primeira assinatura
feita no dia do retorno ou depois (a do próprio servidor; as da chefia vêm depois). Se o relatório não
tem assinatura legível, vale a data escrita nele ("Maringá, 22 de janeiro de 2026").

Só contam os arquivos que são o relatório: "Relatório de Viagem", "Relatório Unificado de Missão",
"Relatório de Visita Técnica", "Relatório Técnico", pedido de diária intempestiva etc. Um relatório
assinado só antes da saída é de outra viagem e é ignorado. Quando o portal só publica outros documentos
(requisição da viagem, notas fiscais), a diária fica marcada como sem relatório (`sem_relatorio`).
Quando nenhum arquivo é identificado como relatório, vale a primeira assinatura no retorno ou depois
em qualquer arquivo, como antes.

PDF escaneado (página sem texto) é lido com OCR (tesseract, idioma por), se estiver instalado.

Incremental: a data lida não muda. Por isso cada coleta reaproveita o resultado da anterior (campo
`prestacao` do diarias.json) e só consulta o portal para as diárias ainda sem data. Arquivos já lidos
sem data legível também não são baixados de novo, salvo quando a forma de ler muda (LEITURA) ou
quando o arquivo veio vazio.

Campo `prestacao` gravado em cada diária:
  data      "AAAA-MM-DD" ou null   primeira assinatura do relatório no retorno ou depois
  arquivos  int                    quantos arquivos de prestação o portal publica
  link      url ou ""              PDF do relatório no portal
  lidos     [ids]                  arquivos já lidos sem data legível (só quando data é null)
  rel       true                   algum arquivo lido é o relatório de viagem (só quando data é null)
  sem_relatorio true               há arquivos, mas nenhum é o relatório desta viagem
  leitura   int                    versão da forma de ler (LEITURA) usada em `lidos`
  nao_lido  true                   o tempo da coleta acabou antes; completa na próxima
"""
from __future__ import annotations

import datetime as dt
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# o "em" antes da data é opcional: o OCR de PDFs escaneados às vezes o perde
ASSINATURA = re.compile(r"assinad[oa]\s+eletronicamente\s+por\s.{0,600}?(\d{2}/\d{2}/\d{4})", re.S | re.I)
# títulos do relatório; "vi?a?gem" cobre erros de digitação como "Relatório de Vigem"
RELATORIO = re.compile(r"relat[óo]rio\s+(de\s+v\w{0,3}gem|unificado|de\s+miss[ãa]o|de\s+visita|t[ée]cnico|de\s+atividades"
                       r"|de\s+representa)", re.I)
# pedido de diária intempestiva: traz o relato da viagem quando não há relatório próprio (vale só na falta dele)
INTEMPESTIVA = re.compile(r"intempestiv", re.I)
DATA_LOCAL = re.compile(r"maring[áa]\s*[-–/]?\s*(?:pr)?\s*,\s*(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", re.I)
MESES = {m: i + 1 for i, m in enumerate("janeiro fevereiro março abril maio junho julho agosto setembro outubro "
                                        "novembro dezembro".split())}
MAX_PAGINAS = 30
MAX_PAGINAS_OCR = 15
LEITURA = 2  # muda quando a forma de ler os PDFs melhora: os arquivos sem data são lidos de novo
TEM_OCR = bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def ocr_pagina(conteudo: bytes, pagina: int) -> str:
    """Texto de uma página escaneada (1 = primeira), via pdftoppm + tesseract."""
    with tempfile.TemporaryDirectory() as d:
        pdf = os.path.join(d, "a.pdf")
        with open(pdf, "wb") as f:
            f.write(conteudo)
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(pagina), "-l", str(pagina), pdf,
                        os.path.join(d, "p")], check=True, capture_output=True, timeout=120)
        img = next((os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".png")), None)
        if not img:
            return ""
        r = subprocess.run(["tesseract", img, "-", "-l", "por", "--psm", "3"], capture_output=True, timeout=180,
                           env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        return r.stdout.decode("utf-8", "replace")


def precisa_ocr(conteudo: bytes) -> bool:
    """Há página sem texto (escaneada)?"""
    from pypdf import PdfReader
    try:
        return any(len((p.extract_text() or "").strip()) < 80
                   for p in list(PdfReader(io.BytesIO(conteudo)).pages)[:MAX_PAGINAS])
    except Exception:  # noqa: BLE001
        return False


def texto_pdf(conteudo: bytes, ocr: bool = False) -> str:
    """Texto do PDF; com ocr=True, as páginas sem texto são lidas por OCR."""
    from pypdf import PdfReader  # importado aqui para a coleta principal não depender do pypdf
    try:
        paginas = list(PdfReader(io.BytesIO(conteudo)).pages)[:MAX_PAGINAS]
    except Exception:  # noqa: BLE001  (PDF corrompido ou protegido: fica sem data)
        return ""
    textos, n_ocr = [], 0
    for n, p in enumerate(paginas, 1):
        try:
            t = p.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        if ocr and len(t.strip()) < 80 and TEM_OCR and n_ocr < MAX_PAGINAS_OCR:
            n_ocr += 1
            try:
                t = t + "\n" + ocr_pagina(conteudo, n)
            except Exception:  # noqa: BLE001  (OCR falhou: segue com o que tem)
                pass
        textos.append(t)
    return "\n".join(textos)


def datas_assinatura(texto: str) -> list[dt.date]:
    saida = set()
    for s in ASSINATURA.findall(texto):
        try:
            saida.add(dt.datetime.strptime(s, "%d/%m/%Y").date())
        except ValueError:
            pass
    return sorted(saida)


def datas_texto(texto: str) -> list[dt.date]:
    """Datas escritas por extenso no fecho do documento: "Maringá, 22 de janeiro de 2026"."""
    saida = set()
    for d, m, a in DATA_LOCAL.findall(texto):
        m = MESES.get(m.lower().replace("marco", "março"))
        try:
            saida.add(dt.date(int(a), m, int(d)))
        except (TypeError, ValueError):
            pass
    return sorted(saida)


def escolher_data(lidos: list[tuple[str, str]], retorno: dt.date,
                  saida: dt.date | None = None) -> tuple[dt.date | None, str | None, bool, bool]:
    """lidos = [(id_arquivo, texto)] -> (data, id do arquivo do relatório, há relatório, veio do relatório próprio).

    Ordem: o relatório de viagem; na falta dele, o pedido de diária intempestiva; na falta dos dois,
    a primeira assinatura no retorno ou depois em qualquer arquivo."""
    saida = saida or retorno

    def deste(t):
        ass = datas_assinatura(t)
        return ass, not (ass and max(ass) < saida)  # assinado só antes da saída: é de outra viagem

    def data_de(grupo):
        cands = [(d, i) for i, t, ass in grupo for d in ass if d >= retorno]
        if not cands:
            cands = [(d, i) for i, t, _ in grupo for d in datas_texto(t) if d >= retorno]
        return min(cands) if cands else None

    fortes, fracos = [], []
    for i, t in lidos:
        ass, ok = deste(t)
        if not ok:
            continue
        if RELATORIO.search(t):
            fortes.append((i, t, ass))
        elif INTEMPESTIVA.search(t):
            fracos.append((i, t, ass))
    for grupo, forte in ((fortes, True), (fracos, False)):
        achou = data_de(grupo)
        if achou:
            return achou[0], achou[1], True, forte
    rel = fortes or fracos
    if rel:
        return None, rel[0][0], True, False
    melhor = None
    for i, t in lidos:
        d = next((x for x in datas_assinatura(t) if x >= retorno), None)
        if d and (melhor is None or d < melhor[0]):
            melhor = (d, i)
    if melhor:
        return melhor[0], melhor[1], True, False
    return None, None, False, False


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
        mesma = ant.get("leitura") == LEITURA  # leitura antiga: lê de novo todos os arquivos
        v["prestacao"] = {"data": None, "arquivos": ant.get("arquivos", 0), "link": ant.get("link", ""),
                          "lidos": list(ant.get("lidos") or []) if mesma else [],
                          "rel": bool(ant.get("rel")) if mesma else False}
        if not so_reaproveitar:
            pendentes.append((v, retorno))

    def tratar(item):
        v, retorno = item
        p = v["prestacao"]
        try:
            saida = dt.date.fromisoformat(v.get("data_saida") or "")
        except ValueError:
            saida = retorno
        if limite and time.monotonic() - inicio > limite:
            p["nao_lido"] = True
            return 0
        arqs = get_json(f"/api/diarias-prestacao-conta/arquivos?idEmpenho={v['id_empenho']}",
                        v["ano"], v["entidade_id"]) or []
        p["arquivos"] = len(arqs)
        if not arqs:
            p.pop("lidos", None)
            p.pop("rel", None)
            return 0
        ja = set(p.get("lidos") or [])
        lidos, brutos, baixados = [], {}, 0
        for a in arqs:
            i = a.get("idArquivo")
            if not i or i in ja:
                continue
            brutos[i] = get_arquivo(f"/api/files/arquivo/{i}")
            lidos.append((i, texto_pdf(brutos[i])))
            baixados += 1
        data, arq, rel, forte = escolher_data(lidos, retorno, saida)
        escaneados = [i for i in brutos if precisa_ocr(brutos[i])] if TEM_OCR and not forte else []
        if escaneados:
            # OCR é lento: só quando a data não veio do relatório próprio, e só nos PDFs escaneados
            lidos = [(i, texto_pdf(brutos[i], ocr=True) if i in escaneados else t) for i, t in lidos]
            data, arq, rel, forte = escolher_data(lidos, retorno, saida)
        rel = rel or p.get("rel", False)
        if arq is None and not p.get("link") and arqs:
            arq = arqs[0].get("idArquivo")
        if arq:
            p["link"] = f"{api}/api/files/arquivo/{arq}"
        if data:
            p["data"] = data.isoformat()
            for k in ("lidos", "rel", "sem_relatorio", "leitura"):
                p.pop(k, None)
        else:
            # arquivo que veio vazio (download falho ou PDF vazio) não entra em `lidos`: tenta de novo na próxima
            p["lidos"] = sorted(ja | {i for i, t in lidos if t.strip()})
            p["rel"] = rel
            p["leitura"] = LEITURA
            if rel:
                p.pop("sem_relatorio", None)
            else:
                p["sem_relatorio"] = True
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
        if p is not None and not p.get("rel"):
            p.pop("rel", None)
    nao = sum(1 for v in viagens if (v.get("prestacao") or {}).get("nao_lido"))
    log(f"  prestação de contas: {reaproveitadas} reaproveitadas, {len(pendentes)} verificadas, "
        f"{pdfs} PDFs lidos, {falhas} com erro, {nao} para a próxima coleta")
    if nao or falhas:
        avisos.append(f"Prestação de contas: {nao + falhas} diárias ainda não verificadas; completam na próxima "
                      "atualização")
