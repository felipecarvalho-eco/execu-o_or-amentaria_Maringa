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
despesas: {ano, mes, entidade, orgao, funcao, subfuncao, programa, categoria, grupo, elemento,
           fonte_recurso, dotacao_inicial, dotacao_atualizada, empenhado, liquidado, pago}
receitas: {ano, mes, entidade, categoria, origem, fonte_recurso, previsto, arrecadado}   (opcional)
```

- `empenhado`, `liquidado`, `pago` e `arrecadado` são o movimento **do mês**.
- `dotacao_inicial`, `dotacao_atualizada` e `previsto` são a posição acumulada no fim do mês; o painel usa a do último mês de cada combinação.
- `entidade` (opcional): Prefeitura, autarquia, empresa pública etc. Com mais de uma entidade, o painel mostra o filtro "Entidade".
- `docs/dados/credores.js` (opcional, carregado só ao abrir a aba Credores): `window.DADOS_CREDORES = {meta, credores:[{ano, mes, entidade, credor_documento, credor, qtd_empenhos, empenhado, anulado, liquidado, pago}]}`.
- `meta.fora_do_portal` (opcional): entidades sem execução no portal (ex.: INPACTA), com `nome, tipo, cnpj, transparencia, observacao, repasses_por_ano:[{ano, empenhado, anulado, liquidado, pago, qtd_empenhos}]`; aparecem no cartão "Entidades fora do Portal da Transparência".
- `empresas` (opcional): balancetes mensais de empresas do Município (ex.: SBMG), `{entidade, ano, mes, receita_liquida, custo_servicos, despesas_operacionais, resultado_financeiro, ir_csll, outros_resultados, lucro_liquido, caixa, ativo_total, emprestimos_curto_prazo, emprestimos_longo_prazo, patrimonio_liquido, fonte}`; os seis primeiros são do mês e os demais, posição no fim do mês. A empresa aparece no filtro Entidade com uma visão própria; `meta.empresas_fonte` é a nota de rodapé.
- `meta.exemplo = true` mostra um aviso de dados de exemplo.

## Coleta de dados

Os dados em `docs/dados/` são baixados do Portal da Transparência (Prefeitura, Câmara, Maringá Previdência e autarquias) por `coleta/coletar.py`
(esquema completo e endpoints em [`coleta/ESQUEMA.md`](coleta/ESQUEMA.md)):

```bash
pip install -r coleta/requirements.txt
python coleta/coletar.py --saida docs/dados
```

Além de `execucao.js/json`, a coleta grava `credores.js/json` (empenhos por credor e mês) e CSVs
detalhados (`despesas.csv`, `receitas.csv`, `credores.csv`) para quem quiser analisar em planilha.
