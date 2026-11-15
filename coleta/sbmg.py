"""Números da SBMG (Terminais Aéreos de Maringá S/A) a partir dos balancetes mensais em PDF.

A SBMG é uma empresa pública com contabilidade empresarial: o portal não traz a execução
orçamentária dela, mas publica em "Publicações > Balancetes Mensais" um balancete simplificado
por mês (PDF de texto, gerado pelo sistema Exactus/Excelsus). Este módulo baixa esses PDFs e lê as
contas principais.

Cada linha do balancete tem: Saldo Anterior, Débito, Crédito, Saldo Atual e o nome da conta.
- Contas de resultado (receita, custos, lucro): o saldo é acumulado no ano; o valor do mês é
  Saldo Atual - Saldo Anterior.
- Contas patrimoniais (caixa, ativo, dívida, patrimônio): usa-se o Saldo Atual (posição no fim do mês).
"""
from __future__ import annotations

import io
import re

ENTIDADE_SBMG = 10
NOME_SBMG = "SBMG - Terminais Aéreos"

_NUM = r"\(?-?[\d\.]+,\d{2}\)?"
_NUM_ANTES = re.compile(rf"^\s*({_NUM})\s+({_NUM})\s+({_NUM})\s*({_NUM})\s+(.+?)\s*$")
_NUM_DEPOIS = re.compile(rf"^\s*(.+?)\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s*$")
_PERIODO = re.compile(r"BALANCETE\s*-\s*\d{2}/(\d{2})/(\d{4})\s*a\s*\d{2}/(\d{2})/(\d{4})")

# campo de saída -> (nomes de conta no balancete, tipo). Usa a primeira ocorrência de cada nome.
FLUXOS = {
    "receita_liquida": ["RECEITA LIQUIDA"],
    "custo_servicos": ["CUSTO DOS SERVIÇOS PRESTADOS", "CUSTO DOS SERVICOS PRESTADOS"],
    "despesas_operacionais": ["DESPESAS OPERACIONAIS"],
    "resultado_financeiro": ["RESULTADO FINANCEIRO LIQUIDO"],
    "ir_csll": ["PROVISOES P/IRPJ E CSLL"],
    "lucro_liquido": ["CONTAS DE RESULTADO"],
}
SALDOS = {
    "caixa": ["CAIXA E EQUIVALENTES DE CAIXA"],
    "ativo_total": ["ATIVO"],
    "emprestimos_curto_prazo": ["EMPRESTIMOS OU FINANCIAMENTOS"],
    "emprestimos_longo_prazo": ["EMPRESTIMOS OU FINANCIAMENTOS - LP"],
    "patrimonio_liquido": ["PATRIMONIO LIQUIDO"],
}


def _valor(s: str) -> float:
    neg = s.startswith("(") or s.startswith("-")
    x = float(s.strip("()-").replace(".", "").replace(",", "."))
    return -x if neg else x


def ler_balancete(conteudo_pdf: bytes) -> tuple[int, int, dict[str, list[float]]]:
    """Devolve (ano, mês, {conta: [saldo_anterior, debito, credito, saldo_atual]})."""
    from pypdf import PdfReader  # importado aqui para a coleta principal não depender do pypdf

    texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(conteudo_pdf)).pages)
    m = _PERIODO.search(texto)
    if not m or m.group(1) != m.group(3) or m.group(2) != m.group(4):
        raise ValueError("período do balancete não encontrado ou não é de um mês só")
    contas: dict[str, list[float]] = {}
    for linha in texto.splitlines():
        a = _NUM_ANTES.match(linha)
        if a:
            nome, vals = a.group(5), a.groups()[:4]
        else:
            b = _NUM_DEPOIS.match(linha)
            if not b or b.group(1).startswith("Título"):
                continue
            nome, vals = b.group(1), b.groups()[1:]
        contas.setdefault(nome.strip().upper(), [_valor(v) for v in vals])
    return int(m.group(2)), int(m.group(1)), contas


def _achar(contas: dict, nomes: list[str]) -> list[float] | None:
    for n in nomes:
        if n in contas:
            return contas[n]
    return None


def linha_do_mes(ano: int, mes: int, contas: dict, fonte: str) -> dict:
    reg = {"entidade": NOME_SBMG, "ano": ano, "mes": mes}
    for campo, nomes in FLUXOS.items():
        c = _achar(contas, nomes)
        reg[campo] = round(c[3] - c[0], 2) if c else None
    for campo, nomes in SALDOS.items():
        c = _achar(contas, nomes)
        reg[campo] = round(c[3], 2) if c else None
    reg["fonte"] = fonte
    # conferência: o lucro do mês tem de ser a soma das parcelas
    partes = [reg[k] for k in ("receita_liquida", "custo_servicos", "despesas_operacionais",
                               "resultado_financeiro", "ir_csll")]
    if None in partes or reg["lucro_liquido"] is None or reg["receita_liquida"] is None:
        raise ValueError("contas principais ausentes no balancete")
    outras = round(reg["lucro_liquido"] - sum(partes), 2)
    reg["outros_resultados"] = outras  # ex.: receitas e despesas eventuais
    return reg


def coletar(get_json, get_bytes, anos: list[int], api: str, avisos: list[str], log) -> list[dict]:
    """Baixa e lê os balancetes dos anos pedidos. Falhas viram avisos (a SBMG é opcional)."""
    try:
        grupos = get_json("/api/publicacoes", max(anos), {"entidade": ENTIDADE_SBMG, "exercicio": max(anos)},
                          entidade=ENTIDADE_SBMG)
    except Exception as e:  # noqa: BLE001
        avisos.append(f"{NOME_SBMG}: não foi possível listar as publicações ({e})")
        return []
    grupo = next((g for g in grupos if "BALANCETE" in (g.get("valor") or "").upper()), None)
    if not grupo:
        avisos.append(f"{NOME_SBMG}: grupo 'Balancetes Mensais' não encontrado nas publicações")
        return []
    linhas = []
    for sub in grupo.get("list") or []:
        try:
            ano_sub = int((sub.get("valor") or "").strip())
        except ValueError:
            continue
        if ano_sub not in anos:
            continue
        for arq in sub.get("list") or []:
            nome = (arq.get("valor") or arq.get("nomeArquivo") or "").strip()
            url = f"{api}/api/files/arquivo/{arq['idArquivo']}"
            try:
                ano, mes, contas = ler_balancete(get_bytes(f"/api/files/arquivo/{arq['idArquivo']}", ano_sub,
                                                           entidade=ENTIDADE_SBMG))
                linhas.append(linha_do_mes(ano, mes, contas, url))
            except Exception as e:  # noqa: BLE001
                avisos.append(f"{NOME_SBMG}: não foi possível ler '{nome}' ({e})")
    # um balancete por mês (se houver republicação, fica o último da lista)
    unicos = {(l["ano"], l["mes"]): l for l in linhas}
    saida = [unicos[k] for k in sorted(unicos)]
    log(f"SBMG: {len(saida)} balancetes lidos")
    return saida
