# Coleta: esquema dos dados

O script `coleta/coletar.py` baixa a execução orçamentária da **Prefeitura do Município de Maringá-PR**
do [Portal da Transparência](https://transparencia.maringa.pr.gov.br/portaltransparencia/1/), usando a
API pública que o próprio portal usa (`https://maringa.oxy.elotech.com.br/portaltransparencia-api`).
Não precisa de login nem de chave.

## Como rodar

```bash
pip install -r coleta/requirements.txt
python coleta/coletar.py --saida docs/dados                 # ano anterior + ano atual (padrão)
python coleta/coletar.py --saida docs/dados --anos 2023 2024 2025 2026
python coleta/coletar.py --saida docs/dados --com-empenhos  # grava também empenhos.csv (~20 MB/ano)
python coleta/coletar.py --saida docs/dados --sem-credores  # pula empenhos/credores (mais rápido)
```

- Leva cerca de 7 minutos para dois anos.
- **Idempotente:** cada execução baixa tudo de novo e regrava os mesmos arquivos. Rodar duas vezes no mesmo
  dia dá o mesmo resultado (só muda `meta.atualizado_em`).
- **Seguro:** os arquivos só são gravados no fim, de forma atômica. Se algo falhar (portal fora do ar,
  resposta vazia, totais que não batem), o script sai com código 1 e os arquivos anteriores ficam intactos.
- **Validação:** em cada mês confere se a soma das linhas detalhadas é igual ao total do portal. Diferenças de
  até 0,01% viram aviso em `meta.avisos` (o portal tem, por exemplo, R$ 4,48 de diferença interna na receita
  de 2026); diferenças maiores interrompem a coleta.

## Regras gerais

- Valores em reais, número com ponto decimal, duas casas.
- `ano` = exercício; `mes` = 1 a 12. O mês corrente é parcial (indicado em `meta.mes_corrente_parcial`).
- **Fluxos** (`empenhado`, `liquidado`, `pago`, `arrecadado`, `arrecadado_bruto`, `deducoes`) são o
  **movimento do mês**, não o acumulado. Podem ser negativos (anulações e estornos). Somando os meses
  obtém-se o acumulado do ano.
- **Posições** (`dotacao_inicial`, `dotacao_atualizada`, `previsto_inicial`, `previsto`) são o saldo
  **no fim daquele mês**. Uma combinação só aparece num mês se algo mudou nela (ou no primeiro mês do ano);
  para saber a dotação atual de uma combinação, use a linha do **último mês** em que ela aparece.
- Textos ausentes = `"Não informado"`.
- Não inclui restos a pagar de exercícios anteriores.

## Arquivos

| Arquivo | Para quê | Detalhe |
|---|---|---|
| `execucao.js` / `execucao.json` | lido pelo painel | `window.DADOS_EXECUCAO = {meta, despesas, receitas}` (agregado) |
| `credores.js` / `credores.json` | lido pelo painel (aba de credores) | `window.DADOS_CREDORES = {meta, credores}` |
| `despesas.csv` | análise detalhada | menor nível: até unidade, ação, modalidade e fonte, com códigos |
| `receitas.csv` | análise detalhada | por natureza da receita (rubrica), com código |
| `credores.csv` | análise | mesmo conteúdo de `credores` |
| `empenhos.csv` | só com `--com-empenhos` | um empenho por linha |

### `meta`

| Campo | Descrição |
|---|---|
| `municipio`, `entidade`, `fonte`, `api` | identificação e links |
| `atualizado_em` | quando a coleta rodou (ISO 8601) |
| `portal_atualizado_despesas`, `portal_atualizado_receitas` | última atualização informada pelo portal |
| `anos` | exercícios coletados |
| `mes_corrente_parcial` | `"AAAA-MM"` do mês ainda em andamento |
| `observacoes` | texto com as regras acima |
| `avisos` | lista de diferenças pequenas encontradas na validação |
| `linhas_csv` | contagem de linhas dos CSVs |

### `despesas` (em `execucao.js`), agregada para o painel

`ano, mes, orgao, funcao, subfuncao, programa, categoria, grupo, elemento, fonte_recurso,
dotacao_inicial, dotacao_atualizada, empenhado, liquidado, pago`

- `categoria`: Despesas Correntes / Despesas de Capital. `grupo`: ex. Pessoal e Encargos Sociais.
- `elemento`: elemento de despesa (ex. Vencimentos e Vantagens Fixas - Pessoal Civil).
- `fonte_recurso`: fonte/destinação (ex. Recursos Ordinários (Livres)).
- Unidade orçamentária, ação e modalidade ficam só no CSV, para o arquivo do painel não ficar pesado.

### `despesas.csv` (detalhado)

Para cada nível há o nome e o código (`<nivel>_codigo`):
`orgao, unidade, funcao, subfuncao, programa, acao, categoria, grupo, modalidade, elemento, fonte_recurso`,
mais `programatica` (código completo, ex. `02.010.04.122.0002.2001.3.1.90.11.1000`), e os mesmos valores
acima. `elemento_codigo` é a natureza da despesa (ex. `3.1.90.11`).

### `receitas` (em `execucao.js`), agregada para o painel

`ano, mes, categoria, origem, fonte_recurso, previsto_inicial, previsto, arrecadado_bruto, deducoes, arrecadado`

- `categoria`: Receitas Correntes / Receitas de Capital. `origem`: ex. Impostos, Taxas e Contribuições de Melhoria.
- `arrecadado` = **líquido de deduções** (ex. retenção para o FUNDEB), comparável a `previsto`.
  `arrecadado_bruto` e `deducoes` (negativo) mostram a composição.
- `fonte_recurso` vem como "Não informado": a API detalhada da receita não informa a fonte por rubrica.

### `receitas.csv` (detalhado)

Mais `especie`, `rubrica` (nome da natureza no menor nível) e `natureza_codigo` (ex. `11125001`).

### `credores`

`ano, mes, credor_documento, credor, qtd_empenhos, empenhado, anulado, liquidado, pago`

- Empenhos agrupados por credor e **mês de emissão** do empenho.
- `credor_documento` = CNPJ/CPF como aparece no portal (CPFs vêm mascarados pelo próprio portal).
- `empenhado`/`anulado` = valores dos empenhos emitidos no mês. `liquidado`/`pago` = **posição atual**
  desses empenhos (não o que foi pago naquele mês).

### `empenhos.csv`

`ano, empenho (nº/ano), data, mes, credor_documento, credor, fonte_recurso, historico, empenhado, anulado,
liquidado, pago, a_pagar`

## Endpoints usados

Todos exigem os cabeçalhos HTTP `entidade: 1` e `exercicio: <ano>`.

| Dado | Endpoint |
|---|---|
| exercícios | `/api/exercicios/entidade/1` |
| despesa | `/despesapornivel/detalhada?entidade=1&exercicio=A&execucao=true&dataInicial=A-01-01&dataFinal=A-MM-DD` |
| receita | `/api/receitas/detalhada?entidade=1&exercicio=A&dataInicial=...&dataFinal=...` |
| empenhos | `/empenhos/lista?search=id.exercicio=='A' and id.entidade=='1' and data>='...' and data<='...'&entidade=1&page=N&size=2000` |
| última atualização | `/api/ultimas-atualizacoes/last-update-despesas` e `-receitas` |

A API devolve valores do intervalo de datas pedido (inclusive créditos adicionais). Por isso o script pede
sempre de 1º de janeiro até o fim de cada mês e calcula o movimento do mês pela diferença entre dois meses
seguidos. Os totais conferem com a consulta "Despesa por órgão" e com o painel inicial do portal.
