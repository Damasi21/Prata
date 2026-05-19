from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import Budget, Categoria, ContaBancaria, ContaPagarReceber, Lancamento, RateioLancamento, RecorrenciaConta
from .views import _datas_recorrencia


def criar_categoria_filha(nome, tipo=Categoria.DESPESA, pai_nome='Grupo'):
    pai = Categoria.objects.create(nome=pai_nome, tipo=tipo)
    return Categoria.objects.create(nome=nome, tipo=tipo, pai=pai)


class CategoriaTests(TestCase):
    def test_categoria_filha_exibe_caminho_com_pai(self):
        categoria = criar_categoria_filha('Combustivel', pai_nome='Veiculos')

        self.assertEqual(str(categoria), 'Veiculos / Combustivel')
        self.assertTrue(categoria.eh_filha)
        self.assertFalse(categoria.eh_pai)

    def test_contas_pagar_receber_lista_apenas_categorias_filhas(self):
        pai = Categoria.objects.create(nome='Veiculos', tipo=Categoria.DESPESA)
        filha = Categoria.objects.create(nome='Combustivel', tipo=Categoria.DESPESA, pai=pai)

        resposta = self.client.get(reverse('contas_pagar_receber'))

        opcoes = resposta.context['form'].fields['categoria'].queryset
        self.assertNotIn(pai, opcoes)
        self.assertIn(filha, opcoes)

    def test_categorias_atualiza_categoria_existente(self):
        pai = Categoria.objects.create(nome='Veiculos', tipo=Categoria.DESPESA)
        categoria = Categoria.objects.create(nome='Combustivel', tipo=Categoria.DESPESA, pai=pai)

        resposta = self.client.post(
            reverse('categorias'),
            {
                'categoria_id': categoria.id,
                'nome': 'Posto de combustivel',
                'tipo': Categoria.DESPESA,
                'pai': pai.id,
                'ativa': 'on',
            },
        )

        self.assertRedirects(resposta, reverse('categorias'))
        categoria.refresh_from_db()
        self.assertEqual(categoria.nome, 'Posto de combustivel')
        self.assertEqual(categoria.pai, pai)

    def test_categorias_exclui_categoria_sem_lancamentos(self):
        categoria = criar_categoria_filha('Combustivel', pai_nome='Veiculos')

        resposta = self.client.post(
            reverse('categorias'),
            {
                'categoria_id': categoria.id,
                'acao': 'excluir',
            },
        )

        self.assertRedirects(resposta, reverse('categorias'))
        self.assertFalse(Categoria.objects.filter(id=categoria.id).exists())

    def test_categorias_nao_exclui_categoria_com_lancamento(self):
        categoria = criar_categoria_filha('Combustivel', pai_nome='Veiculos')
        conta = ContaBancaria.objects.create(nome='Principal', banco='Banco')
        lancamento = Lancamento.objects.create(
            conta=conta,
            data=date(2026, 5, 1),
            descricao='Abastecimento',
            valor=Decimal('-100.00'),
            tipo=Lancamento.DEBITO,
        )
        RateioLancamento.objects.create(lancamento=lancamento, categoria=categoria, percentual=Decimal('100.00'))

        resposta = self.client.post(
            reverse('categorias'),
            {
                'categoria_id': categoria.id,
                'acao': 'excluir',
            },
        )

        self.assertRedirects(resposta, reverse('categorias'))
        self.assertTrue(Categoria.objects.filter(id=categoria.id).exists())


class RecorrenciaContaTests(TestCase):
    def test_datas_recorrencia_mensal_respeitam_ultimo_dia_do_mes(self):
        datas = list(_datas_recorrencia(date(2026, 1, 31), RecorrenciaConta.MENSAL, 3))

        self.assertEqual(datas, [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])

    def test_contas_pagar_receber_cria_recorrencia_semanal(self):
        categoria = criar_categoria_filha('Telefone', pai_nome='Moradia')

        resposta = self.client.post(
            reverse('contas_pagar_receber'),
            {
                'categoria': categoria.id,
                'descricao': 'Conta de telefone',
                'vencimento': '2026-06-05',
                'valor': '150.00',
                'status': ContaPagarReceber.ABERTO,
                'criar_recorrencia': 'on',
                'frequencia_recorrencia': RecorrenciaConta.SEMANAL,
                'quantidade_recorrencia': '4',
            },
        )

        self.assertRedirects(resposta, reverse('contas_pagar_receber'))
        recorrencia = RecorrenciaConta.objects.get()
        contas = list(ContaPagarReceber.objects.order_by('vencimento'))
        self.assertEqual(recorrencia.quantidade, 4)
        self.assertEqual([conta.vencimento for conta in contas], [
            date(2026, 6, 5),
            date(2026, 6, 12),
            date(2026, 6, 19),
            date(2026, 6, 26),
        ])
        self.assertTrue(all(conta.recorrencia == recorrencia for conta in contas))
        self.assertTrue(all(conta.valor == Decimal('150.00') for conta in contas))

    def test_contas_pagar_receber_atualiza_conta_existente(self):
        categoria = criar_categoria_filha('Telefone', pai_nome='Moradia')
        nova_categoria = criar_categoria_filha('Internet', pai_nome='Servicos')
        conta = ContaPagarReceber.objects.create(
            categoria=categoria,
            descricao='Conta de telefone',
            vencimento=date(2026, 6, 5),
            valor=Decimal('150.00'),
        )

        resposta = self.client.post(
            reverse('contas_pagar_receber'),
            {
                'conta_id': conta.id,
                'categoria': nova_categoria.id,
                'descricao': 'Conta de telefone ajustada',
                'vencimento': '2026-06-10',
                'valor': '175.50',
                'status': ContaPagarReceber.PAGO,
            },
        )

        self.assertRedirects(resposta, reverse('contas_pagar_receber'))
        conta.refresh_from_db()
        self.assertEqual(conta.categoria, nova_categoria)
        self.assertEqual(conta.descricao, 'Conta de telefone ajustada')
        self.assertEqual(conta.vencimento, date(2026, 6, 10))
        self.assertEqual(conta.valor, Decimal('175.50'))
        self.assertEqual(conta.status, ContaPagarReceber.PAGO)

    def test_contas_pagar_receber_exclui_conta_existente(self):
        categoria = criar_categoria_filha('Telefone', pai_nome='Moradia')
        conta = ContaPagarReceber.objects.create(
            categoria=categoria,
            descricao='Conta de telefone',
            vencimento=date(2026, 6, 5),
            valor=Decimal('150.00'),
        )

        resposta = self.client.post(
            reverse('contas_pagar_receber'),
            {
                'conta_id': conta.id,
                'acao': 'excluir',
            },
        )

        self.assertRedirects(resposta, reverse('contas_pagar_receber'))
        self.assertFalse(ContaPagarReceber.objects.filter(id=conta.id).exists())

    def test_contas_pagar_receber_exclui_recorrencia_inteira(self):
        categoria = criar_categoria_filha('Telefone', pai_nome='Moradia')
        recorrencia = RecorrenciaConta.objects.create(
            descricao='Conta de telefone',
            frequencia=RecorrenciaConta.MENSAL,
            quantidade=2,
            data_inicio=date(2026, 6, 5),
        )
        primeira_conta = ContaPagarReceber.objects.create(
            categoria=categoria,
            descricao='Conta de telefone',
            vencimento=date(2026, 6, 5),
            valor=Decimal('150.00'),
            recorrencia=recorrencia,
        )
        ContaPagarReceber.objects.create(
            categoria=categoria,
            descricao='Conta de telefone',
            vencimento=date(2026, 7, 5),
            valor=Decimal('150.00'),
            recorrencia=recorrencia,
        )

        resposta = self.client.post(
            reverse('contas_pagar_receber'),
            {
                'conta_id': primeira_conta.id,
                'acao': 'excluir',
                'excluir_recorrencia': '1',
            },
        )

        self.assertRedirects(resposta, reverse('contas_pagar_receber'))
        self.assertFalse(RecorrenciaConta.objects.filter(id=recorrencia.id).exists())
        self.assertFalse(ContaPagarReceber.objects.filter(descricao='Conta de telefone').exists())


class BudgetTests(TestCase):
    def test_budgets_separa_despesas_receitas_e_calcula_totais(self):
        despesa = criar_categoria_filha('Telefone', pai_nome='Moradia')
        receita = criar_categoria_filha('Salario', tipo=Categoria.RECEITA, pai_nome='Trabalho')
        Budget.objects.create(categoria=despesa, mes=date(2026, 5, 1), valor=Decimal('150.00'))
        Budget.objects.create(categoria=receita, mes=date(2026, 5, 1), valor=Decimal('1000.00'))

        resposta = self.client.get(reverse('budgets'), {'mes': '2026-05-01'})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['linhas_despesas'][0]['categoria'], despesa)
        self.assertEqual(resposta.context['linhas_receitas'][0]['categoria'], receita)
        self.assertEqual(resposta.context['total_despesas'], Decimal('150.00'))
        self.assertEqual(resposta.context['total_receitas'], Decimal('1000.00'))
        self.assertEqual(resposta.context['diferenca_budget'], Decimal('850.00'))

    def test_budgets_copia_valores_para_meses_subsequentes(self):
        despesa = criar_categoria_filha('Telefone', pai_nome='Moradia')
        receita = criar_categoria_filha('Salario', tipo=Categoria.RECEITA, pai_nome='Trabalho')

        resposta = self.client.post(
            reverse('budgets'),
            {
                'mes_numero': '5',
                'ano': '2026',
                'acao': 'copiar_subsequentes',
                f'budget-{despesa.id}': '150.00',
                f'budget-{receita.id}': '1000.00',
            },
        )

        self.assertRedirects(resposta, f'{reverse("budgets")}?mes_numero=5&ano=2026')
        meses = [date(2026, mes, 1) for mes in range(5, 13)]
        self.assertEqual(Budget.objects.filter(categoria=despesa, mes__in=meses).count(), 8)
        self.assertEqual(Budget.objects.filter(categoria=receita, mes__in=meses).count(), 8)
        self.assertEqual(Budget.objects.get(categoria=despesa, mes=date(2026, 12, 1)).valor, Decimal('150.00'))
        self.assertEqual(Budget.objects.get(categoria=receita, mes=date(2026, 12, 1)).valor, Decimal('1000.00'))

    def test_budgets_mantem_valores_independentes_por_mes(self):
        despesa = criar_categoria_filha('Telefone', pai_nome='Moradia')
        Budget.objects.create(categoria=despesa, mes=date(2026, 5, 1), valor=Decimal('150.00'))
        Budget.objects.create(categoria=despesa, mes=date(2026, 6, 1), valor=Decimal('300.00'))

        resposta = self.client.post(
            reverse('budgets'),
            {
                'mes_numero': '5',
                'ano': '2026',
                f'budget-{despesa.id}': '175.00',
            },
        )

        self.assertRedirects(resposta, f'{reverse("budgets")}?mes_numero=5&ano=2026')
        self.assertEqual(Budget.objects.get(categoria=despesa, mes=date(2026, 5, 1)).valor, Decimal('175.00'))
        self.assertEqual(Budget.objects.get(categoria=despesa, mes=date(2026, 6, 1)).valor, Decimal('300.00'))

        resposta_junho = self.client.get(reverse('budgets'), {'mes_numero': '6', 'ano': '2026'})
        self.assertEqual(resposta_junho.context['linhas_despesas'][0]['valor'], Decimal('300.00'))
