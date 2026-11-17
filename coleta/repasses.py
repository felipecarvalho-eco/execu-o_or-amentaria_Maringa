"""Repasses financeiros da Prefeitura às outras entidades do Município (Câmara, autarquias, previdência).

As autarquias e a Câmara vivem principalmente do dinheiro que a Prefeitura transfere a elas
(duodécimo da Câmara, repasses do Tesouro às autarquias, aportes à Maringá Previdência). Esse
dinheiro não é receita orçamentária de quem recebe: é uma "transferência financeira" (interferência
financeira). Por isso ele não aparece em /api/receitas/detalhada, e sim no módulo "Repasses" do portal
(menu Receitas > Repasses recebidos e Despesas > Repasses concedidos).

Endpoints (os mesmos que o portal usa):
  /api/repasses?tipo=E|R&entidade=X&exercicio=A&mesInicial=01&mesFinal=12
      um registro por entidade de origem/destino, com valorPrevistoRepassar (previsto no ano)
      e valorLancado (quanto foi transferido no período).
  /api/repasses/mes?tipo=E|R&entidade=X&exercicio=A&mesInicial=01&mesFinal=12&entidadeDestinoOrigem=Y
      um registro por mês, com valorPrevisto (cronograma do mês) e valorRecebido (transferido no mês).

Regras adotadas:
  - previsto: o que a Prefeitura prevê repassar à entidade no ano (visão da Prefeitura, tipo=E).
    Em 2025 só o total anual está preenchido; em 2026 há também o cronograma mês a mês.
  - recebido: o que a entidade registrou como recebido da Prefeitura em cada mês (visão de quem
    recebe, tipo=R). Em 2026 bate com o que a Prefeitura registrou como enviado; em 2025 a
    Prefeitura só registrou de agosto em diante, enquanto a Câmara registrou o ano todo.
  - devolvido: o que a entidade devolveu à Prefeitura (ex.: sobra do duodécimo da Câmara em dezembro).
"""
from __future__ import annotations

PREFEITURA = 1
TOL = 0.05


def _r2(v: float) -> float:
    v = round(float(v or 0), 2)
    return 0.0 if v == 0 else v


def _por_mes(lista, campo: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for x in lista or []:
        m = x.get("mes")
        if m:
            out[int(m)] = out.get(int(m), 0.0) + float(x.get(campo) or 0)
    return out


def coletar(get, anos_meses: dict[int, list[int]], destinos: dict[int, str], avisos: list, log) -> list[dict]:
    """get(path, exercicio, params, entidade) -> json.
    anos_meses: {ano: [meses a gravar]}; destinos: {id da entidade: nome curto} (sem a Prefeitura)."""
    linhas: list[dict] = []
    for ano, meses in sorted(anos_meses.items()):
        if not meses:
            continue
        log(f"Repasses {ano}")
        base = {"entidade": PREFEITURA, "exercicio": ano, "mesInicial": "01", "mesFinal": "12"}
        anual = get("/api/repasses", ano, {**base, "tipo": "E"}, PREFEITURA) or []
        prev_anual: dict[int, float] = {}
        for r in anual:
            d = r.get("entidadeDestinoOrigem")
            if d is not None:
                prev_anual[int(d)] = prev_anual.get(int(d), 0.0) + float(r.get("valorPrevistoRepassar") or 0)
        for eid, nome in sorted(destinos.items()):
            if eid == PREFEITURA:
                continue
            p_ent = {**base, "entidade": eid, "entidadeDestinoOrigem": PREFEITURA}
            try:
                rec = _por_mes(get("/api/repasses/mes", ano, {**p_ent, "tipo": "R"}, eid), "valorRecebido")
                dev = _por_mes(get("/api/repasses/mes", ano, {**p_ent, "tipo": "E"}, eid), "valorRecebido")
            except RuntimeError as e:  # entidade sem o exercício no portal
                log(f"  repasses {nome}/{ano}: {e}")
                continue
            pref = get("/api/repasses/mes", ano, {**base, "tipo": "E", "entidadeDestinoOrigem": eid}, PREFEITURA) or []
            env = _por_mes(pref, "valorRecebido")
            crono = _por_mes(pref, "valorPrevisto")
            previsto = prev_anual.get(eid, 0.0)
            if not previsto:  # algumas entidades só registram a previsão do lado de quem recebe
                previsto = sum(float(r.get("valorPrevistoReceber") or 0) for r in
                               (get("/api/repasses", ano, {**base, "entidade": eid, "tipo": "R"}, eid) or []))
            # o cronograma só é usado quando fecha com a previsão anual (em 2025 vem incompleto ou errado)
            usa_crono = previsto > 0 and abs(sum(crono.values()) - previsto) <= TOL
            if not previsto and not any(rec.values()):
                continue
            t_rec, t_env = sum(rec.values()), sum(env.values())
            if abs(t_rec - t_env) > TOL:
                sem = [m for m in range(1, 13) if rec.get(m) and not env.get(m)]
                msg = (f"Repasses {ano} a {nome}: a entidade registrou {t_rec:.2f} recebidos e a Prefeitura "
                       f"{t_env:.2f} enviados")
                if sem:
                    msg += f" (a Prefeitura não registrou os meses {', '.join(map(str, sem))})"
                msg += "; o painel usa o valor registrado por quem recebe"
                log("  aviso: " + msg)
                avisos.append(msg)
            for m in meses:
                linhas.append({
                    "ano": ano, "mes": m, "entidade": nome, "entidade_id": eid,
                    "origem": "Prefeitura (administração direta)",
                    "previsto": _r2(previsto),
                    "previsto_mes": _r2(crono.get(m, 0)) if usa_crono else None,
                    "recebido": _r2(rec.get(m, 0)),
                    "devolvido": _r2(dev.get(m, 0)),
                    "enviado_prefeitura": _r2(env.get(m, 0)),
                })
            fora = sum(v for m, v in rec.items() if m not in meses)
            if abs(fora) > TOL:
                log(f"  repasses {nome}/{ano}: {fora:.2f} em meses ainda não fechados ignorados")
    return linhas
