"""Atas de registro de preços vigentes: o que cada entidade pode comprar, de quem e por quanto.

O portal publica as atas no módulo "Contratos / Atas" (filtro Tipo ato = Ata de Registro de Preços):

    /api/contratos?entidade=E&tipoAto=2&contratosVigentes=true&page=N&size=1000
        uma linha por ata e fornecedor (o mesmo número de ata é dividido entre vários fornecedores),
        com objeto, licitação de origem, vigência (atualizada pelos aditivos) e valor.
    /api/contratos/<idOxy>
        a mesma ata com os itens: lote, ordem, descrição, unidade, quantidade registrada,
        valor unitário e valor total.

O portal não publica o saldo de cada ata. Como estimativa do uso, somamos os empenhos do ano (de
qualquer entidade) que citam o número da ata e têm o mesmo fornecedor (CNPJ). Esses empenhos vêm do
gastos.js (detalhe de cada empenho). Empenhos de anos anteriores não entram.
"""
from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

TIPO_ATA = 2
LINK = "https://transparencia.maringa.pr.gov.br/portaltransparencia/{ent}/contratos/detalhes?id={id}"
COLUNAS_ITENS = ["lote", "ordem", "descricao", "unidade", "quantidade", "valor_unitario", "valor"]

# Categoria pelo objeto da ata (a primeira regra que casar vale). Serve só para filtrar no painel.
CATEGORIAS = [
    ("Saúde: medicamentos", r"medicament|farmac|vacina|formulas? (magistra|infant)|dietas? entera|suplementos? aliment|sensores? para monitoramento"),
    ("Saúde: material médico e laboratorial", r"medic[oa][- ]?hospitalar|hospitalar|materiais medicos|odontolog|laboratori|reagente|cirurg|campo operatorio|esteriliza|oxigenio|preservativo|clinic|oftalmolog|seringa|luvas? (nitril|de procedimento)|equipamentos? (clinicos|medico)|autoclave|glicemia|fraldas|aedes"),
    ("Animais e veterinária", r"veterinar|caes e gatos|animal|microchip"),
    ("Jardinagem e agricultura", r"mudas|sementes|fertilizante|substrato|sombreamento|stihl|rocadeira|motosserra|soprador|agricol|horta"),
    ("Obras, vias e máquinas", r"asfalt|pavimen|fresagem|concreto|sinalizacao|viari|caminh|maquinario|veiculos pesados|trator|retroescavadeira|drenagem|calcadas|meio-fio|sarjeta|cacamba|guindaste|terraplan|ensaios|semafor|calhas"),
    ("Veículos, combustível e pneus", r"veicul|pneu|combustive|borracharia|frota|motofrete|ambulancia"),
    ("Vestuário e EPI", r"uniforme|calcado|\bepi|protecao individual|protecao e de acessorios|tenis|vestuario|coturno|balistic"),
    ("Alimentação", r"alimenticio|alimentos?|hortifrut|carne|panifica|lacticin|laticin|refeic|marmitex|\bcafe\b"),
    ("Material de expediente e impressos", r"expediente|papelaria|impressos|formularios|cartuchos|toner"),
    ("Informática e software", r"informatica|software|licenca|tablet|certificado digital|cabeamento|tecnologic|rack"),
    ("Eventos, som e estruturas", r"evento|sonoriza|tendas?|tablado|palco|decorac|apresentacoes artisticas|trofeus|medalhas|arbitragem|banheiro"),
    ("Comunicação visual e vídeo", r"comunicacao visual|video|gravacao|camisetas personalizadas|cordoes|cracha"),
    ("Manutenção predial", r"eletric|hidraul|pintura|tintas?|marcenaria|serralheria|vidracaria|ferrage|ferramenta|construcao|forro|divisoria|toldo|telas?|cubas|chaveiro|desentup|caixas? d.agua|dedetiza|descupin|extintor|hidrante|piscina|impermeabiliza|padroes de energia|cortinas|persianas|abelhas|reparos|solda|siderurgic|tampoes|grelhas"),
    ("Limpeza, higiene e descartáveis", r"limpeza|higien|descartave|copos|sacos plasticos|detergente|conteineres|enxoval"),
    ("Mobiliário e eletrodomésticos", r"mobiliari|eletrodomest|eletroportat|bebedouro|colchoes|cama, mesa|cama mesa|camas|utensilios|filtros"),
    ("Educação, esporte e cultura", r"escolar|esportiv|musica|musicaliza|livros?|pedagogic|brinquedo|academias|artesanato|aviamentos|educativ|oficinas"),
    ("Viagens e transporte", r"passagens|hospedagem|transporte|frete|mudanca"),
]
_CAT = [(n, re.compile(p)) for n, p in CATEGORIAS]


def _sem_acento(t) -> str:
    return unicodedata.normalize("NFKD", str(t or "").lower()).encode("ascii", "ignore").decode()


def _txt(s) -> str:
    return " ".join(str(s or "").split())


def categoria(objeto: str) -> str:
    o = _sem_acento(objeto)
    for nome, rx in _CAT:
        if rx.search(o):
            return nome
    return "Outros"


def _n(v) -> float:
    try:
        return round(float(v or 0), 4)
    except (TypeError, ValueError):
        return 0.0


def uso_por_ata(gastos: list[dict] | None) -> dict[tuple[str, str], dict]:
    """Soma, por (número/ano da ata, CNPJ do fornecedor), o empenhado menos anulado no ano e quem empenhou."""
    uso: dict[tuple[str, str], dict] = {}
    for g in gastos or []:
        k = (str(g.get("contrato") or ""), re.sub(r"\D", "", str(g.get("credor_documento") or "")))
        if not k[0] or not k[1]:
            continue
        u = uso.setdefault(k, {"valor": 0.0, "por_entidade": {}})
        v = float(g.get("empenhado") or 0) - float(g.get("anulado") or 0)
        u["valor"] += v
        e = g.get("entidade") or ""
        u["por_entidade"][e] = u["por_entidade"].get(e, 0.0) + v
    return uso


def coletar(get, entidades: dict[int, str], ano: int, gastos: list[dict] | None, avisos: list, log,
            threads: int = 6) -> list[dict]:
    """get(path, exercicio, params, entidade) -> json. entidades: {id: nome curto}."""
    lista = []
    for eid, nome in sorted(entidades.items()):
        pagina = 0
        while True:
            r = get("/api/contratos", ano, {"entidade": eid, "tipoAto": TIPO_ATA, "contratosVigentes": "true",
                                            "page": pagina, "size": 1000}, eid)
            lista += [(eid, nome, a) for a in r.get("content") or []]
            if r.get("last", True) or not r.get("content"):
                break
            pagina += 1
    log(f"Atas de registro de preços vigentes: {len(lista)}")

    def detalhe(item):
        eid, nome, a = item
        try:
            return item, get(f"/api/contratos/{a['idOxy']}", ano, {}, eid)
        except RuntimeError as e:  # sem os itens, a ata continua na lista
            return item, {"_erro": str(e)}

    uso = uso_por_ata(gastos)
    atas, sem_itens = [], 0
    with ThreadPoolExecutor(max_workers=threads) as ex:
        for fut in as_completed([ex.submit(detalhe, x) for x in lista]):
            (eid, nome, a), d = fut.result()
            if d.get("_erro"):
                sem_itens += 1
            itens = [[i.get("lote"), i.get("ordem"), _txt(i.get("descricao")), _txt(i.get("unidade")),
                      _n(i.get("quantidade")), _n(i.get("valorUnitario")), round(float(i.get("valor") or 0), 2)]
                     for i in d.get("itens") or []]
            itens.sort(key=lambda i: (i[0] or 0, i[1] or 0))
            numero = f"{a.get('numeroContrato')}/{a.get('exercicio')}"
            cnpj = re.sub(r"\D", "", str(a.get("cnpjCpf") or ""))
            u = uso.get((numero, cnpj), {"valor": 0.0, "por_entidade": {}})
            objeto = _txt(a.get("objeto"))
            atas.append({
                "id": a.get("idOxy"),
                "entidade": nome,
                "ata": numero,
                "fornecedor": _txt(a.get("nome")),
                "cnpj": cnpj,
                "objeto": objeto,
                "categoria": categoria(objeto),
                "tipo": _txt(a.get("tipoContrato")),
                "licitacao": f"{_txt(a.get('descricaoTipoLicitacao'))} {a.get('numeroLicitacao')}/{a.get('exercicioLicitacao')}".strip(),
                "inicio": a.get("inicioVigencia"),
                "vigencia": a.get("vigencia") or a.get("terminoVigencia"),
                "valor": round(float(a.get("valorContratado") or 0) + float(a.get("valorAditivo") or 0), 2),
                "empenhado_ano": round(u["valor"], 2),
                "empenhado_por_entidade": {k: round(v, 2) for k, v in sorted(u["por_entidade"].items()) if round(v, 2)},
                "link": LINK.format(ent=eid, id=a.get("idOxy")),
                "itens": itens,
            })
    if sem_itens:
        avisos.append(f"Atas: {sem_itens} atas sem os itens nesta atualização (o portal não respondeu)")
    atas.sort(key=lambda a: (a["vigencia"] or "", a["ata"], a["fornecedor"]))
    return atas


def itens_csv(atas: list[dict]) -> list[dict]:
    """Uma linha por item, para o CSV."""
    out = []
    for a in atas:
        base = {k: a[k] for k in ("entidade", "ata", "fornecedor", "cnpj", "categoria", "licitacao", "inicio",
                                  "vigencia", "objeto")}
        for i in a["itens"] or [[None] * len(COLUNAS_ITENS)]:
            out.append({**base, **dict(zip(COLUNAS_ITENS, i)), "valor_ata": a["valor"],
                        "empenhado_ano_na_ata": a["empenhado_ano"]})
    return out
