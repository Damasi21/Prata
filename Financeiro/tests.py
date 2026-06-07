from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import (
    Budget,
    Categoria,
    ContaBancaria,
    ContaPagarReceber,
    Importacao,
    Lancamento,
    RateioLancamento,
    RecorrenciaConta,
)
from .views import _datas_recorrencia


def criar_categoria_filha(nome, tipo=Categoria.DESPESA, pai_nome='Grupo'):
    pai = Categoria.objects.create(nome=pai_nome, tipo=tipo)
    return Categoria.objects.create(nome=nome, tipo=tipo, pai=pai)


class LoginTests(TestCase):
    def test_index_redireciona_anonimo_para_login(self):
        resposta = self.client.get(reverse('index'))

        self.assertRedirects(resposta, f'{reverse("tela_login")}?next={reverse("index")}')

    def test_login_com_credenciais_validas_abre_index(self):
        User.objects.create_user(username='usuario', password='senha-segura')

        resposta = self.client.post(
            reverse('tela_login'),
            {
                'username': 'usuario',
                'password': 'senha-segura',
            },
        )

        self.assertRedirects(resposta, reverse('index'))

    def test_cadastro_primeiro_usuario_cria_admin_e_autentica(self):
        resposta = self.client.post(
            reverse('cadastro_usuario'),
            {
                'username': 'admin',
                'first_name': 'Admin',
                'last_name': 'Prata',
                'email': 'admin@example.com',
                'password1': 'SenhaForte123!',
                'password2': 'SenhaForte123!',
            },
        )

        self.assertRedirects(resposta, reverse('index'))
        usuario = User.objects.get(username='admin')
        self.assertTrue(usuario.is_staff)
        self.assertTrue(usuario.is_superuser)

    def test_cadastro_publico_cria_usuario_pendente_quando_ja_existe_usuario(self):
        User.objects.create_user(username='usuario', password='senha-segura')

        resposta = self.client.post(
            reverse('cadastro_usuario'),
            {
                'username': 'novo',
                'first_name': 'Novo',
                'last_name': 'Usuario',
                'email': 'novo@example.com',
                'password1': 'SenhaForte123!',
                'password2': 'SenhaForte123!',
            },
        )

        self.assertRedirects(resposta, reverse('tela_login'))
        usuario = User.objects.get(username='novo')
        self.assertFalse(usuario.is_active)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)

    def test_usuario_pendente_nao_consegue_logar(self):
        User.objects.create_user(username='pendente', password='senha-segura', is_active=False)

        resposta = self.client.post(
            reverse('tela_login'),
            {
                'username': 'pendente',
                'password': 'senha-segura',
            },
        )

        self.assertRedirects(resposta, reverse('tela_login'))

    def test_admin_aprova_usuario_pendente(self):
        admin = User.objects.create_user(username='admin', password='senha-segura', is_staff=True)
        pendente = User.objects.create_user(username='pendente', password='senha-segura', is_active=False)
        self.client.force_login(admin)

        resposta = self.client.post(
            reverse('usuarios'),
            {
                'usuario_id': pendente.id,
                'acao': 'aprovar',
            },
        )

        self.assertRedirects(resposta, reverse('usuarios'))
        pendente.refresh_from_db()
        self.assertTrue(pendente.is_active)

    def test_admin_reprova_usuario_pendente(self):
        admin = User.objects.create_user(username='admin', password='senha-segura', is_staff=True)
        pendente = User.objects.create_user(username='pendente', password='senha-segura', is_active=False)
        self.client.force_login(admin)

        resposta = self.client.post(
            reverse('usuarios'),
            {
                'usuario_id': pendente.id,
                'acao': 'reprovar',
            },
        )

        self.assertRedirects(resposta, reverse('usuarios'))
        self.assertFalse(User.objects.filter(username='pendente').exists())


class CategoriaTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='usuario', password='senha-segura')
        self.client.login(username='usuario', password='senha-segura')

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


class ContaBancariaTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='usuario', password='senha-segura')
        self.client.login(username='usuario', password='senha-segura')

    def test_contas_bancarias_atualiza_conta_existente(self):
        conta = ContaBancaria.objects.create(nome='Principal', banco='Banco')

        resposta = self.client.post(
            reverse('contas_bancarias'),
            {
                'conta_id': conta.id,
                'nome': 'Reserva',
                'banco': 'Novo banco',
                'agencia': '1234',
                'numero': '5678',
                'saldo_inicial': '-250.75',
                'data_saldo_inicial': '2026-05-10',
                'ativa': 'on',
            },
        )

        self.assertRedirects(resposta, reverse('contas_bancarias'))
        conta.refresh_from_db()
        self.assertEqual(conta.nome, 'Reserva')
        self.assertEqual(conta.banco, 'Novo banco')
        self.assertEqual(conta.saldo_inicial, Decimal('-250.75'))
        self.assertEqual(conta.data_saldo_inicial, date(2026, 5, 10))

    def test_contas_bancarias_exclui_conta_sem_lancamentos(self):
        conta = ContaBancaria.objects.create(nome='Principal', banco='Banco')

        resposta = self.client.post(
            reverse('contas_bancarias'),
            {
                'conta_id': conta.id,
                'acao': 'excluir',
            },
        )

        self.assertRedirects(resposta, reverse('contas_bancarias'))
        self.assertFalse(ContaBancaria.objects.filter(id=conta.id).exists())

    def test_contas_bancarias_nao_exclui_conta_com_lancamento(self):
        conta = ContaBancaria.objects.create(nome='Principal', banco='Banco')
        Lancamento.objects.create(
            conta=conta,
            data=date(2026, 5, 1),
            descricao='Compra',
            valor=Decimal('-50.00'),
            tipo=Lancamento.DEBITO,
        )

        resposta = self.client.post(
            reverse('contas_bancarias'),
            {
                'conta_id': conta.id,
                'acao': 'excluir',
            },
        )

        self.assertRedirects(resposta, reverse('contas_bancarias'))
        self.assertTrue(ContaBancaria.objects.filter(id=conta.id).exists())

    def test_extrato_com_conta_filtrada_calcula_saldo_acumulado(self):
        conta = ContaBancaria.objects.create(
            nome='Principal',
            banco='Banco',
            saldo_inicial=Decimal('1000.00'),
            data_saldo_inicial=date(2026, 5, 1),
        )
        Lancamento.objects.create(
            conta=conta,
            data=date(2026, 5, 2),
            descricao='Compra',
            valor=Decimal('-150.00'),
            tipo=Lancamento.DEBITO,
        )
        Lancamento.objects.create(
            conta=conta,
            data=date(2026, 5, 3),
            descricao='Recebimento',
            valor=Decimal('300.00'),
            tipo=Lancamento.CREDITO,
        )

        resposta = self.client.get(reverse('importar_ofx'), {'conta': conta.id})

        linhas = resposta.context['extrato_linhas']
        self.assertEqual(linhas[0].tipo, 'saldo_inicial')
        self.assertEqual(linhas[0].saldo, Decimal('1000.00'))
        self.assertEqual(linhas[1].saldo, Decimal('850.00'))
        self.assertEqual(linhas[2].saldo, Decimal('1150.00'))
        self.assertEqual(resposta.context['saldo_final'], Decimal('1150.00'))
        self.assertContains(resposta, 'Saldo inicial')


class CategorizacaoImportacaoTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='usuario', password='senha-segura')
        self.client.login(username='usuario', password='senha-segura')
        self.conta = ContaBancaria.objects.create(nome='Principal', banco='Banco')
        self.despesa = criar_categoria_filha('Alimentacao', tipo=Categoria.DESPESA, pai_nome='Gastos')
        self.receita = criar_categoria_filha('Salario', tipo=Categoria.RECEITA, pai_nome='Ganhos')

    def _criar_preview(self):
        session = self.client.session
        session['importacao_preview'] = {
            'conta_id': self.conta.id,
            'conta_nome': str(self.conta),
            'origem': Importacao.OFX,
            'arquivo_nome': 'extrato.ofx',
            'arquivo_conteudo': '',
            'lancamentos': [
                {
                    'id': 1,
                    'data': '2026-05-01',
                    'descricao': 'Mercado',
                    'valor': '-120.00',
                    'tipo': Lancamento.DEBITO,
                    'identificador': 'negativo',
                },
                {
                    'id': 2,
                    'data': '2026-05-02',
                    'descricao': 'Pagamento',
                    'valor': '500.00',
                    'tipo': Lancamento.CREDITO,
                    'identificador': 'positivo',
                },
            ],
        }
        session.save()

    def test_preview_filtra_categorias_por_sinal_do_valor(self):
        self._criar_preview()

        resposta = self.client.get(reverse('categorizar_ofx_preview'))

        lancamentos = resposta.context['lancamentos']
        self.assertEqual(list(lancamentos[0].categorias), [self.despesa])
        self.assertEqual(list(lancamentos[1].categorias), [self.receita])
        self.assertContains(resposta, '>Alimentacao</option>')
        self.assertContains(resposta, '>Salario</option>')
        self.assertNotContains(resposta, 'Gastos / Alimentacao')
        self.assertNotContains(resposta, 'Ganhos / Salario')

    def test_preview_rejeita_categoria_incompativel_com_sinal_do_valor(self):
        self._criar_preview()

        resposta = self.client.post(
            reverse('categorizar_ofx_preview'),
            {
                'alloc-1-categoria': str(self.receita.id),
                'alloc-1-percentual': '100',
                'alloc-2-categoria': '',
                'alloc-2-percentual': '100',
            },
        )

        self.assertRedirects(resposta, reverse('categorizar_ofx_preview'))
        self.assertFalse(Lancamento.objects.exists())

    def test_preview_aceita_rateio_por_valor_e_calcula_percentual(self):
        self._criar_preview()
        outra_despesa = Categoria.objects.create(
            nome='Transporte',
            tipo=Categoria.DESPESA,
            pai=self.despesa.pai,
        )

        resposta = self.client.post(
            reverse('categorizar_ofx_preview'),
            {
                'alloc-1-categoria': [str(self.despesa.id), str(outra_despesa.id)],
                'alloc-1-valor': ['100,00', '20,00'],
                'alloc-1-percentual': ['', ''],
                'alloc-2-categoria': '',
                'alloc-2-percentual': '100',
            },
        )

        self.assertRedirects(resposta, reverse('index'))
        lancamento = Lancamento.objects.get(descricao='Mercado')
        percentuais = list(lancamento.rateios.order_by('categoria__nome').values_list('percentual', flat=True))
        self.assertEqual(percentuais, [Decimal('83.33'), Decimal('16.67')])


class RecorrenciaContaTests(TestCase):
    def setUp(self):
        User.objects.create_user(username='usuario', password='senha-segura')
        self.client.login(username='usuario', password='senha-segura')

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
    def setUp(self):
        User.objects.create_user(username='usuario', password='senha-segura')
        self.client.login(username='usuario', password='senha-segura')

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
