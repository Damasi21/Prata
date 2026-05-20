import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum


class TimeStampedModel(models.Model):
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ContaBancaria(TimeStampedModel):
    nome = models.CharField(max_length=120)
    banco = models.CharField(max_length=120)
    agencia = models.CharField(max_length=30, blank=True)
    numero = models.CharField(max_length=40, blank=True)
    saldo_inicial = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    data_saldo_inicial = models.DateField(null=True, blank=True)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ['banco', 'nome']
        verbose_name = 'conta bancaria'
        verbose_name_plural = 'contas bancarias'

    def __str__(self):
        return f'{self.banco} - {self.nome}'


class Categoria(TimeStampedModel):
    RECEITA = 'receita'
    DESPESA = 'despesa'
    TIPO_CHOICES = (
        (RECEITA, 'Receita'),
        (DESPESA, 'Despesa'),
    )

    nome = models.CharField(max_length=120, unique=True)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    pai = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        related_name='filhas',
        null=True,
        blank=True,
        verbose_name='conta pai',
    )
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ['tipo', 'pai__nome', 'nome']

    def __str__(self):
        if self.pai_id:
            return f'{self.pai.nome} / {self.nome}'
        return self.nome

    @property
    def eh_pai(self):
        return self.pai_id is None

    @property
    def eh_filha(self):
        return self.pai_id is not None

    def clean(self):
        if self.pai_id:
            if self.pai_id == self.id:
                raise ValidationError('A categoria nao pode ser pai dela mesma.')
            if self.pai.pai_id:
                raise ValidationError('Use apenas um nivel de hierarquia: pai e filho.')
            if self.pai.tipo != self.tipo:
                raise ValidationError('A conta pai deve ter o mesmo tipo da conta filho.')


class Evento(TimeStampedModel):
    nome = models.CharField(max_length=120, unique=True)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return self.nome


class Importacao(TimeStampedModel):
    OFX = 'ofx'
    EXCEL = 'excel'
    ORIGEM_CHOICES = (
        (OFX, 'OFX'),
        (EXCEL, 'Cartao de Credito'),
    )

    conta = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT, related_name='importacoes')
    origem = models.CharField(max_length=10, choices=ORIGEM_CHOICES)
    arquivo = models.FileField(upload_to='importacoes/%Y/%m/')
    total_lancamentos = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'importacao'
        verbose_name_plural = 'importacoes'

    def __str__(self):
        return f'{self.get_origem_display()} - {self.conta} - {self.criado_em:%d/%m/%Y}'


class Lancamento(TimeStampedModel):
    DEBITO = 'debito'
    CREDITO = 'credito'
    TIPO_CHOICES = (
        (DEBITO, 'Debito'),
        (CREDITO, 'Credito'),
    )

    conta = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT, related_name='lancamentos')
    evento = models.ForeignKey(
        Evento,
        on_delete=models.PROTECT,
        related_name='lancamentos',
        null=True,
        blank=True,
    )
    importacao = models.ForeignKey(
        Importacao,
        on_delete=models.SET_NULL,
        related_name='lancamentos',
        null=True,
        blank=True,
    )
    data = models.DateField()
    descricao = models.CharField(max_length=255)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    identificador_externo = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-data', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['conta', 'identificador_externo'],
                condition=~models.Q(identificador_externo=''),
                name='lancamento_unico_por_conta_e_identificador',
            )
        ]

    def __str__(self):
        return f'{self.data:%d/%m/%Y} - {self.descricao}'

    @property
    def valor_absoluto(self):
        return abs(self.valor)

    @property
    def percentual_rateado(self):
        total = self.rateios.aggregate(total=Sum('percentual'))['total'] or Decimal('0')
        return total

    @property
    def esta_rateado(self):
        return self.percentual_rateado == Decimal('100.00')


class RateioLancamento(TimeStampedModel):
    lancamento = models.ForeignKey(Lancamento, on_delete=models.CASCADE, related_name='rateios')
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name='rateios')
    percentual = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        ordering = ['lancamento', 'categoria']
        verbose_name = 'rateio de lancamento'
        verbose_name_plural = 'rateios de lancamentos'

    def clean(self):
        if self.percentual <= 0 or self.percentual > 100:
            raise ValidationError('O percentual deve ser maior que 0 e menor ou igual a 100.')

    @property
    def valor_rateado(self):
        return (self.lancamento.valor * self.percentual / Decimal('100')).quantize(Decimal('0.01'))

    def __str__(self):
        return f'{self.lancamento} - {self.categoria} ({self.percentual}%)'


class Budget(TimeStampedModel):
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE, related_name='budgets')
    mes = models.DateField(help_text='Use sempre o primeiro dia do mes.')
    valor = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['-mes', 'categoria__nome']
        constraints = [
            models.UniqueConstraint(fields=['categoria', 'mes'], name='budget_unico_por_categoria_mes')
        ]

    def __str__(self):
        return f'{self.categoria} - {self.mes:%m/%Y}'


class RecorrenciaConta(TimeStampedModel):
    SEMANAL = 'semanal'
    MENSAL = 'mensal'
    FREQUENCIA_CHOICES = (
        (SEMANAL, 'Semanal'),
        (MENSAL, 'Mensal'),
    )

    codigo = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    descricao = models.CharField(max_length=180)
    frequencia = models.CharField(max_length=10, choices=FREQUENCIA_CHOICES)
    quantidade = models.PositiveIntegerField()
    data_inicio = models.DateField()
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ['-data_inicio', 'descricao']
        verbose_name = 'recorrencia de conta'
        verbose_name_plural = 'recorrencias de contas'

    def __str__(self):
        return f'{self.descricao} - {self.get_frequencia_display()} ({self.quantidade}x)'


class ContaPagarReceber(TimeStampedModel):
    ABERTO = 'aberto'
    PAGO = 'pago'
    STATUS_CHOICES = (
        (ABERTO, 'Aberto'),
        (PAGO, 'Pago/Recebido'),
    )

    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name='contas_pagar_receber')
    evento = models.ForeignKey(
        Evento,
        on_delete=models.PROTECT,
        related_name='contas_pagar_receber',
        null=True,
        blank=True,
    )
    descricao = models.CharField(max_length=180)
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ABERTO)
    recorrencia = models.ForeignKey(
        RecorrenciaConta,
        on_delete=models.SET_NULL,
        related_name='contas',
        null=True,
        blank=True,
    )
    lancamento_conciliado = models.ForeignKey(
        Lancamento,
        on_delete=models.SET_NULL,
        related_name='contas_conciliadas',
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ['vencimento', 'descricao']
        verbose_name = 'conta a pagar/receber'
        verbose_name_plural = 'contas a pagar/receber'

    @property
    def natureza(self):
        return 'A receber' if self.categoria.tipo == Categoria.RECEITA else 'A pagar'

    @property
    def esta_conciliada(self):
        return self.lancamento_conciliado_id is not None

    def __str__(self):
        return f'{self.descricao} - {self.natureza}'
