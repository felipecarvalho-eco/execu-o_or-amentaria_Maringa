# Execução Orçamentária de Maringá

Acompanhamento da execução orçamentária do Município de Maringá (PR), com dados do Portal da Transparência.

## Painel

O painel é uma página HTML estática em `docs/` (publicada pelo GitHub Pages a partir da pasta `docs` da branch `main`).

- `docs/index.html`: o painel (filtros, indicadores, gráficos e tabela).
- `docs/dados/execucao.js`: os dados que o painel lê, no formato `window.DADOS_EXECUCAO = {...};`.
- `docs/dados/execucao.json`: o mesmo conteúdo em JSON.

Para ver localmente, abra `docs/index.html` no navegador (funciona sem servidor).

### Formato dos dados

```
meta:     {municipio, fonte, atualizado_em (ISO), exemplo (bool), observacoes}
despesas: {ano, mes, orgao, funcao, subfuncao, programa, categoria, grupo, elemento,
           fonte_recurso, dotacao_inicial, dotacao_atualizada, empenhado, liquidado, pago}
receitas: {ano, mes, categoria, origem, fonte_recurso, previsto, arrecadado}   (opcional)
```

- `empenhado`, `liquidado`, `pago` e `arrecadado` são o movimento **do mês**.
- `dotacao_inicial`, `dotacao_atualizada` e `previsto` são a posição acumulada no fim do mês; o painel usa a do último mês de cada combinação.
- `meta.exemplo = true` mostra um aviso de dados de exemplo.

## Coleta de dados

Os dados em `docs/dados/` são baixados do Portal da Transparência por `coleta/coletar.py`
(esquema completo e endpoints em [`coleta/ESQUEMA.md`](coleta/ESQUEMA.md)):

```bash
pip install -r coleta/requirements.txt
python coleta/coletar.py --saida docs/dados
```

Além de `execucao.js/json`, a coleta grava `credores.js/json` (empenhos por credor e mês) e CSVs
detalhados (`despesas.csv`, `receitas.csv`, `credores.csv`) para quem quiser analisar em planilha.
