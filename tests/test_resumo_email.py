"""Testes do resumo por e-mail. Rodar com: python -m unittest discover -s tests"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import resumo_email as r  # noqa: E402

BASE = dict(ano=2026, orgao="SECRETARIA MUNICIPAL DE SAUDE", funcao="SAUDE", subfuncao="ATENCAO BASICA",
            programa="P", categoria="DESPESAS CORRENTES", grupo="OUTRAS DESPESAS CORRENTES",
            elemento="SERVICOS", fonte_recurso="1000", liquidado=0, pago=0)


def desp(**kw):
    return {**BASE, **kw}


class TotaisDespesa(unittest.TestCase):
    def test_modalidades_diferentes_nao_se_sobrescrevem(self):
        # mesma despesa em duas modalidades: a 50 só aparece em janeiro, a 90 também em março
        dados = {"despesas": [
            desp(mes=1, modalidade_aplicacao="90 - APLICACOES DIRETAS", dotacao_atualizada=1000, empenhado=300),
            desp(mes=1, modalidade_aplicacao="50 - TRANSFERENCIAS A INSTITUICOES PRIVADAS", dotacao_atualizada=500, empenhado=200),
            desp(mes=3, modalidade_aplicacao="90 - APLICACOES DIRETAS", dotacao_atualizada=1200, empenhado=100),
        ]}
        t = r.totais(dados)
        self.assertAlmostEqual(t["dotacao"], 1700)  # 1200 (90, posição de março) + 500 (50)
        self.assertAlmostEqual(t["empenhado"], 600)
        self.assertAlmostEqual(t["orgaos"][0][1][0], 1700)

    def test_entidades_diferentes_nao_se_sobrescrevem(self):
        dados = {"despesas": [
            desp(mes=1, entidade="Prefeitura", dotacao_atualizada=1000, empenhado=0),
            desp(mes=2, entidade="IAM", dotacao_atualizada=400, empenhado=0),
        ]}
        self.assertAlmostEqual(r.totais(dados)["dotacao"], 1400)

    def test_base_antiga_sem_modalidade(self):
        dados = {"despesas": [desp(mes=1, dotacao_atualizada=1000, empenhado=10),
                              desp(mes=2, dotacao_atualizada=1100, empenhado=20)]}
        t = r.totais(dados)
        self.assertAlmostEqual(t["dotacao"], 1100)  # última posição do mesmo grupo
        self.assertAlmostEqual(t["empenhado"], 30)


class TotaisReceita(unittest.TestCase):
    def test_intra_e_entidade_entram_na_chave(self):
        rec = dict(ano=2026, categoria="RECEITAS CORRENTES", origem="CONTRIBUICOES", fonte_recurso="1000")
        dados = {"despesas": [desp(mes=1, dotacao_atualizada=1, empenhado=0)], "receitas": [
            {**rec, "mes": 1, "entidade": "Previdência", "intra_orcamentaria": False, "previsto": 600, "arrecadado": 50},
            {**rec, "mes": 2, "entidade": "Previdência", "intra_orcamentaria": True, "previsto": 300, "arrecadado": 30},
            {**rec, "mes": 1, "entidade": "Prefeitura", "intra_orcamentaria": False, "previsto": 100, "arrecadado": 10},
        ]}
        t = r.totais(dados)
        self.assertAlmostEqual(t["previsto"], 1000)
        self.assertAlmostEqual(t["arrecadado"], 90)


if __name__ == "__main__":
    unittest.main()
