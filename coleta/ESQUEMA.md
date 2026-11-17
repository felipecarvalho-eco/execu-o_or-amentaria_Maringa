# Coleta: esquema dos dados

O script `coleta/coletar.py` baixa a execução orçamentária do **Município de Maringá-PR** (todas as
entidades publicadas no portal: Prefeitura, Câmara, Maringá Previdência e administração indireta)
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
python coleta/coletar.py --saida docs/dados --entidades 1     # só a Prefeitura
```

## Entidades

O portal publica estas entidades (lista lida a cada execução em `/api/entidades/lista`; uma entidade nova
entra sozinha, com o nome oficial):

| id | `entidade` (nome no painel) | Tipo |
|---|---|---|
| 1 | Prefeitura (administração direta) | Executivo |
| 3 | Maringá Previdência | Previdência (RPPS), autarquia |
| 4 | IAM - Instituto Ambiental | Autarquia |
| 6 | Câmara Municipal | Legislativo |
| 9 | AMR - Agência de Regulação | Autarquia |
| 10 | SBMG - Terminais Aéreos | Empresa pública (S/A) |
| 15 | IPPLAM - Instituto de Planejamento | Autarquia |

- A **SBMG** aparece na lista, mas o portal não publica execução orçamentária dela (empresa estatal com
  contabilidade própria). Fica em `meta.entidades` com zero linhas. Os números dela vêm dos **balancetes
  mensais em PDF** que o portal publica (Publicações > Balancetes Mensais), lidos por `coleta/sbmg.py`
  e gravados em `empresas` (ver abaixo).
- O **INPACTA** (Instituto de Projetos Avançados, serviço social autônomo criado pela LC 1.503/2025) não está
  neste portal. O [site de transparência dele](https://inpacta.org.br/transparencia) só publica PDFs
  (contratos de gestão, atas, prestações de contas), sem dados de execução para baixar. O que dá para medir
  é o que a Prefeitura empenhou e pagou a ele: aparece em `credores` (CNPJ 63.783.232/0001-83) e resumido
  por ano em `meta.fora_do_portal`.
- Somar todas as entidades pode contar duas vezes operações entre elas (ex.: a contribuição patronal que a
  Prefeitura paga à Maringá Previdência é despesa de uma e receita da outra).

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
| `execucao.js` / `execucao.json` | lido pelo painel | `window.DADOS_EXECUCAO = {meta, despesas, receitas, empresas}` (agregado) |
| `credores.js` / `credores.json` | lido pelo painel (aba de credores) | `window.DADOS_CREDORES = {meta, credores}` |
| `despesas.csv` | análise detalhada | menor nível: até unidade, ação, modalidade e fonte, com códigos |
| `receitas.csv` | análise detalhada | por natureza da receita (rubrica), com código |
| `credores.csv` | análise | mesmo conteúdo de `credores` |
| `empresas.csv` | análise | mesmo conteúdo de `empresas` (SBMG) |
| `licitacoes.js` / `.json` | aba Licitações | `window.DADOS_LICITACOES = {meta, licitacoes, mudancas}` |
| `diarias.js` / `.json` | aba Diárias e passagens | `window.DADOS_DIARIAS = {meta, diarias}` |
| `licitacoes.csv`, `diarias.csv` | análise | mesmo conteúdo das listas acima |
| `empenhos.csv` | só com `--com-empenhos` | um empenho por linha |

### `meta`

| Campo | Descrição |
|---|---|
| `municipio`, `fonte`, `api` | identificação e links |
| `entidades` | lista `{id, nome, nome_oficial, cnpj, tipo, linhas_despesa, linhas_receita, empenhos}` |
| `fora_do_portal` | entidades sem dados no portal (INPACTA): `{nome, tipo, cnpj, transparencia, observacao, repasses_por_ano: [{ano, empenhado, anulado, liquidado, pago, qtd_empenhos}]}` |
| `atualizado_em` | quando a coleta rodou (ISO 8601) |
| `portal_atualizado_despesas`, `portal_atualizado_receitas` | última atualização informada pelo portal |
| `anos` | exercícios coletados |
| `mes_corrente_parcial` | `"AAAA-MM"` do mês ainda em andamento |
| `observacoes` | texto com as regras acima |
| `avisos` | lista de diferenças pequenas encontradas na validação |
| `linhas_csv` | contagem de linhas dos CSVs |
| `empresas_fonte` | nota de rodapé sobre a origem dos números de `empresas` |
| `repasses_fonte` | nota sobre a origem e as regras de `repasses` |

### `despesas` (em `execucao.js`), agregada para o painel

`ano, mes, entidade, orgao, funcao, subfuncao, programa, categoria, grupo, modalidade_aplicacao, elemento,
fonte_recurso, dotacao_inicial, dotacao_atualizada, empenhado, liquidado, pago`

- `categoria`: Despesas Correntes / Despesas de Capital. `grupo`: ex. Pessoal e Encargos Sociais.
- `elemento`: elemento de despesa (ex. Vencimentos e Vantagens Fixas - Pessoal Civil).
- `fonte_recurso`: fonte/destinação (ex. Recursos Ordinários (Livres)).
- `modalidade_aplicacao`: código de 2 dígitos + nome (ex. "90 - Aplicações Diretas"). **91** = aplicação direta
  decorrente de operação entre órgãos, fundos e entidades do orçamento (**intraorçamentária**): pagamento a
  outra entidade do próprio município (ex. contribuição patronal à Maringá Previdência). Somar entidades sem
  tirar a 91 conta o mesmo dinheiro duas vezes.
- Unidade orçamentária e ação ficam só no CSV, para o arquivo do painel não ficar pesado.

### `despesas.csv` (detalhado)

Começa com `entidade` e `entidade_id`. Para cada nível há o nome e o código (`<nivel>_codigo`):
`orgao, unidade, funcao, subfuncao, programa, acao, categoria, grupo, modalidade, elemento, fonte_recurso`,
mais `programatica` (código completo, ex. `02.010.04.122.0002.2001.3.1.90.11.1000`), e os mesmos valores
acima. `elemento_codigo` é a natureza da despesa (ex. `3.1.90.11`).

### `receitas` (em `execucao.js`), agregada para o painel

`ano, mes, entidade, categoria, origem, fonte_recurso, intra_orcamentaria, previsto_inicial, previsto, arrecadado_bruto,
deducoes, arrecadado`

- `intra_orcamentaria` = `true` quando a natureza começa com 7 ou 8 (receitas correntes/de capital
  intraorçamentárias, vindas de outra entidade do município). É o outro lado da modalidade 91 da despesa.

- `categoria`: Receitas Correntes / Receitas de Capital. `origem`: ex. Impostos, Taxas e Contribuições de Melhoria.
- `arrecadado` = **líquido de deduções** (ex. retenção para o FUNDEB), comparável a `previsto`.
  `arrecadado_bruto` e `deducoes` (negativo) mostram a composição.
- `fonte_recurso` vem como "Não informado": a API detalhada da receita não informa a fonte por rubrica.

### `repasses` (em `execucao.js` e `repasses.csv`)

Repasses financeiros da Prefeitura às outras entidades (duodécimo da Câmara, repasses às autarquias,
aportes à Maringá Previdência). Não são receita orçamentária de quem recebe (são "transferências
financeiras"), por isso não aparecem em `receitas`. Módulo `coleta/repasses.py`; `--sem-repasses` desliga.

`ano, mes, entidade, entidade_id, origem, previsto, previsto_mes, recebido, devolvido, enviado_prefeitura`

- `previsto` = previsão anual da Prefeitura para a entidade (posição; igual em todos os meses).
- `previsto_mes` = cronograma do mês, só quando a soma do cronograma fecha com `previsto` (em 2025 fica vazio).
- `recebido` = movimento do mês **registrado por quem recebe**; `devolvido` = o que a entidade devolveu à
  Prefeitura no mês (ex.: sobra do duodécimo da Câmara em dezembro).
- `enviado_prefeitura` = o mesmo movimento visto pela Prefeitura, só para conferência. Em 2026 bate com
  `recebido`; em 2025 a Prefeitura e as autarquias só registraram de agosto em diante (a Câmara registrou o ano
  todo), o que vira aviso em `meta.avisos` e nota no painel.

### `receitas.csv` (detalhado)

Mais `entidade_id`, `especie`, `rubrica` (nome da natureza no menor nível) e `natureza_codigo` (ex. `11125001`).

### `credores`

`entidade, ano, mes, credor_documento, credor, qtd_empenhos, empenhado, anulado, liquidado, pago`

- Empenhos agrupados por credor e **mês de emissão** do empenho.
- `credor_documento` = CNPJ/CPF como aparece no portal (CPFs vêm mascarados pelo próprio portal).
- `empenhado`/`anulado` = valores dos empenhos emitidos no mês. `liquidado`/`pago` = **posição atual**
  desses empenhos (não o que foi pago naquele mês).

### `empresas` (SBMG, a partir dos balancetes mensais)

`entidade, ano, mes, receita_liquida, custo_servicos, despesas_operacionais, resultado_financeiro, ir_csll,
lucro_liquido, outros_resultados, caixa, ativo_total, emprestimos_curto_prazo, emprestimos_longo_prazo,
patrimonio_liquido, fonte`

- É **contabilidade empresarial** (receita, custo, lucro), não orçamentária: não há dotação nem empenho.
- `receita_liquida` a `outros_resultados` são valores **do mês** (Saldo Atual menos Saldo Anterior de cada
  conta de resultado). Custos, despesas e IR/CSLL vêm negativos. A soma dá `lucro_liquido`
  (`outros_resultados` = receitas e despesas eventuais).
- `caixa`, `ativo_total`, `emprestimos_*` e `patrimonio_liquido` são a **posição no fim do mês**.
- `fonte` = link direto do PDF do balancete.
- Conferência: a soma do lucro de 2025 (R$ 6.583.988,04) é igual ao lucro das demonstrações contábeis
  auditadas de 2025.
- O balancete sai com atraso (em nov/2026 o último publicado era o de mar/2026).
- Se algum PDF não puder ser lido, a coleta segue e registra o problema em `meta.avisos`.
- `--sem-sbmg` pula essa parte. Requer o pacote `pypdf`.

### `licitacoes` (de 2026 em diante; `--desde` muda o ano inicial)

`id, ano, mes, entidade, secretaria, numero, modalidade, contratacao_direta, natureza, objeto, valor_estimado,
valor_contratado, situacao, grupo, publicacao, abertura, processo, link`

- Inclui licitações e **contratações diretas** (Dispensa e Inexigibilidade; `contratacao_direta = true`).
- `ano` = exercício do processo; `mes` = mês da publicação.
- `valor_estimado` = valor máximo do processo; `valor_contratado` = total vencido (homologado).
- `situacao` = texto do portal (Homologada, Andamento, Prorrogada, Suspensa, Revogada...). `grupo` resume em
  Homologada / Em andamento / Sem sucesso (revogada, fracassada, deserta, anulada) / Suspensa.
- `secretaria`: o portal não tem esse campo; é lida do texto do objeto quando ele cita "Secretaria Municipal
  de ...". Senão, "Não informado".
- O portal não informa a data de homologação. Para acompanhar, use `mudancas`: quando uma licitação passa a
  "Homologada", a mudança fica registrada com a data da coleta.
- `link` abre o processo no portal.

### `mudancas`

`atualizacao, id, entidade, numero, modalidade, objeto, valor_estimado, valor_contratado, tipo, de, para`

- Cada coleta compara as licitações com as da coleta anterior (o `licitacoes.json` que já está na pasta).
  `tipo` = "Nova", "Mudou de situação" ou "Mudou valor contratado". Guarda as 500 mais recentes.
- Na primeira coleta não há com o que comparar, então a lista começa vazia.

### `diarias` (diárias e passagens, de 2026 em diante)

`id, ano, mes, entidade, orgao, tipo, forma, beneficiario, cargo, destino, motivo, objetivo, data_saida, data_retorno,
quantidade, valor, valor_empenhado, situacao, meio_transporte, documento, empresa`

- `tipo` = "Diária" ou "Passagem". `ano`/`mes` = data de saída da viagem.
- **Diárias:** `valor` = valor líquido (empenhado menos anulações; diária cancelada fica com 0).
  `valor_empenhado` = valor original. `orgao` vem do empenho que pagou a diária (a API de diárias não traz a
  secretaria). `quantidade` = número de diárias. `documento` = número do empenho.
- `forma` = "Diária", "Adiantamento ao servidor" ou "Empresa contratada".
- **Passagens, forma "Adiantamento ao servidor"** (`/api/passagens`): `valor` = adiantamento para o servidor
  comprar a passagem; `empresa` = companhia; o portal não informa o órgão (`orgao` = "Não informado");
  `quantidade` = 1.
- **Passagens, forma "Empresa contratada"** (empenhos do elemento 3.3.90.33, "Passagens e despesas com
  locomoção"): compras feitas por contrato com agência de viagem ou transportadora (ex.: a Câmara compra todas
  as passagens por agência; a Prefeitura freta ônibus para equipes e eventos). Não há nome do viajante
  (`beneficiario` = "Compra por empresa contratada"), destino nem datas; `ano`/`mes` = data do empenho;
  `valor` = empenhado menos anulado (empenhos por estimativa costumam ter anulações); `empresa` = credor;
  `orgao` vem do empenho; `documento` = número do empenho. A soma confere com o elemento 3.3.90.33 da despesa.

### `empenhos.csv`

`entidade, entidade_id, ano, empenho (nº/ano), data, mes, credor_documento, credor, fonte_recurso, historico, empenhado, anulado,
liquidado, pago, a_pagar`

## Endpoints usados

Todos exigem os cabeçalhos HTTP `entidade: <id>` e `exercicio: <ano>` (nos exemplos, `1` = Prefeitura).

| Dado | Endpoint |
|---|---|
| entidades | `/api/entidades/lista?fields=id,nome,cnpj,tipo` |
| exercícios | `/api/exercicios/entidade/1` |
| despesa | `/despesapornivel/detalhada?entidade=1&exercicio=A&execucao=true&dataInicial=A-01-01&dataFinal=A-MM-DD` |
| repasses | `/api/repasses?tipo=E&entidade=1&exercicio=A&mesInicial=01&mesFinal=12` (previsto anual) e `/api/repasses/mes?tipo=R\|E&entidade=X&exercicio=A&mesInicial=01&mesFinal=12&entidadeDestinoOrigem=Y` (por mês) |
| receita | `/api/receitas/detalhada?entidade=1&exercicio=A&dataInicial=...&dataFinal=...` |
| empenhos | `/empenhos/lista?search=id.exercicio=='A' and id.entidade=='1' and data>='...' and data<='...'&entidade=1&page=N&size=2000` |
| última atualização | `/api/ultimas-atualizacoes/last-update-despesas` e `-receitas` |
| publicações (SBMG) | `/api/publicacoes?entidade=10&exercicio=A` → grupo "Balancetes Mensais" |
| arquivo PDF | `/api/files/arquivo/<idArquivo>` (não precisa de cabeçalhos) |
| licitações | `/api/licitacoes?entidade=E&exercicio=A&page=0&size=2000` |
| diárias | `/api/diarias?entidade=E&exercicio=A&dataInicial=A-01-01&dataFinal=A-12-31&page=0&size=2000` |
| passagens | `/api/passagens?entidade=E&exercicio=A&page=0&size=2000` |
| passagens por empresa | `/empenhos/lista?...&programatica.elemento=339033` |
| órgão do empenho | `/empenhos/lista?...&programatica.orgao=<código do órgão>` |

A API devolve valores do intervalo de datas pedido (inclusive créditos adicionais). Por isso o script pede
sempre de 1º de janeiro até o fim de cada mês e calcula o movimento do mês pela diferença entre dois meses
seguidos. Os totais conferem com a consulta "Despesa por órgão" e com o painel inicial do portal.
