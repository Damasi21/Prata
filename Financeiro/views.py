import base64
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .cartao import parse_cartao_csv
from .forms import CategoriaForm, ContaBancariaForm, ContaPagarReceberForm, EventoForm, ImportacaoCartaoForm, ImportacaoOFXForm, UsuarioCadastroForm
from .models import Budget, Categoria, ContaBancaria, ContaPagarReceber, Evento, Importacao, Lancamento, RateioLancamento, RecorrenciaConta
from .ofx import parse_ofx


class EmptyRateios:
    def all(self):
        return []


class PreviewRateios:
    def __init__(self, rateios):
        self._rateios = rateios

    def all(self):
        return self._rateios


def _grupos_categorias():
    pais = list(
        Categoria.objects.filter(pai__isnull=True)
        .prefetch_related('filhas')
        .order_by('tipo', 'nome')
    )
    return [{'pai': pai, 'filhas': list(pai.filhas.all().order_by('nome'))} for pai in pais]


def _mes_atual():
    hoje = date.today()
    return hoje.replace(day=1)


MESES_DO_ANO = [
    (1, 'janeiro'),
    (2, 'fevereiro'),
    (3, 'marco'),
    (4, 'abril'),
    (5, 'maio'),
    (6, 'junho'),
    (7, 'julho'),
    (8, 'agosto'),
    (9, 'setembro'),
    (10, 'outubro'),
    (11, 'novembro'),
    (12, 'dezembro'),
]


def _mes_budget(request):
    mes_numero = request.POST.get('mes_numero') if request.method == 'POST' else request.GET.get('mes_numero')
    ano = request.POST.get('ano') if request.method == 'POST' else request.GET.get('ano')
    if mes_numero and ano:
        try:
            return date(int(ano), int(mes_numero), 1)
        except ValueError:
            return _mes_atual()

    mes_str = request.POST.get('mes') if request.method == 'POST' else request.GET.get('mes')
    if mes_str:
        try:
            return date.fromisoformat(mes_str).replace(day=1)
        except ValueError:
            return _mes_atual()

    return _mes_atual()


def _data_filtro(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _somar_meses(data_base, meses):
    mes_indice = data_base.month - 1 + meses
    ano = data_base.year + mes_indice // 12
    mes = mes_indice % 12 + 1
    dia = min(data_base.day, monthrange(ano, mes)[1])
    return data_base.replace(year=ano, month=mes, day=dia)


def _datas_recorrencia(data_inicio, frequencia, quantidade):
    for indice in range(quantidade):
        if frequencia == RecorrenciaConta.SEMANAL:
            yield data_inicio + timedelta(weeks=indice)
        else:
            yield _somar_meses(data_inicio, indice)


def _natureza_lancamento(valor):
    return Categoria.RECEITA if valor > 0 else Categoria.DESPESA


def _categorias_operacionais():
    return Categoria.objects.filter(ativa=True, pai__isnull=False).select_related('pai')


def _categorias_por_natureza(categorias):
    categorias = list(categorias)
    return {
        Categoria.DESPESA: [categoria for categoria in categorias if categoria.tipo == Categoria.DESPESA],
        Categoria.RECEITA: [categoria for categoria in categorias if categoria.tipo == Categoria.RECEITA],
    }


def _categorias_ids_por_natureza(categorias_por_natureza):
    return {
        natureza: {categoria.id for categoria in categorias}
        for natureza, categorias in categorias_por_natureza.items()
    }


def _usuario_pode_gerenciar_usuarios(user):
    return user.is_authenticated and user.is_staff


def _motivo_bloqueio_exclusao_categoria(categoria):
    if categoria.rateios.exists():
        return 'Esta categoria esta atrelada a lancamentos e nao pode ser excluida. Voce pode edita-la ou inativa-la.'
    if categoria.contas_pagar_receber.exists():
        return 'Esta categoria esta atrelada a contas a pagar/receber e nao pode ser excluida. Voce pode edita-la ou inativa-la.'
    if categoria.filhas.exists():
        return 'Esta conta pai possui contas filho. Exclua ou mova as contas filho antes.'
    if categoria.budgets.exists():
        return 'Esta categoria possui budgets cadastrados e nao pode ser excluida. Voce pode edita-la ou inativa-la.'
    return None


def _sugestoes_conciliacao(lancamentos):
    sugestoes = {}
    for lancamento in lancamentos:
        valor = abs(lancamento.valor)
        tipo_categoria = _natureza_lancamento(lancamento.valor)
        contas = ContaPagarReceber.objects.filter(
            status=ContaPagarReceber.ABERTO,
            lancamento_conciliado__isnull=True,
            vencimento=lancamento.data,
            valor=valor,
            categoria__tipo=tipo_categoria,
        ).select_related('categoria', 'evento')
        if contas:
            sugestoes[lancamento.id] = contas
    return sugestoes


def _sugestoes_rateio_por_descricao(descricoes, categorias_validas):
    sugestoes = {}
    lancamentos = (
        Lancamento.objects.filter(descricao__in=descricoes, rateios__isnull=False)
        .prefetch_related('rateios__categoria')
        .order_by('descricao', '-data', '-id')
        .distinct()
    )
    for lancamento in lancamentos:
        if lancamento.descricao in sugestoes:
            continue
        rateios = [
            SimpleNamespace(categoria_id=rateio.categoria_id, percentual=rateio.percentual)
            for rateio in lancamento.rateios.all()
            if rateio.categoria_id in categorias_validas and rateio.categoria.ativa
        ]
        if rateios:
            sugestoes[lancamento.descricao] = rateios
    return sugestoes


def _decimal_do_post(valor, nome_campo, descricao):
    valor_normalizado = valor.strip()
    if ',' in valor_normalizado:
        valor_normalizado = valor_normalizado.replace('.', '').replace(',', '.')
    try:
        return Decimal(valor_normalizado)
    except InvalidOperation:
        raise ValueError(f'O {nome_campo} do lancamento "{descricao}" nao e valido.')


def _rateios_do_post(request, lancamento_id, descricao, categorias_validas, valor_lancamento, obrigatorio=True):
    categorias_ids = request.POST.getlist(f'alloc-{lancamento_id}-categoria')
    percentuais = request.POST.getlist(f'alloc-{lancamento_id}-percentual')
    valores = request.POST.getlist(f'alloc-{lancamento_id}-valor')
    novos_rateios = []
    total = Decimal('0')

    for indice, categoria_id in enumerate(categorias_ids):
        percentual = percentuais[indice] if indice < len(percentuais) else ''
        valor_rateio = valores[indice] if indice < len(valores) else ''
        if not categoria_id:
            continue
        if not categoria_id.isdigit() or int(categoria_id) not in categorias_validas:
            raise ValueError(f'A categoria selecionada para "{descricao}" nao esta ativa.')
        if percentual:
            percentual_decimal = _decimal_do_post(percentual, 'percentual', descricao)
        elif valor_rateio:
            valor_decimal = _decimal_do_post(valor_rateio, 'valor', descricao)
            if valor_lancamento <= 0:
                raise ValueError(f'O valor do lancamento "{descricao}" precisa ser maior que zero para calcular o rateio.')
            percentual_decimal = (valor_decimal / valor_lancamento * Decimal('100')).quantize(Decimal('0.01'))
        else:
            raise ValueError(f'O lancamento "{descricao}" precisa ter categoria e percentual ou valor preenchidos.')
        if percentual_decimal <= 0 or percentual_decimal > 100:
            raise ValueError(f'O percentual do lancamento "{descricao}" deve ser maior que 0 e menor ou igual a 100.')
        total += percentual_decimal
        novos_rateios.append((categoria_id, percentual_decimal))

    if not novos_rateios and obrigatorio:
        raise ValueError(f'O lancamento "{descricao}" precisa ter uma categoria.')
    if not novos_rateios:
        return []
    if total != Decimal('100'):
        raise ValueError(f'O lancamento "{descricao}" precisa fechar 100% de rateio.')

    return novos_rateios


def _criar_preview_importacao(request, conta, arquivo, lancamentos, origem):
    arquivo.seek(0)
    request.session['importacao_preview'] = {
        'conta_id': conta.id,
        'conta_nome': str(conta),
        'origem': origem,
        'arquivo_nome': arquivo.name,
        'arquivo_conteudo': base64.b64encode(arquivo.read()).decode('ascii'),
        'lancamentos': [
            {
                'id': index,
                'data': item.data.isoformat(),
                'descricao': item.descricao,
                'valor': str(item.valor),
                'tipo': item.tipo,
                'identificador': item.identificador,
            }
            for index, item in enumerate(lancamentos, start=1)
        ],
    }
    request.session.pop('ofx_preview', None)


@require_http_methods(['GET', 'POST'])
def tela_login(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        next_url = request.POST.get('next') or reverse('index')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            if not request.POST.get('remember'):
                request.session.set_expiry(0)
            return redirect(next_url)

        usuario_pendente = User.objects.filter(username=username, is_active=False).first()
        if usuario_pendente and usuario_pendente.check_password(password):
            messages.info(request, 'Seu cadastro esta aguardando aprovacao do administrador.')
            return redirect('tela_login')

        messages.error(request, 'Usuario ou senha invalidos.')

    return render(
        request,
        'financeiro/login.html',
        {'pode_criar_primeiro_usuario': not User.objects.exists()},
    )


@require_http_methods(['GET', 'POST'])
def cadastro_usuario(request):
    primeiro_usuario = not User.objects.exists()

    form = UsuarioCadastroForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.save(commit=False)
        if primeiro_usuario:
            usuario.is_staff = True
            usuario.is_superuser = True
        else:
            usuario.is_active = False
        usuario.save()
        if primeiro_usuario:
            messages.success(request, 'Usuario administrador cadastrado com sucesso.')
            login(request, usuario)
            return redirect('index')

        messages.success(request, 'Cadastro enviado com sucesso. O administrador ira avaliar sua liberacao.')
        return redirect('tela_login')

    return render(
        request,
        'financeiro/cadastro_usuario.html',
        {'form': form, 'primeiro_usuario': primeiro_usuario},
    )


@require_http_methods(['POST'])
def sair(request):
    logout(request)
    return redirect('tela_login')


@require_http_methods(['GET', 'POST'])
@login_required
@user_passes_test(_usuario_pode_gerenciar_usuarios, login_url='index')
def usuarios(request):
    if request.method == 'POST':
        usuario = get_object_or_404(User, id=request.POST.get('usuario_id'))
        acao = request.POST.get('acao')

        if acao == 'aprovar':
            usuario.is_active = True
            usuario.save(update_fields=['is_active'])
            messages.success(request, f'Usuario "{usuario.username}" aprovado com sucesso.')
        elif acao == 'reprovar':
            if usuario.is_active:
                messages.error(request, 'Apenas usuarios pendentes podem ser reprovados.')
            else:
                username = usuario.username
                usuario.delete()
                messages.success(request, f'Cadastro de "{username}" reprovado.')
        elif acao == 'bloquear':
            if usuario == request.user:
                messages.error(request, 'Voce nao pode bloquear o seu proprio usuario.')
            else:
                usuario.is_active = False
                usuario.save(update_fields=['is_active'])
                messages.success(request, f'Usuario "{usuario.username}" bloqueado com sucesso.')
        else:
            messages.error(request, 'Acao invalida.')

        return redirect('usuarios')

    lista_usuarios = User.objects.order_by('is_active', 'username')
    return render(
        request,
        'financeiro/usuarios.html',
        {
            'usuarios': lista_usuarios,
            'pendentes': lista_usuarios.filter(is_active=False).count(),
        },
    )


@login_required
def index(request):
    mes = _mes_atual()
    budgets = Budget.objects.filter(mes=mes).select_related('categoria')
    gastos = {}
    rateios = RateioLancamento.objects.filter(
        lancamento__data__year=mes.year,
        lancamento__data__month=mes.month,
        categoria__tipo=Categoria.DESPESA,
    ).select_related('lancamento', 'categoria')
    for rateio in rateios:
        gastos[rateio.categoria_id] = gastos.get(rateio.categoria_id, Decimal('0')) + abs(rateio.valor_rateado)
    alertas = [
        {'categoria': budget.categoria, 'budget': budget.valor, 'gasto': gastos.get(budget.categoria_id, Decimal('0'))}
        for budget in budgets
        if gastos.get(budget.categoria_id, Decimal('0')) > budget.valor
    ]
    context = {
        'total_contas': ContaBancaria.objects.count(),
        'total_categorias': Categoria.objects.count(),
        'lancamentos_sem_rateio': Lancamento.objects.filter(rateios__isnull=True).count(),
        'alertas_budget': alertas,
    }
    return render(request, 'index.html', context)


@require_http_methods(['GET', 'POST'])
@login_required
def contas_bancarias(request):
    conta_em_edicao = None
    conta_id = request.POST.get('conta_id') or request.GET.get('editar')
    if conta_id:
        conta_em_edicao = get_object_or_404(ContaBancaria, id=conta_id)

    form = ContaBancariaForm(request.POST or None, instance=conta_em_edicao)
    if request.method == 'POST' and conta_em_edicao and request.POST.get('acao') == 'excluir':
        if conta_em_edicao.lancamentos.exists() or conta_em_edicao.importacoes.exists():
            messages.error(request, 'Esta conta bancaria possui lancamentos/importacoes e nao pode ser excluida.')
            return redirect('contas_bancarias')
        nome = str(conta_em_edicao)
        try:
            conta_em_edicao.delete()
        except ProtectedError:
            messages.error(request, 'Esta conta bancaria possui vinculos e nao pode ser excluida.')
        else:
            messages.success(request, f'Conta bancaria "{nome}" excluida com sucesso.')
        return redirect('contas_bancarias')

    if request.method == 'POST' and form.is_valid():
        form.save()
        if conta_em_edicao:
            messages.success(request, 'Conta bancaria atualizada com sucesso.')
        else:
            messages.success(request, 'Conta bancaria cadastrada com sucesso.')
        return redirect('contas_bancarias')
    return render(
        request,
        'financeiro/contas_bancarias.html',
        {'form': form, 'contas': ContaBancaria.objects.all(), 'conta_em_edicao': conta_em_edicao},
    )


@require_http_methods(['GET', 'POST'])
@login_required
def categorias(request):
    categoria_em_edicao = None
    categoria_id = request.POST.get('categoria_id') or request.GET.get('editar')
    if categoria_id:
        categoria_em_edicao = get_object_or_404(Categoria, id=categoria_id)

    form = CategoriaForm(request.POST or None, instance=categoria_em_edicao)
    if request.method == 'POST' and categoria_em_edicao and request.POST.get('acao') == 'excluir':
        motivo = _motivo_bloqueio_exclusao_categoria(categoria_em_edicao)
        if motivo:
            messages.error(request, motivo)
            return redirect('categorias')
        nome = str(categoria_em_edicao)
        try:
            categoria_em_edicao.delete()
        except ProtectedError:
            messages.error(request, 'Esta categoria possui vinculos e nao pode ser excluida. Voce pode edita-la ou inativa-la.')
        else:
            messages.success(request, f'Categoria "{nome}" excluida com sucesso.')
        return redirect('categorias')

    if request.method == 'POST' and form.is_valid():
        form.save()
        if categoria_em_edicao:
            messages.success(request, 'Categoria atualizada com sucesso.')
        else:
            messages.success(request, 'Categoria cadastrada com sucesso.')
        return redirect('categorias')

    categorias_grupos = _grupos_categorias()
    return render(
        request,
        'financeiro/categorias.html',
        {'form': form, 'categorias_grupos': categorias_grupos, 'categoria_em_edicao': categoria_em_edicao},
    )


@require_http_methods(['GET', 'POST'])
@login_required
def eventos(request):
    form = EventoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Evento cadastrado com sucesso.')
        return redirect('eventos')
    return render(request, 'financeiro/eventos.html', {'form': form, 'eventos': Evento.objects.all()})


@require_http_methods(['GET', 'POST'])
@login_required
def importar_ofx(request):
    form = ImportacaoOFXForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        conta = form.cleaned_data['conta']
        arquivo = form.cleaned_data['arquivo']
        lancamentos_ofx = parse_ofx(arquivo)
        _criar_preview_importacao(request, conta, arquivo, lancamentos_ofx, Importacao.OFX)
        messages.info(request, 'OFX lido com sucesso. Revise os lancamentos e clique em Salvar para gravar no banco.')
        return redirect('categorizar_ofx_preview')

    data_inicio = _data_filtro(request.GET.get('data_inicio'))
    data_fim = _data_filtro(request.GET.get('data_fim'))
    contas_ativas = ContaBancaria.objects.filter(ativa=True)
    conta_filtro_id = request.GET.get('conta') or ''
    conta_filtro = None
    if conta_filtro_id:
        conta_filtro = get_object_or_404(ContaBancaria, id=conta_filtro_id, ativa=True)

    lancamentos = Lancamento.objects.select_related('conta', 'importacao', 'evento').prefetch_related(
        'rateios__categoria'
    )
    if conta_filtro:
        lancamentos = lancamentos.filter(conta=conta_filtro).order_by('data', 'id')
    if data_inicio:
        lancamentos = lancamentos.filter(data__gte=data_inicio)
    if data_fim:
        lancamentos = lancamentos.filter(data__lte=data_fim)

    extrato_linhas = []
    saldo_atual = None
    if conta_filtro:
        saldo_atual = conta_filtro.saldo_inicial
        extrato_linhas.append(
            SimpleNamespace(
                tipo='saldo_inicial',
                data=conta_filtro.data_saldo_inicial,
                descricao='Saldo inicial',
                conta=conta_filtro,
                valor=conta_filtro.saldo_inicial,
                saldo=saldo_atual,
            )
        )
        for lancamento in lancamentos:
            saldo_atual += lancamento.valor
            extrato_linhas.append(
                SimpleNamespace(
                    tipo='lancamento',
                    lancamento=lancamento,
                    data=lancamento.data,
                    descricao=lancamento.descricao,
                    conta=lancamento.conta,
                    valor=lancamento.valor,
                    saldo=saldo_atual,
                )
            )

    contas_saldos = {
        str(conta.id): {
            'saldo': str(conta.saldo_inicial),
            'data': conta.data_saldo_inicial.strftime('%d/%m/%Y') if conta.data_saldo_inicial else '',
        }
        for conta in contas_ativas
    }
    context = {
        'form': form,
        'lancamentos': lancamentos,
        'extrato_linhas': extrato_linhas,
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        'contas_ativas': contas_ativas,
        'conta_filtro': conta_filtro,
        'conta_filtro_id': conta_filtro_id,
        'saldo_final': saldo_atual,
        'contas_saldos': contas_saldos,
    }
    return render(request, 'financeiro/importar_ofx.html', context)


@require_http_methods(['POST'])
@login_required
def excluir_lancamento(request, lancamento_id):
    lancamento = get_object_or_404(Lancamento, id=lancamento_id)
    descricao = lancamento.descricao
    importacao = lancamento.importacao
    lancamento.contas_conciliadas.update(status=ContaPagarReceber.ABERTO)
    lancamento.delete()
    if importacao:
        importacao.total_lancamentos = importacao.lancamentos.count()
        importacao.save(update_fields=['total_lancamentos'])
    messages.success(request, f'Lancamento "{descricao}" excluido com sucesso.')

    proxima_url = request.POST.get('next') or 'importar_ofx'
    return redirect(proxima_url)


@require_http_methods(['GET', 'POST'])
@login_required
def categorizar_ofx_preview(request):
    preview = request.session.get('importacao_preview') or request.session.get('ofx_preview')
    if not preview:
        messages.info(request, 'Importe um arquivo antes de categorizar.')
        return redirect('importar_ofx')

    categorias_por_natureza = _categorias_por_natureza(_categorias_operacionais())
    categorias_ids_por_natureza = _categorias_ids_por_natureza(categorias_por_natureza)
    categorias_ativas_ids = set().union(*categorias_ids_por_natureza.values())
    sugestoes_rateio = _sugestoes_rateio_por_descricao(
        [item['descricao'] for item in preview['lancamentos']],
        categorias_ativas_ids,
    )
    lancamentos = []
    for item in preview['lancamentos']:
        valor = Decimal(item['valor'])
        natureza = _natureza_lancamento(valor)
        categorias_do_lancamento = categorias_por_natureza[natureza]
        categorias_ids_do_lancamento = categorias_ids_por_natureza[natureza]
        rateios_sugeridos = [
            rateio
            for rateio in sugestoes_rateio.get(item['descricao'], [])
            if rateio.categoria_id in categorias_ids_do_lancamento
        ]
        lancamentos.append(
            SimpleNamespace(
                id=item['id'],
                data=date.fromisoformat(item['data']),
                descricao=item['descricao'],
                valor=valor,
                tipo=item['tipo'],
                identificador_externo=item['identificador'],
                valor_absoluto=abs(valor),
                evento_id=None,
                categorias=categorias_do_lancamento,
                rateios=PreviewRateios(rateios_sugeridos) if rateios_sugeridos else EmptyRateios(),
                tem_sugestao_categoria=bool(rateios_sugeridos),
            )
        )
    importacao_preview = SimpleNamespace(
        conta=preview['conta_nome'],
        origem=preview.get('origem', Importacao.OFX),
        total_lancamentos=len(lancamentos),
        salva=False,
    )
    conciliacoes_sugeridas = _sugestoes_conciliacao(lancamentos)
    identificadores_existentes = set(
        Lancamento.objects.filter(
            conta_id=preview['conta_id'],
            identificador_externo__in=[
                item['identificador']
                for item in preview['lancamentos']
                if item['identificador']
            ],
        ).values_list('identificador_externo', flat=True)
    )
    for lancamento in lancamentos:
        lancamento.sugestoes_conciliacao = conciliacoes_sugeridas.get(lancamento.id, [])
        lancamento.ja_gravado = (
            bool(lancamento.identificador_externo)
            and lancamento.identificador_externo in identificadores_existentes
        )

    if request.method == 'POST':
        eventos_ativos = set(Evento.objects.filter(ativa=True).values_list('id', flat=True))
        contas_sugeridas_ids = {
            conta_sugerida.id
            for contas_sugeridas in conciliacoes_sugeridas.values()
            for conta_sugerida in contas_sugeridas
        }
        rateios_por_lancamento = {}
        eventos_por_lancamento = {}
        conciliacoes_por_lancamento = {}
        contas_conciliadas_no_post = set()

        for item in preview['lancamentos']:
            lancamento_id = item['id']
            evento_id = request.POST.get(f'evento-{lancamento_id}') or None
            if evento_id and (not evento_id.isdigit() or int(evento_id) not in eventos_ativos):
                messages.error(request, f'O evento selecionado para "{item["descricao"]}" nao esta ativo.')
                return redirect('categorizar_ofx_preview')
            try:
                novos_rateios = _rateios_do_post(
                    request,
                    lancamento_id,
                    item['descricao'],
                    categorias_ids_por_natureza[_natureza_lancamento(Decimal(item['valor']))],
                    abs(Decimal(item['valor'])),
                    obrigatorio=False,
                )
            except ValueError as erro:
                messages.error(request, str(erro))
                return redirect('categorizar_ofx_preview')
            if not novos_rateios:
                continue
            rateios_por_lancamento[lancamento_id] = novos_rateios

            conta_conciliacao_id = request.POST.get(f'conciliar-{lancamento_id}') or None
            if conta_conciliacao_id:
                contas_do_lancamento = {conta_sugerida.id for conta_sugerida in conciliacoes_sugeridas.get(lancamento_id, [])}
                if (
                    not conta_conciliacao_id.isdigit()
                    or int(conta_conciliacao_id) not in contas_sugeridas_ids
                    or int(conta_conciliacao_id) not in contas_do_lancamento
                    or int(conta_conciliacao_id) in contas_conciliadas_no_post
                ):
                    messages.error(request, f'A conciliacao selecionada para "{item["descricao"]}" nao e valida.')
                    return redirect('categorizar_ofx_preview')
                contas_conciliadas_no_post.add(int(conta_conciliacao_id))

            eventos_por_lancamento[lancamento_id] = evento_id
            conciliacoes_por_lancamento[lancamento_id] = conta_conciliacao_id

        if not rateios_por_lancamento:
            messages.error(request, 'Selecione categoria em pelo menos um lancamento antes de salvar.')
            return redirect('categorizar_ofx_preview')

        conta = get_object_or_404(ContaBancaria, id=preview['conta_id'])
        arquivo = ContentFile(base64.b64decode(preview['arquivo_conteudo']), name=preview['arquivo_nome'])

        criados = 0
        ignorados = 0
        with transaction.atomic():
            importacao = Importacao.objects.create(
                conta=conta,
                origem=preview.get('origem', Importacao.OFX),
                arquivo=arquivo,
            )
            for item in preview['lancamentos']:
                lancamento_id = item['id']
                if lancamento_id not in rateios_por_lancamento:
                    continue
                defaults = {
                    'importacao': importacao,
                    'data': date.fromisoformat(item['data']),
                    'descricao': item['descricao'],
                    'valor': Decimal(item['valor']),
                    'tipo': item['tipo'],
                    'identificador_externo': item['identificador'],
                    'evento_id': eventos_por_lancamento[lancamento_id],
                }
                if item['identificador']:
                    lancamento, created = Lancamento.objects.get_or_create(
                        conta=conta,
                        identificador_externo=item['identificador'],
                        defaults=defaults,
                    )
                else:
                    lancamento = Lancamento.objects.create(conta=conta, **defaults)
                    created = True

                criados += int(created)
                ignorados += int(not created)
                if created:
                    for categoria_id, percentual in rateios_por_lancamento[lancamento_id]:
                        RateioLancamento.objects.create(
                            lancamento=lancamento,
                            categoria_id=categoria_id,
                            percentual=percentual,
                        )

                conta_conciliacao_id = conciliacoes_por_lancamento[lancamento_id]
                if conta_conciliacao_id:
                    ContaPagarReceber.objects.filter(
                        id=conta_conciliacao_id,
                        status=ContaPagarReceber.ABERTO,
                        lancamento_conciliado__isnull=True,
                    ).update(
                        status=ContaPagarReceber.PAGO,
                        lancamento_conciliado_id=lancamento.id,
                    )

            importacao.total_lancamentos = criados
            importacao.save(update_fields=['total_lancamentos'])
        request.session.pop('importacao_preview', None)
        request.session.pop('ofx_preview', None)
        messages.success(request, f'Importacao salva: {criados} lancamentos criados, {ignorados} duplicados ignorados.')
        return redirect('index')

    return render(
        request,
        'financeiro/categorizar_importacao.html',
        {
            'importacao': importacao_preview,
            'lancamentos': lancamentos,
            'eventos': Evento.objects.filter(ativa=True),
            'conciliacoes_sugeridas': conciliacoes_sugeridas,
            'preview': True,
        },
    )


@require_http_methods(['GET', 'POST'])
@login_required
def categorizar_importacao(request, importacao_id):
    importacao = get_object_or_404(Importacao, id=importacao_id)
    lancamentos = importacao.lancamentos.prefetch_related('rateios__categoria').all()
    categorias_por_natureza = _categorias_por_natureza(_categorias_operacionais())
    categorias_ids_por_natureza = _categorias_ids_por_natureza(categorias_por_natureza)
    for lancamento in lancamentos:
        lancamento.categorias = categorias_por_natureza[_natureza_lancamento(lancamento.valor)]
    eventos_ativos = Evento.objects.filter(ativa=True)
    eventos_ativos_ids = set(eventos_ativos.values_list('id', flat=True))

    if request.method == 'POST':
        rateios_por_lancamento = {}
        eventos_por_lancamento = {}
        for lancamento in lancamentos:
            evento_id = request.POST.get(f'evento-{lancamento.id}') or None
            if evento_id and (not evento_id.isdigit() or int(evento_id) not in eventos_ativos_ids):
                messages.error(request, f'O evento selecionado para "{lancamento.descricao}" nao esta ativo.')
                return redirect('categorizar_importacao', importacao_id=importacao.id)
            try:
                rateios_por_lancamento[lancamento.id] = _rateios_do_post(
                    request,
                    lancamento.id,
                    lancamento.descricao,
                    categorias_ids_por_natureza[_natureza_lancamento(lancamento.valor)],
                    lancamento.valor_absoluto,
                )
            except ValueError as erro:
                messages.error(request, str(erro))
                return redirect('categorizar_importacao', importacao_id=importacao.id)
            eventos_por_lancamento[lancamento.id] = evento_id

        with transaction.atomic():
            for lancamento in lancamentos:
                lancamento.evento_id = eventos_por_lancamento[lancamento.id]
                lancamento.save(update_fields=['evento'])
                lancamento.rateios.all().delete()
                for categoria_id, percentual in rateios_por_lancamento[lancamento.id]:
                    RateioLancamento.objects.create(
                        lancamento=lancamento,
                        categoria_id=categoria_id,
                        percentual=percentual,
                    )

        messages.success(request, 'Categorias e rateios salvos com sucesso.')
        return redirect('index')

    return render(
        request,
        'financeiro/categorizar_importacao.html',
        {'importacao': importacao, 'lancamentos': lancamentos, 'eventos': eventos_ativos},
    )


@require_http_methods(['GET', 'POST'])
@login_required
def budgets(request):
    mes = _mes_budget(request)
    categorias = _categorias_operacionais().order_by('tipo', 'pai__nome', 'nome')

    if request.method == 'POST':
        valores_por_categoria = {}
        for categoria in categorias:
            valor = request.POST.get(f'budget-{categoria.id}')
            if valor:
                valores_por_categoria[categoria.id] = Decimal(valor.replace(',', '.'))

        meses_destino = [mes]
        if request.POST.get('acao') == 'copiar_subsequentes':
            meses_destino.extend(date(mes.year, mes_numero, 1) for mes_numero in range(mes.month + 1, 13))

        with transaction.atomic():
            for mes_destino in meses_destino:
                for categoria in categorias:
                    if categoria.id not in valores_por_categoria:
                        continue
                    Budget.objects.update_or_create(
                        categoria=categoria,
                        mes=mes_destino,
                        defaults={'valor': valores_por_categoria[categoria.id]},
                    )

        if request.POST.get('acao') == 'copiar_subsequentes':
            total_meses_copiados = max(0, 12 - mes.month)
            messages.success(request, f'Budgets copiados para {total_meses_copiados} meses subsequentes.')
        else:
            messages.success(request, 'Budgets salvos com sucesso.')
        return redirect(f'{reverse("budgets")}?mes_numero={mes.month}&ano={mes.year}')

    budgets_existentes = {
        budget.categoria_id: budget.valor
        for budget in Budget.objects.filter(mes=mes, categoria__in=categorias)
    }
    realizados = {}
    rateios_realizados = RateioLancamento.objects.filter(
        lancamento__data__year=mes.year,
        lancamento__data__month=mes.month,
        categoria__in=categorias,
    ).select_related('lancamento', 'categoria')
    for rateio in rateios_realizados:
        realizados[rateio.categoria_id] = realizados.get(rateio.categoria_id, Decimal('0')) + abs(rateio.valor_rateado)

    linhas = []
    for categoria in categorias:
        valor_budget = budgets_existentes.get(categoria.id, '')
        valor_realizado = realizados.get(categoria.id, Decimal('0'))
        linhas.append(
            {
                'categoria': categoria,
                'valor': valor_budget,
                'realizado': valor_realizado,
                'estourado': bool(valor_budget != '' and valor_realizado > valor_budget),
            }
        )
    linhas_despesas = [linha for linha in linhas if linha['categoria'].tipo == Categoria.DESPESA]
    linhas_receitas = [linha for linha in linhas if linha['categoria'].tipo == Categoria.RECEITA]
    total_despesas = sum((linha['valor'] or Decimal('0')) for linha in linhas_despesas)
    total_receitas = sum((linha['valor'] or Decimal('0')) for linha in linhas_receitas)
    total_realizado_despesas = sum((linha['realizado'] or Decimal('0')) for linha in linhas_despesas)
    total_realizado_receitas = sum((linha['realizado'] or Decimal('0')) for linha in linhas_receitas)
    return render(
        request,
        'financeiro/budgets.html',
        {
            'mes': mes,
            'meses_do_ano': MESES_DO_ANO,
            'ano': mes.year,
            'mes_numero': mes.month,
            'linhas_despesas': linhas_despesas,
            'linhas_receitas': linhas_receitas,
            'total_despesas': total_despesas,
            'total_receitas': total_receitas,
            'total_realizado_despesas': total_realizado_despesas,
            'total_realizado_receitas': total_realizado_receitas,
            'diferenca_budget': total_receitas - total_despesas,
        },
    )


@require_http_methods(['GET', 'POST'])
@login_required
def contas_pagar_receber(request):
    conta_em_edicao = None
    conta_id = request.POST.get('conta_id') or request.GET.get('editar')
    if conta_id:
        conta_em_edicao = get_object_or_404(ContaPagarReceber, id=conta_id)

    form = ContaPagarReceberForm(request.POST or None, instance=conta_em_edicao)
    if request.method == 'POST' and conta_em_edicao and request.POST.get('acao') == 'excluir':
        descricao = conta_em_edicao.descricao
        recorrencia = conta_em_edicao.recorrencia
        if recorrencia and request.POST.get('excluir_recorrencia') == '1':
            with transaction.atomic():
                total_excluido = recorrencia.contas.count()
                recorrencia.contas.all().delete()
                recorrencia.delete()
            messages.success(
                request,
                f'Recorrencia "{descricao}" excluida com sucesso: {total_excluido} contas removidas.',
            )
        else:
            conta_em_edicao.delete()
            messages.success(request, f'Conta "{descricao}" excluida com sucesso.')
        return redirect('contas_pagar_receber')

    if request.method == 'POST' and form.is_valid():
        conta_base = form.save(commit=False)
        if conta_em_edicao:
            conta_base.save()
            messages.success(request, 'Conta atualizada com sucesso.')
        elif form.cleaned_data['criar_recorrencia']:
            quantidade = form.cleaned_data['quantidade_recorrencia']
            frequencia = form.cleaned_data['frequencia_recorrencia']
            with transaction.atomic():
                recorrencia = RecorrenciaConta.objects.create(
                    descricao=conta_base.descricao,
                    frequencia=frequencia,
                    quantidade=quantidade,
                    data_inicio=conta_base.vencimento,
                )
                contas = [
                    ContaPagarReceber(
                        categoria=conta_base.categoria,
                        evento=conta_base.evento,
                        descricao=conta_base.descricao,
                        vencimento=vencimento,
                        valor=conta_base.valor,
                        status=conta_base.status,
                        recorrencia=recorrencia,
                    )
                    for vencimento in _datas_recorrencia(conta_base.vencimento, frequencia, quantidade)
                ]
                ContaPagarReceber.objects.bulk_create(contas)
            messages.success(request, f'Recorrencia criada com sucesso: {quantidade} contas geradas.')
        else:
            conta_base.save()
            messages.success(request, 'Conta cadastrada com sucesso.')
        return redirect('contas_pagar_receber')
    contas = ContaPagarReceber.objects.select_related('categoria', 'evento', 'lancamento_conciliado', 'recorrencia')
    return render(
        request,
        'financeiro/contas_pagar_receber.html',
        {'form': form, 'contas': contas, 'conta_em_edicao': conta_em_edicao},
    )


@require_http_methods(['GET', 'POST'])
@login_required
def importar_excel(request):
    form = ImportacaoCartaoForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        conta = form.cleaned_data['conta']
        arquivo = form.cleaned_data['arquivo']
        try:
            lancamentos_cartao = parse_cartao_csv(arquivo)
        except (ValueError, InvalidOperation) as erro:
            messages.error(request, f'Nao foi possivel ler o CSV: {erro}')
            return redirect('importar_excel')
        if not lancamentos_cartao:
            messages.error(request, 'Nenhum lancamento foi encontrado no CSV.')
            return redirect('importar_excel')
        _criar_preview_importacao(request, conta, arquivo, lancamentos_cartao, Importacao.EXCEL)
        messages.info(request, 'CSV lido com sucesso. Revise os lancamentos e clique em Salvar para gravar no banco.')
        return redirect('categorizar_ofx_preview')
    return render(request, 'financeiro/importar_excel.html', {'form': form})


@login_required
def fluxo_de_caixa(request):
    return render(request, 'financeiro/fluxo_de_caixa.html')


@login_required
def relatorios_bi(request):
    return render(request, 'financeiro/relatorios_bi.html')
