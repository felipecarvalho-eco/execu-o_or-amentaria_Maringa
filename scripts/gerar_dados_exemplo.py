import os
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import json, random
random.seed(42)
# (orgao, [(funcao, subfuncao, programa, peso)])
ORG = [
 ("Secretaria Municipal de Saúde", [("Saúde","Atenção Básica","Saúde mais Perto",0.10),("Saúde","Assistência Hospitalar e Ambulatorial","Rede Hospitalar e Especializada",0.14),("Saúde","Vigilância Epidemiológica","Vigilância em Saúde",0.02)]),
 ("Secretaria Municipal de Educação", [("Educação","Ensino Fundamental","Educação de Qualidade",0.15),("Educação","Educação Infantil","Infância em Primeiro Lugar",0.09)]),
 ("Maringá Previdência", [("Previdência Social","Previdência do Regime Estatutário","Gestão Previdenciária",0.09)]),
 ("Secretaria Municipal de Obras Públicas", [("Urbanismo","Infraestrutura Urbana","Cidade Bem Cuidada",0.07),("Transporte","Transporte Rodoviário","Pavimentação e Vias",0.03)]),
 ("Secretaria Municipal de Assistência Social", [("Assistência Social","Assistência Comunitária","Proteção Social Básica",0.03),("Assistência Social","Assistência à Criança e ao Adolescente","Proteção Social Especial",0.015)]),
 ("Secretaria Municipal de Fazenda", [("Administração","Administração Financeira","Gestão Fiscal",0.02),("Encargos Especiais","Serviço da Dívida Interna","Encargos da Dívida",0.025)]),
 ("Secretaria Municipal de Administração", [("Administração","Administração Geral","Gestão Administrativa",0.04)]),
 ("Secretaria Municipal de Mobilidade Urbana", [("Transporte","Transportes Coletivos Urbanos","Mobilidade para Todos",0.025)]),
 ("Secretaria Municipal de Meio Ambiente", [("Gestão Ambiental","Preservação e Conservação Ambiental","Maringá Verde",0.01),("Saneamento","Saneamento Básico Urbano","Limpeza Urbana e Resíduos",0.04)]),
 ("Secretaria Municipal de Segurança Pública", [("Segurança Pública","Policiamento","Cidade Segura",0.015)]),
 ("Secretaria Municipal de Cultura", [("Cultura","Difusão Cultural","Cultura Viva",0.008)]),
 ("Secretaria Municipal de Esportes e Lazer", [("Desporto e Lazer","Desporto Comunitário","Esporte para Todos",0.007)]),
 ("Câmara Municipal de Maringá", [("Legislativa","Ação Legislativa","Processo Legislativo",0.025)]),
 ("Gabinete do Prefeito", [("Administração","Administração Geral","Apoio ao Gabinete",0.006)]),
]
GRUPOS = {
 "Pessoal e Encargos Sociais": ("Despesas Correntes", ["Vencimentos e Vantagens Fixas - Pessoal Civil","Obrigações Patronais"]),
 "Outras Despesas Correntes": ("Despesas Correntes", ["Material de Consumo","Outros Serviços de Terceiros - Pessoa Jurídica"]),
 "Investimentos": ("Despesas de Capital", ["Obras e Instalações","Equipamentos e Material Permanente"]),
 "Juros e Encargos da Dívida": ("Despesas Correntes", ["Juros sobre a Dívida por Contrato"]),
 "Amortização da Dívida": ("Despesas de Capital", ["Principal da Dívida Contratual Resgatado"]),
}
def mix(funcao):
    if funcao=="Encargos Especiais": return {"Juros e Encargos da Dívida":0.45,"Amortização da Dívida":0.55}
    if funcao=="Previdência Social": return {"Pessoal e Encargos Sociais":0.95,"Outras Despesas Correntes":0.05}
    if funcao in ("Urbanismo","Transporte"): return {"Pessoal e Encargos Sociais":0.2,"Outras Despesas Correntes":0.35,"Investimentos":0.45}
    if funcao in ("Saúde",): return {"Pessoal e Encargos Sociais":0.45,"Outras Despesas Correntes":0.48,"Investimentos":0.07}
    if funcao=="Educação": return {"Pessoal e Encargos Sociais":0.62,"Outras Despesas Correntes":0.30,"Investimentos":0.08}
    return {"Pessoal e Encargos Sociais":0.5,"Outras Despesas Correntes":0.42,"Investimentos":0.08}
def fontes(funcao):
    if funcao=="Saúde": return {"Recursos Livres":0.55,"Saúde - Transferências SUS":0.45}
    if funcao=="Educação": return {"Educação - MDE (25%)":0.45,"FUNDEB":0.5,"Convênios":0.05}
    if funcao=="Previdência Social": return {"Recursos do RPPS":1.0}
    if funcao in ("Urbanismo","Transporte"): return {"Recursos Livres":0.6,"Operações de Crédito":0.25,"Convênios":0.15}
    if funcao=="Assistência Social": return {"Recursos Livres":0.7,"Convênios":0.3}
    return {"Recursos Livres":1.0}
TOTAL = {2025: 3_150_000_000, 2026: 3_420_000_000}
ULT = {2025:12, 2026:10}
desp=[]
for ano,tot in TOTAL.items():
    for org, items in ORG:
        for funcao,sub,prog,peso in items:
            for grupo,gp in mix(funcao).items():
                cat, elems = GRUPOS[grupo]
                for fonte,fp in fontes(funcao).items():
                    for i,el in enumerate(elems):
                        ep = (0.75 if i==0 else 0.25) if len(elems)>1 else 1
                        base = tot*peso*gp*fp*ep*random.uniform(0.85,1.15)
                        dot = base; 
                        execrate = {"Pessoal e Encargos Sociais":0.98,"Outras Despesas Correntes":0.9,"Investimentos":0.55}.get(grupo,0.97)*random.uniform(0.9,1.03)
                        emp_acc=liq_acc=pag_acc=0.0
                        for m in range(1,ULT[ano]+1):
                            if m in (4,7,10): dot *= random.uniform(1.0,1.04)
                            alvo_emp = dot*execrate
                            if grupo=="Pessoal e Encargos Sociais":
                                frac_e = m/12; frac_l = m/12; frac_p=(m-0.15)/12
                            elif grupo=="Investimentos":
                                frac_e = min(1,(m/12)**0.8); frac_l=(m/12)**1.6; frac_p=(m/12)**1.8
                            else:
                                frac_e = min(1,0.25+0.75*m/12) ; frac_l=(m/12)**1.05; frac_p=(m/12)**1.1*0.97
                            e=alvo_emp*frac_e; l=min(e,alvo_emp*frac_l); p=min(l,alvo_emp*max(0,frac_p))
                            if m==12: l=e*0.96 if grupo=="Investimentos" else e*0.995; p=l*0.93 if grupo=="Investimentos" else l*0.985
                            row=dict(ano=ano,mes=m,orgao=org,funcao=funcao,subfuncao=sub,programa=prog,categoria=cat,grupo=grupo,elemento=el,fonte_recurso=fonte,
                                     dotacao_inicial=round(base,2),dotacao_atualizada=round(dot,2),
                                     empenhado=round(max(0,e-emp_acc),2),liquidado=round(max(0,l-liq_acc),2),pago=round(max(0,p-pag_acc),2))
                            emp_acc=max(emp_acc,e); liq_acc=max(liq_acc,l); pag_acc=max(pag_acc,p)
                            desp.append(row)
REC = [("Receitas Correntes","Impostos, Taxas e Contribuições de Melhoria",0.30,"Recursos Livres"),
       ("Receitas Correntes","Transferências Correntes",0.47,"Recursos Livres"),
       ("Receitas Correntes","Contribuições",0.08,"Recursos do RPPS"),
       ("Receitas Correntes","Receita Patrimonial",0.05,"Recursos Livres"),
       ("Receitas Correntes","Outras Receitas Correntes",0.03,"Recursos Livres"),
       ("Receitas de Capital","Operações de Crédito",0.04,"Operações de Crédito"),
       ("Receitas de Capital","Transferências de Capital",0.03,"Convênios")]
rec=[]
for ano,tot in TOTAL.items():
    for cat,orig,p,fonte in REC:
        prev=tot*p
        for m in range(1,ULT[ano]+1):
            saz = 1.9 if (orig.startswith("Impostos") and m in (2,3)) else 1.0
            arr = prev/12*saz*random.uniform(0.85,1.1)*(0.6 if cat=="Receitas de Capital" else 1)
            rec.append(dict(ano=ano,mes=m,categoria=cat,origem=orig,fonte_recurso=fonte,previsto=round(prev,2),arrecadado=round(arr,2)))
out=dict(meta=dict(municipio="Maringá - PR",fonte="https://www.maringa.pr.gov.br/portaltransparencia (exemplo)",atualizado_em="2026-11-15T12:00:00-03:00",
        exemplo=True,observacoes="DADOS DE EXEMPLO gerados para testar o painel. Não representam a execução real do município."),despesas=desp,receitas=rec)
s=json.dumps(out,ensure_ascii=False,separators=(",",":"))
open(os.path.join(RAIZ,"docs/dados/execucao.js"),"w").write("window.DADOS_EXECUCAO = "+s+";\n")
open(os.path.join(RAIZ,"docs/dados/execucao.json"),"w").write(s)
print(len(desp),len(rec),len(s))
